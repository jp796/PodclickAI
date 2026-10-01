"""
PodClick — GHLAdapter: GHL Social Planner implementation of SocialService.

This is THE ONLY file in the codebase that may call services.leadconnectorhq.com.
Verify with: rg 'leadconnectorhq' --type py (should return only this file)

Phase 2A: Uses static GHL_TOKEN (private integration token).
Phase 3: Swap to per-location OAuth tokens stored in oauth_tokens table,
         with Redis-locked token refresh background job.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from config import settings
from services.social_service import (
    SocialAuthError,
    SocialProviderError,
    SocialPublishError,
    SocialRateLimitError,
    SocialService,
)

logger = logging.getLogger(__name__)

_GHL_API_BASE = "https://services.leadconnectorhq.com"
_GHL_API_VER  = "2021-07-28"
_HTTP_TIMEOUT = 20


def _ghl_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Version": _GHL_API_VER,
        "Content-Type": "application/json",
    }


def _raise_for_status(response: httpx.Response) -> None:
    """
    Map GHL HTTP status codes to typed SocialService exceptions.

    Retry policy (enforced at the call site / worker):
      401  → SocialAuthError       — refresh token, retry once
      429  → SocialRateLimitError  — exponential backoff from 30s, max 4 retries
      5xx  → SocialProviderError   — exponential backoff from 30s, max 4 retries
      400  → SocialPublishError    — no retry, surface to Brick
      network → caller catches httpx.RequestError, treats as SocialProviderError
    """
    code = response.status_code
    if code == 401:
        raise SocialAuthError("GHL token expired or invalid — refresh required")
    if code == 429:
        retry_after = int(response.headers.get("Retry-After", "30"))
        raise SocialRateLimitError(retry_after=retry_after)
    if code >= 500:
        raise SocialProviderError(
            f"GHL returned {code}: {response.text[:200]}", status_code=code
        )
    if code >= 400:
        raise SocialPublishError(
            f"GHL returned {code}: {response.text[:200]}", status_code=code
        )


_MIME_BY_EXT = {
    ".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime",
    ".webm": "video/webm", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
}


def _to_mime_media(item: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a stored GHL media object into the MIME-typed form PUT requires.

    GHL stores and returns `type: "video"`, but its own PUT only accepts
    `type: "video/mp4"`. The extension is the authority; the stored bare type is
    the fallback when the URL carries no usable extension (CDN links with query
    strings are handled by stripping at "?").
    """
    out = dict(item)
    existing = str(out.get("type") or "")
    if "/" in existing:
        return out
    url = str(out.get("url") or "").split("?", 1)[0].lower()
    for ext, mime in _MIME_BY_EXT.items():
        if url.endswith(ext):
            out["type"] = mime
            return out
    out["type"] = "video/mp4" if existing == "video" else "image/jpeg"
    return out


