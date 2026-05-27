"""
PodClick — SocialService abstract interface.

Every social publish MUST go through an implementation of this interface.
Never call GHL (or any other provider) directly outside of an adapter.
Five-contract rule: all social publishes go through SocialService abstraction.

Phase 2A: GHLAdapter is the only implementation.
Phase 3+: future providers (Postiz, direct platform APIs) swap in here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class SocialService(ABC):
    """
    Abstract contract for social publishing operations.

    All implementations must be async-safe and raise on hard failures
    rather than returning error dicts (callers handle exceptions).
    """

    @abstractmethod
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
        """
        Publish immediately. Returns the provider post ID (string).

        Raises:
            SocialPublishError: on any non-retryable failure (e.g. 400 Bad Request)
            SocialAuthError: on 401 — caller should refresh token and retry once
            SocialRateLimitError: on 429 — caller should back off
            SocialProviderError: on 5xx — caller should back off
        """

    @abstractmethod
    async def schedule(
        self,
        location_id: str,
        platform: str,
        caption: str,
        account_id: str,
        scheduled_at: str,                       # ISO-8601 timestamp
        media_urls: Optional[List[str]] = None,
        first_comment: Optional[str] = None,
        platform_specific: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Schedule a post. Returns the provider post ID."""

    @abstractmethod
    async def get_status(
        self,
        location_id: str,
        provider_post_id: str,
    ) -> Dict[str, Any]:
        """
        Fetch current status from provider.

        Returns a dict with at minimum:
            { "status": "published" | "scheduled" | "failed" | "unknown",
              "error": str | None }
        """

    @abstractmethod
    async def fetch_analytics(
        self,
        location_id: str,
        provider_post_id: str,
    ) -> Dict[str, Any]:
        """
        Fetch engagement analytics for a published post.

        Returns a dict with platform-specific fields.
        Returns empty dict if analytics not available yet.
        """

    @abstractmethod
    async def list_accounts(
        self,
        location_id: str,
    ) -> List[Dict[str, Any]]:
        """
        List connected social accounts for a location.

        Each dict has: { "id", "name", "platform", "expired" }
        """


# ── Exceptions ────────────────────────────────────────────────────────────────

class SocialPublishError(Exception):
    """Non-retryable publish failure (e.g. 400 Bad Request, validation error)."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class SocialAuthError(Exception):
    """401 — token expired or invalid. Refresh token, then retry once."""

    def __init__(self, message: str = "Token expired"):
        super().__init__(message)


class SocialRateLimitError(Exception):
    """429 — rate limited. Caller should apply exponential backoff."""

    def __init__(self, retry_after: int = 30):
        super().__init__(f"Rate limited — retry after {retry_after}s")
        self.retry_after = retry_after


class SocialProviderError(Exception):
    """5xx — provider-side error. Caller should apply exponential backoff."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code
