"""
TikTok Content Posting API v2 integration.
Handles OAuth 2.0 authorization and video upload/scheduling.

Setup (one-time):
  1. Create app at developers.tiktok.com
  2. Add redirect URI: http://localhost:8765/api/tiktok/callback
  3. Enable scope: video.publish, video.upload
  4. Set TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET in .env
  5. Visit http://localhost:8765/api/tiktok/auth to authorize
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
ENV_PATH     = Path(__file__).parent.parent / ".env"
REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI", "http://localhost:8765/api/tiktok/callback")

_pending_state: dict = {}   # state -> {code_verifier, ...}


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

    # Persist tokens to .env
    set_key(str(ENV_PATH), "TIKTOK_ACCESS_TOKEN",  data["access_token"])
    set_key(str(ENV_PATH), "TIKTOK_REFRESH_TOKEN", data.get("refresh_token", ""))
    set_key(str(ENV_PATH), "TIKTOK_OPEN_ID",       data.get("open_id", ""))

    # Reload so current process has new values
    load_dotenv(ENV_PATH, override=True)
    return data


def is_authorized() -> bool:
    return bool(os.getenv("TIKTOK_ACCESS_TOKEN", "").strip())


# ── Video upload ──────────────────────────────────────────────────────────────

async def upload_clip(
    video_path: str,
    title: str,
    schedule_time: Optional[int] = None,   # Unix timestamp; None = post now
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Upload a video to TikTok via Content Posting API.
    Returns {"publish_id": str, "url": str}
    """
    def log(msg):
        if progress_cb: progress_cb(msg)

    access_token = os.getenv("TIKTOK_ACCESS_TOKEN", "")
    if not access_token:
        raise RuntimeError("TikTok not authorized — visit http://localhost:8765/api/tiktok/auth")

    video_file = Path(video_path)
    file_size  = video_file.stat().st_size
    log(f"Uploading {video_file.name} ({file_size / 1_048_576:.1f} MB) to TikTok…")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json; charset=UTF-8",
    }

    # Build post info
    post_info: dict = {
        "title":            title[:150],
        "privacy_level":    "PUBLIC_TO_EVERYONE",
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
    }


async def check_publish_status(publish_id: str) -> dict:
    """Poll TikTok for the publish status of a video."""
    access_token = os.getenv("TIKTOK_ACCESS_TOKEN", "")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json; charset=UTF-8",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TT_STATUS_URL, headers=headers, json={"publish_id": publish_id})
    return resp.json()
