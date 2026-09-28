"""
TikTok Content Posting API v2 integration.
Handles OAuth 2.0 authorization, token refresh, and video upload/scheduling.

Setup (one-time):
  1. Create app at developers.tiktok.com
  2. Add redirect URI: http://localhost:8765/api/tiktok/callback
  3. Enable scope: video.publish, video.upload
  4. Set TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET in .env
  5. Visit http://localhost:8765/api/tiktok/auth to authorize

Two things about this API that are easy to get wrong:

**Audit gates privacy.** Until the app clears TikTok's review, the authorized
creator's only allowed privacy option is SELF_ONLY — a hard-coded
PUBLIC_TO_EVERYONE is rejected. `query_creator_info()` asks what is permitted and
`negotiate_privacy_level()` picks the strongest allowed option, so an unaudited
app posts privately instead of failing. `upload_clip()` returns the level that was
actually used; report that, not the one requested.

**Access tokens live ~24h.** Refresh tokens live ~365d and rotate on every use.
Always read the token via `get_valid_access_token()`, which refreshes when the
stored deadline is inside TOKEN_REFRESH_SKEW_SEC. Reading TIKTOK_ACCESS_TOKEN
directly works for one day after authorizing and then 401s silently — which is
exactly how scheduled posting dies without anyone noticing.

`token_state()` reports what is actually possible right now (live / needs refresh
/ needs re-authorization) rather than whether a token string happens to be set.

PKCE note: TikTok deviates from RFC 7636 and expects a hex-encoded SHA-256
code_challenge, not base64url. `get_auth_url()` is correct as written — do not
"fix" it to base64url.
"""

import asyncio
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv, set_key

load_dotenv(Path(__file__).parent.parent / ".env")

TT_AUTH_URL  = "https://www.tiktok.com/v2/auth/authorize/"
TT_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TT_INIT_URL  = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TT_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
TT_CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
ENV_PATH     = Path(__file__).parent.parent / ".env"
REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI", "http://localhost:8765/api/tiktok/callback")

# Refresh the access token this many seconds before it actually expires, so a
# long upload started near the boundary does not die mid-flight.
TOKEN_REFRESH_SKEW_SEC = 300

# Preference order when the creator's allowed privacy options do not include the
# level we asked for. An unaudited TikTok app is restricted to SELF_ONLY, so that
# has to be a landing spot rather than a failure.
PRIVACY_FALLBACK_ORDER = (
    "PUBLIC_TO_EVERYONE",
    "MUTUAL_FOLLOW_FRIENDS",
    "FOLLOWER_OF_CREATOR",
    "SELF_ONLY",
)

_pending_state: dict = {}   # state -> {code_verifier, ...}


class TikTokAuthError(RuntimeError):
    """Raised when no usable access token can be obtained (re-authorization needed)."""


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def get_auth_url() -> tuple[str, str]:
    """
    Build TikTok OAuth authorization URL.
    Returns (url, state) — state must be stored and verified on callback.
    """
    client_key    = os.getenv("TIKTOK_CLIENT_KEY", "")
    state         = secrets.token_urlsafe(16)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = hashlib.sha256(code_verifier.encode()).hexdigest()

    _pending_state[state] = {"code_verifier": code_verifier}

    params = {
        "client_key":             client_key,
        "response_type":          "code",
        "scope":                  "video.publish,video.upload",
        "redirect_uri":           REDIRECT_URI,
        "state":                  state,
        "code_challenge":         code_challenge,
        "code_challenge_method":  "S256",
    }
    return TT_AUTH_URL + "?" + urlencode(params), state


