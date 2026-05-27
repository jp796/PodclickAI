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

    async def fetch_analytics(
        self,
        location_id: str,
        provider_post_id: str,
    ) -> Dict[str, Any]:
        """GHL Social Planner does not expose engagement analytics. Returns empty dict."""
        return {}

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
            payload["media"] = [{"url": u, "type": "image"} for u in media_urls]
        if first_comment:
            # Instagram first-comment pattern for hashtags
            payload["firstComment"] = first_comment
        if platform_specific:
            payload.update(platform_specific)
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