class GHLAdapter(SocialService):
    """
    Implements SocialService against GHL Social Planner API.

    Phase 2A: location_id parameter is accepted but not used for token lookup —
    we use the global GHL_TOKEN (private integration token) for all locations.
    Phase 3: look up per-location OAuth token from oauth_tokens table.
    """

    def _get_token(self, location_id: str) -> str:
        """
        Resolve the GHL access token for a location.

        Phase 2A: returns the global private-integration token from settings.
        Phase 3: query oauth_tokens table, refresh if < 2h to expiry.
        """
        # Phase 3 TODO: look up per-location OAuth token with Redis-locked refresh
        token = settings.ghl_token
        if not token:
            raise SocialPublishError(
                "GHL_TOKEN not configured — cannot publish via GHL"
            )
        return token

    def _get_location(self, location_id: str) -> str:
        """
        Resolve the GHL locationId for API calls.

        Phase 2A: GHL_LOCATION_ID env var (legacy static value).
        Phase 3: derive from OAuth token install record.
        """
        loc = settings.ghl_location_id
        if not loc:
            raise SocialPublishError(
                "GHL_LOCATION_ID not configured — cannot publish via GHL"
            )
        return loc

    async def publish(
        self,
        location_id: str,
        platform: str,
        caption: str,
        account_id: str,
        media_urls: Optional[List[str]] = None,
        first_comment: Optional[str] = None,
        platform_specific: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Publish immediately to GHL Social Planner. Returns GHL post ID."""
        token = self._get_token(location_id)
        loc   = self._get_location(location_id)
        payload = self._build_payload(
            loc, account_id, caption,
            status="draft",
            media_urls=media_urls,
            first_comment=first_comment,
            platform_specific=platform_specific,
            platform=platform,
        )
        return await self._post_to_ghl(token, loc, payload)

    async def schedule(
        self,
        location_id: str,
        platform: str,
        caption: str,
        account_id: str,
        scheduled_at: str,
        media_urls: Optional[List[str]] = None,
        first_comment: Optional[str] = None,
        platform_specific: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Schedule a post at a specific time. Returns GHL post ID."""
        token = self._get_token(location_id)
        loc   = self._get_location(location_id)
        payload = self._build_payload(
            loc, account_id, caption,
            status="scheduled",
            scheduled_at=scheduled_at,
            media_urls=media_urls,
            first_comment=first_comment,
            platform_specific=platform_specific,
            platform=platform,
        )
        return await self._post_to_ghl(token, loc, payload)

    async def get_status(
        self,
        location_id: str,
        provider_post_id: str,
    ) -> Dict[str, Any]:
        """
        Fetch post status from GHL Social Planner.
        GET /social-media-posting/{locationId}/posts/{ghl_post_id}
        """
        token = self._get_token(location_id)
        loc   = self._get_location(location_id)
        url   = f"{_GHL_API_BASE}/social-media-posting/{loc}/posts/{provider_post_id}"

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url, headers=_ghl_headers(token))
                _raise_for_status(resp)
                data   = resp.json()
                raw_st = (data.get("status") or "unknown").lower()

                # Normalise GHL status → our canonical values
                status_map = {
                    "published":  "published",
                    "scheduled":  "scheduled",
                    "draft":      "scheduled",
                    "failed":     "failed",
                    "error":      "failed",
                }
                return {
                    "status":    status_map.get(raw_st, "unknown"),
                    "error":     data.get("error") or data.get("errorMessage"),
                    "raw":       data,
                }
        except (SocialAuthError, SocialRateLimitError, SocialProviderError, SocialPublishError):
            raise
        except httpx.RequestError as exc:
            raise SocialProviderError(f"Network error fetching GHL status: {exc}")

    async def get_post(
        self,
        location_id: str,
        provider_post_id: str,
    ) -> Dict[str, Any]:
        """
        Return the stored GHL post verbatim.

        get_status() normalises the response down to a single canonical status
        string and discards the body, which makes it impossible to confirm what
        was actually stored — whether the media came through as a video, whether
        tiktokPostDetails is populated, which account it targets. Verification
        needs the raw object, and contract #5 says every GHL HTTP call lives in
        this adapter, so the read belongs here rather than at a call site.
        """
        token = self._get_token(location_id)
        loc = self._get_location(location_id)
        url = f"{_GHL_API_BASE}/social-media-posting/{loc}/posts/{provider_post_id}"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers=_ghl_headers(token))
            _raise_for_status(resp)
            data = resp.json()

        # GHL nests the object under results.post on some endpoints and returns it
        # flat on others; hand back whichever is present.
        return (data.get("results") or {}).get("post") or data.get("post") or data

    # Fields GHL's planner PUT accepts. The API rejects unknown properties, so an
    # update is a read-modify-write over this whitelist rather than a blind merge of
    # the stored post — the GET returns server-owned keys (_id, insights, createdAt,
    # previewLink, ...) that the PUT refuses.
    _UPDATABLE_FIELDS = (
        "type", "accountIds", "summary", "media", "followUpComment",
        "tiktokPostDetails", "instagramPostDetails", "gmbPostDetails",
        "ogTagsDetails", "tags", "userId",
    )

    async def update_post(
        self,
        location_id: str,
        provider_post_id: str,
        status: Optional[str] = None,
        scheduled_at: Optional[str] = None,
        caption: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing planner post in place. Returns the stored post.

        PUT /social-media-posting/{locationId}/posts/{postId}

        This is the only way to move a draft to scheduled — `publish()` and
        `schedule()` both CREATE a post, so using them on an already-drafted clip
        duplicates it in the planner instead of promoting it.

        Read-modify-write: GHL's PUT replaces the post, so the current body is
        fetched and the untouched fields are carried forward. Dropping them would
        silently wipe the media and the `tiktokPostDetails` block that TikTok video
        posts require (see the 2026-07-27 fix in _build_payload).

        `scheduled_at` must be an ISO-8601 UTC instant. GHL stores it and reports it
        back as `displayDate`, NOT as the request key — verify a schedule by reading
        `displayDate`, never by trusting a 2xx.
        """
        token = self._get_token(location_id)
        loc   = self._get_location(location_id)

        current = await self.get_post(location_id, provider_post_id)

        payload: Dict[str, Any] = {
            k: current[k] for k in self._UPDATABLE_FIELDS if current.get(k) is not None
        }
        payload.setdefault("type", "post")
        # GHL's planner is asymmetric about media types: POST accepts the bare
        # "video"/"image" that _build_payload sends, and the GET echoes it back the
        # same way — but the PUT rejects it with 422 "media.0.Invalid media format
        # type" and demands a real MIME type. Omitting `media` is not an escape
        # either ("media must be an array with media objects"). So the stored value
        # has to be translated on the way back in. Verified against the live API
        # 2026-10-01.
        payload["media"] = [_to_mime_media(m) for m in (current.get("media") or [])]
        if not payload.get("userId") and settings.ghl_user_id:
            payload["userId"] = settings.ghl_user_id
        if caption is not None:
            payload["summary"] = caption
        if status is not None:
            payload["status"] = status
        else:
            payload["status"] = current.get("status") or "draft"
        if scheduled_at:
            # GHL's request key. The response echoes it as `displayDate`.
            payload["scheduleDate"] = scheduled_at

        url = f"{_GHL_API_BASE}/social-media-posting/{loc}/posts/{provider_post_id}"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.put(url, headers=_ghl_headers(token), json=payload)
                _raise_for_status(resp)
                data = resp.json()
        except (SocialAuthError, SocialRateLimitError, SocialProviderError, SocialPublishError):
            raise
        except httpx.RequestError as exc:
            raise SocialProviderError(f"Network error updating GHL post: {exc}")

        body = data.get("post") if isinstance(data.get("post"), dict) else data
        results = data.get("results")
        if isinstance(results, dict) and isinstance(results.get("post"), dict):
            body = results["post"]
        logger.info("[ghl.update_post] %s status=%s displayDate=%s",
                    provider_post_id, body.get("status"), body.get("displayDate"))
        return body

    async def fetch_analytics(
        self,
        location_id: str,
        provider_post_id: str,
    ) -> Dict[str, Any]:
        """GHL Social Planner does not expose engagement analytics. Returns empty dict."""
        return {}

    async def upload_media(
        self,
        location_id: str,
        file_path: str,
        filename: str = "",
        mime: str = "video/mp4",
    ) -> str:
        """Upload a local file to the GHL Media Library, return the GHL-hosted CDN URL.

        Tunnel-free media hosting: GHL fetches social-post media from a public URL,
        but PodClick clips live on localhost. Uploading them here yields a
        `assets.cdn.filesafe.space/...` URL GHL can post from. Requires the token's
        `medias.write` scope. Raises SocialProviderError on failure.
        """
        import os as _os
        token = self._get_token(location_id)
        loc   = self._get_location(location_id)
        name  = filename or _os.path.basename(file_path)
        url   = f"{_GHL_API_BASE}/medias/upload-file"
        headers = {"Authorization": f"Bearer {token}", "Version": _GHL_API_VER}
        try:
            with open(file_path, "rb") as fh:
                files = {"file": (name, fh, mime)}
                data  = {"name": name, "altType": "location", "altId": loc, "hosted": "false"}
                async with httpx.AsyncClient(timeout=180) as client:
                    resp = await client.post(url, headers=headers, data=data, files=files)
                    _raise_for_status(resp)
                    body = resp.json()
            cdn = body.get("url") or ""
            if not cdn:
                raise SocialProviderError(f"GHL media upload returned no URL: {body}")
            return cdn
        except (SocialAuthError, SocialRateLimitError, SocialProviderError, SocialPublishError):
            raise
        except httpx.RequestError as exc:
            raise SocialProviderError(f"Network error uploading media to GHL: {exc}")

    async def list_accounts(
        self,
        location_id: str,
    ) -> List[Dict[str, Any]]:
        """
        List connected GHL social accounts for a location.
        GET /social-media-posting/{locationId}/accounts
        """
        token = self._get_token(location_id)
        loc   = self._get_location(location_id)
        url   = f"{_GHL_API_BASE}/social-media-posting/{loc}/accounts"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=_ghl_headers(token))
                _raise_for_status(resp)
                data     = resp.json()
                accounts = data.get("results", {}).get("accounts", [])
                return [
                    {
                        "id":       a["id"],
                        "name":     a["name"],
                        "platform": a["platform"],
                        "expired":  a.get("isExpired", False),
                    }
                    for a in accounts
                    if not a.get("deleted", False)
                ]
        except (SocialAuthError, SocialRateLimitError, SocialProviderError, SocialPublishError):
            raise
        except httpx.RequestError as exc:
            raise SocialProviderError(f"Network error fetching GHL accounts: {exc}")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_payload(
        self,
        loc: str,
        account_id: str,
        caption: str,
        status: str,
        scheduled_at: Optional[str] = None,
        media_urls: Optional[List[str]] = None,
        first_comment: Optional[str] = None,
        platform_specific: Optional[Dict[str, Any]] = None,
        platform: str = "",
    ) -> Dict[str, Any]:
        # locationId goes in the URL path, NOT in the body — GHL rejects it in body
        # userId is required by GHL social planner API (discovered 2026-05-27)
        user_id = settings.ghl_user_id
        payload: Dict[str, Any] = {
            "type":       "post",
            "accountIds": [account_id],
            "summary":    caption,
            "status":     status,
        }
        if user_id:
            payload["userId"] = user_id
        if scheduled_at:
            payload["scheduledAt"] = scheduled_at
        if media_urls:
            # Detect video vs image by extension so IG Reels / TikTok get type=video
            # (was hardcoded "image" — video-only platforms rejected image posts).
            def _media_type(u: str) -> str:
                stem = u.split("?", 1)[0].lower()
                return "video" if stem.endswith((".mp4", ".mov", ".webm", ".m4v")) else "image"
            payload["media"] = [{"url": u, "type": _media_type(u)} for u in media_urls]
        if first_comment:
            # Instagram first-comment pattern for hashtags
            payload["firstComment"] = first_comment
        if platform_specific:
            # Strip internal PodClick tracking keys — GHL rejects unknown properties
            _INTERNAL_KEYS = {"ghl_account_id", "stagger_offset_s", "pillar"}
            ghl_extras = {k: v for k, v in platform_specific.items() if k not in _INTERNAL_KEYS}
            if ghl_extras:
                payload.update(ghl_extras)
        # TikTok video posts require tiktokPostDetails; an empty object makes GHL's
        # planner flag "TikTok will not support thumbnail in the Video" and blocks
        # publish. TikTok uses a video frame as the cover (no custom thumbnail), so
        # we set sensible public defaults. Caller can override via platform_specific.
        if platform.lower() == "tiktok" and "tiktokPostDetails" not in payload:
            payload["tiktokPostDetails"] = {
                "privacyLevel":     "PUBLIC_TO_EVERYONE",
                "promoteOtherBrand": False,
                "enableComment":     True,
                "enableDuet":        True,
                "enableStitch":      True,
                "videoDisclosure":   False,
            }
        return payload

    async def _post_to_ghl(
        self,
        token: str,
        loc: str,
        payload: Dict[str, Any],
    ) -> str:
        """Execute the POST and return the GHL post ID."""
        url = f"{_GHL_API_BASE}/social-media-posting/{loc}/posts"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(url, headers=_ghl_headers(token), json=payload)
                _raise_for_status(resp)
                result  = resp.json()
                # GHL social planner returns: {"results": {"post": {"_id": "..."}}, ...}
                # Fallback chain: results.post._id → id → post.id
                post_id = (
                    (result.get("results") or {}).get("post", {}).get("_id")
                    or result.get("id")
                    or result.get("post", {}).get("id", "")
                )
                if not post_id:
                    logger.warning("GHL publish returned no post ID: %s", result)
                return str(post_id)
        except (SocialAuthError, SocialRateLimitError, SocialProviderError, SocialPublishError):
            raise
        except httpx.RequestError as exc:
            raise SocialProviderError(f"Network error posting to GHL: {exc}")


# ── Module-level singleton ────────────────────────────────────────────────────

ghl_adapter = GHLAdapter()