async def exchange_code(code: str, state: str) -> dict:
    """Exchange auth code for access + refresh tokens. Saves to .env."""
    pending = _pending_state.pop(state, {})
    code_verifier = pending.get("code_verifier", "")

    async with httpx.AsyncClient() as client:
        resp = await client.post(TT_TOKEN_URL, data={
            "client_key":     os.getenv("TIKTOK_CLIENT_KEY", ""),
            "client_secret":  os.getenv("TIKTOK_CLIENT_SECRET", ""),
            "code":           code,
            "grant_type":     "authorization_code",
            "redirect_uri":   REDIRECT_URI,
            "code_verifier":  code_verifier,
        })

    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"TikTok token exchange failed: {data}")

    _persist_tokens(data)
    return data


def _persist_tokens(data: dict) -> None:
    """
    Write the token set to .env and reload it into this process.

    TikTok access tokens live ~24h and refresh tokens ~365d, both delivered as
    relative `expires_in` seconds. We store absolute epoch deadlines so a later
    process can tell whether the token is still good without another round trip.
    """
    now = int(time.time())
    set_key(str(ENV_PATH), "TIKTOK_ACCESS_TOKEN",  data["access_token"])
    if data.get("refresh_token"):
        set_key(str(ENV_PATH), "TIKTOK_REFRESH_TOKEN", data["refresh_token"])
    if data.get("open_id"):
        set_key(str(ENV_PATH), "TIKTOK_OPEN_ID", data["open_id"])
    if data.get("expires_in"):
        set_key(str(ENV_PATH), "TIKTOK_TOKEN_EXPIRES_AT", str(now + int(data["expires_in"])))
    if data.get("refresh_expires_in"):
        set_key(
            str(ENV_PATH),
            "TIKTOK_REFRESH_EXPIRES_AT",
            str(now + int(data["refresh_expires_in"])),
        )

    # Reload so the current process sees the new values
    load_dotenv(ENV_PATH, override=True)


def is_authorized() -> bool:
    return bool(os.getenv("TIKTOK_ACCESS_TOKEN", "").strip())


def _env_epoch(key: str) -> int:
    """Read an absolute epoch deadline from env; 0 when unset or unparseable."""
    raw = (os.getenv(key, "") or "").strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def token_state(now: Optional[int] = None) -> dict:
    """
    Report what we can actually do right now, not merely what is configured.

    Returns {authorized, access_valid, needs_refresh, refresh_valid, expires_in,
    reason}. `authorized` means an access token string exists; `access_valid`
    means it is believed live. When the stored deadline is missing (tokens issued
    before expiry tracking existed) we cannot prove freshness, so we report
    needs_refresh and let the refresh call be the probe.
    """
    now = int(time.time()) if now is None else now
    access = (os.getenv("TIKTOK_ACCESS_TOKEN", "") or "").strip()
    refresh = (os.getenv("TIKTOK_REFRESH_TOKEN", "") or "").strip()
    access_deadline = _env_epoch("TIKTOK_TOKEN_EXPIRES_AT")
    refresh_deadline = _env_epoch("TIKTOK_REFRESH_EXPIRES_AT")

    refresh_valid = bool(refresh) and (refresh_deadline == 0 or refresh_deadline > now)

    if not access:
        reason = "no access token — authorize at /api/tiktok/auth"
        return {
            "authorized": False, "access_valid": False, "needs_refresh": False,
            "refresh_valid": refresh_valid, "expires_in": 0, "reason": reason,
        }

    if access_deadline == 0:
        return {
            "authorized": True, "access_valid": False, "needs_refresh": True,
            "refresh_valid": refresh_valid, "expires_in": 0,
            "reason": "token has no recorded expiry — will refresh to establish one",
        }

    remaining = access_deadline - now
    needs_refresh = remaining <= TOKEN_REFRESH_SKEW_SEC
    return {
        "authorized": True,
        "access_valid": not needs_refresh,
        "needs_refresh": needs_refresh,
        "refresh_valid": refresh_valid,
        "expires_in": max(0, remaining),
        "reason": "expired or expiring" if needs_refresh else "live",
    }


async def refresh_access_token() -> dict:
    """
    Exchange the stored refresh token for a fresh access token.

    TikTok rotates the refresh token on every use, so the response must be
    persisted or the next refresh fails with an already-consumed token.
    """
    refresh = (os.getenv("TIKTOK_REFRESH_TOKEN", "") or "").strip()
    if not refresh:
        raise TikTokAuthError(
            "TikTok has no refresh token stored — re-authorize at /api/tiktok/auth"
        )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TT_TOKEN_URL, data={
            "client_key":     os.getenv("TIKTOK_CLIENT_KEY", ""),
            "client_secret":  os.getenv("TIKTOK_CLIENT_SECRET", ""),
            "grant_type":     "refresh_token",
            "refresh_token":  refresh,
        })

    data = resp.json()
    if "access_token" not in data:
        raise TikTokAuthError(
            f"TikTok refresh failed ({data.get('error', 'unknown')}): "
            f"{data.get('error_description', data)} — re-authorize at /api/tiktok/auth"
        )

    _persist_tokens(data)
    return data


async def get_valid_access_token() -> str:
    """
    Return an access token that is live right now, refreshing if needed.

    This is the only supported way to read the token for an API call. Reading
    TIKTOK_ACCESS_TOKEN directly works exactly once per day and then silently
    starts 401ing, which is how scheduled posting dies without anyone noticing.
    """
    state = token_state()
    if not state["authorized"] and not state["refresh_valid"]:
        raise TikTokAuthError(
            "TikTok is not authorized — visit /api/tiktok/auth to connect the account"
        )
    if state["access_valid"]:
        return (os.getenv("TIKTOK_ACCESS_TOKEN", "") or "").strip()

    await refresh_access_token()
    return (os.getenv("TIKTOK_ACCESS_TOKEN", "") or "").strip()


# ── Creator info + privacy negotiation ────────────────────────────────────────

async def query_creator_info() -> dict:
    """
    Ask TikTok what this creator is allowed to do.

    Returns the `data` block, which carries privacy_level_options,
    max_video_post_duration_sec, creator_nickname and the interaction
    disable_* flags. An app that has not cleared TikTok's audit gets back
    ["SELF_ONLY"] here — which is precisely why we ask instead of assuming.
    """
    token = await get_valid_access_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TT_CREATOR_INFO_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
            json={},
        )
    payload = resp.json()
    err = payload.get("error", {}) or {}
    if err.get("code") not in (None, "ok"):
        raise RuntimeError(
            f"TikTok creator_info failed: {err.get('message', 'unknown')} ({err.get('code', '')})"
        )
    return payload.get("data", {}) or {}


def negotiate_privacy_level(requested: str, allowed: Optional[list]) -> str:
    """
    Pick the strongest privacy level the creator is actually permitted to use.

    Honors `requested` when it is allowed; otherwise walks
    PRIVACY_FALLBACK_ORDER and takes the first permitted option, so an
    unaudited app degrades to SELF_ONLY instead of raising. With no allow-list
    (creator_info unavailable) the request is returned unchanged.
    """
    if not allowed:
        return requested
    if requested in allowed:
        return requested
    for level in PRIVACY_FALLBACK_ORDER:
        if level in allowed:
            return level
    return allowed[0]


# ── Video upload ──────────────────────────────────────────────────────────────

async def upload_clip(
    video_path: str,
    title: str,
    schedule_time: Optional[int] = None,   # Unix timestamp; None = post now
    progress_cb: Optional[Callable[[str], None]] = None,
    privacy_level: str = "PUBLIC_TO_EVERYONE",
    duration_sec: Optional[float] = None,
) -> dict:
    """
    Upload a video to TikTok via Content Posting API.

    The requested `privacy_level` is negotiated against what the creator is
    actually allowed to post (see negotiate_privacy_level); the level used is
    returned so the caller can report it truthfully rather than assuming public.

    Returns {"publish_id", "url", "privacy_level", "requested_privacy_level"}.
    """
    def log(msg):
        if progress_cb: progress_cb(msg)

    access_token = await get_valid_access_token()

    video_file = Path(video_path)
    if not video_file.exists():
        raise FileNotFoundError(f"Clip file not found: {video_path}")
    file_size  = video_file.stat().st_size
    if file_size == 0:
        raise RuntimeError(f"Clip file is empty: {video_path}")

    # Ask TikTok what this creator may do before claiming anything about it.
    allowed_privacy: Optional[list] = None
    max_duration: Optional[int] = None
    try:
        info = await query_creator_info()
        allowed_privacy = info.get("privacy_level_options") or None
        max_duration = info.get("max_video_post_duration_sec")
    except TikTokAuthError:
        raise
    except Exception as exc:
        log(f"creator_info unavailable ({exc}) — proceeding with requested privacy level")

    effective_privacy = negotiate_privacy_level(privacy_level, allowed_privacy)
    if effective_privacy != privacy_level:
        log(
            f"Privacy downgraded {privacy_level} → {effective_privacy} "
            f"(creator allows {allowed_privacy})"
        )

    if duration_sec and max_duration and duration_sec > max_duration:
        raise RuntimeError(
            f"Clip is {duration_sec:.0f}s but this TikTok account caps posts at "
            f"{max_duration}s — trim the clip or get the app audited"
        )

    log(f"Uploading {video_file.name} ({file_size / 1_048_576:.1f} MB) to TikTok…")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json; charset=UTF-8",
    }

    # Build post info
    post_info: dict = {
        "title":            title[:150],
        "privacy_level":    effective_privacy,
        "disable_duet":     False,
        "disable_comment":  False,
        "disable_stitch":   False,
        "video_cover_timestamp_ms": 1000,
    }
    if schedule_time:
        post_info["auto_add_music"] = False
        # TikTok scheduling: must be 15min–10 days from now
        now = int(time.time())
        min_t = now + 15 * 60
        max_t = now + 10 * 24 * 3600
        post_info["scheduled_publish_time"] = max(min_t, min(schedule_time, max_t))

    body = {
        "post_info": post_info,
        "source_info": {
            "source":         "FILE_UPLOAD",
            "video_size":     file_size,
            "chunk_size":     file_size,   # single chunk
            "total_chunk_count": 1,
        },
    }

    async with httpx.AsyncClient(timeout=600) as client:
        # Step 1: Initialize upload
        init_resp = await client.post(TT_INIT_URL, headers=headers, json=body)
        init_data = init_resp.json()

        if init_data.get("error", {}).get("code") != "ok":
            err = init_data.get("error", {})
            raise RuntimeError(f"TikTok init failed: {err.get('message','unknown')} ({err.get('code','')})")

        upload_url = init_data["data"]["upload_url"]
        publish_id = init_data["data"]["publish_id"]
        log(f"Upload initialized (publish_id: {publish_id})")

        # Step 2: Upload the file in a single chunk
        log("Uploading video bytes to TikTok…")
        with open(video_path, "rb") as f:
            video_bytes = f.read()

        upload_headers = {
            "Content-Type":  "video/mp4",
            "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
            "Content-Length": str(file_size),
        }
        up_resp = await client.put(upload_url, content=video_bytes, headers=upload_headers)

        if up_resp.status_code not in (200, 201, 206):
            raise RuntimeError(f"TikTok upload PUT failed: {up_resp.status_code} {up_resp.text[:200]}")

        log("Upload complete — waiting for TikTok to process…")

    # Return publish info
    return {
        "publish_id": publish_id,
        "url": f"https://www.tiktok.com/",
        "privacy_level": effective_privacy,
        "requested_privacy_level": privacy_level,
    }


async def check_publish_status(publish_id: str) -> dict:
    """Poll TikTok for the publish status of a video."""
    access_token = await get_valid_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json; charset=UTF-8",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TT_STATUS_URL, headers=headers, json={"publish_id": publish_id})
    return resp.json()
