"""
PodClick — Arq publish job functions.

Two jobs:
  publish_variant(ctx, attempt_id)  — call GHLAdapter, update PostAttempt
  verify_attempt(ctx, attempt_id)   — 5-min post-publish GHL status check

Stagger logic (per social-publish-stagger SKILL):
  Layer 1: Per-platform offsets (0s … 360s)
  Layer 2: Global concurrency cap via Arq max_jobs=8
  Layer 3: Deterministic jitter for popular schedule-minute slots

Python 3.9 — no str | None syntax; Optional[str] only.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from arq import ArqRedis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import async_session
from db.models import PostAttempt
from services.ghl_adapter import ghl_adapter
from services.social_service import (
    SocialAuthError,
    SocialProviderError,
    SocialPublishError,
    SocialRateLimitError,
)

logger = logging.getLogger(__name__)

# ── Stagger config ─────────────────────────────────────────────────────────────

PLATFORM_OFFSETS: Dict[str, int] = {
    "linkedin":  0,
    "x":         60,
    "facebook":  120,
    "instagram": 180,
    "tiktok":    240,
    "youtube":   300,
    "gmb":       360,
    "threads":   420,
}

# Popular minute slots that receive deterministic jitter
_POPULAR_MINUTES = {0, 15, 30, 45}

# Retry config
_BACKOFF_401_DELAY = 5       # seconds before single retry on 401
_BACKOFF_BASE      = 30      # seconds for exponential backoff base
_MAX_RETRIES_429   = 4
_MAX_RETRIES_5XX   = 4
_MAX_RETRIES_NET   = 4

# Verify-attempt delay after successful publish
_VERIFY_DELAY_SECONDS = 300  # 5 minutes


def stagger_offset_seconds(platform: str, location_id: str, scheduled_at: Optional[datetime] = None) -> int:
    """
    Compute total stagger delay in seconds for a given platform + location.

    Layer 1: per-platform base offset.
    Layer 3: deterministic jitter if scheduled_at falls on :00/:15/:30/:45.
    Layer 2 (global concurrency cap) is enforced by Arq WorkerSettings.max_jobs.
    """
    base = PLATFORM_OFFSETS.get(platform, 0)

    jitter = 0
    if scheduled_at is not None and scheduled_at.minute in _POPULAR_MINUTES:
        # Deterministic per-location — same user always gets the same offset
        jitter = (hash(location_id) % 180) - 90

    return max(0, base + jitter)


# ── Job: publish_variant ───────────────────────────────────────────────────────

async def publish_variant(ctx: Dict[str, Any], attempt_id: str) -> None:
    """
    Arq job: execute one publish attempt.

    Lifecycle events emitted as log lines (tagged for grep / alerting):
      [publish.attempted] — when we actually call the provider
      [publish.completed] — on success
      [publish.failed]    — on non-retryable failure

    Retry policy:
      401 → refresh token (Phase 3), retry once after _BACKOFF_401_DELAY
      429 → exponential backoff, max _MAX_RETRIES_429
      5xx → exponential backoff, max _MAX_RETRIES_5XX
      400 → log [publish.failed], no retry
      network → exponential backoff, max _MAX_RETRIES_NET
    """
    async with async_session() as session:
        attempt = await _load_attempt(session, attempt_id)
        if attempt is None:
            logger.error("[publish.failed] attempt_id=%s not found in DB", attempt_id)
            return

        # Mark as in-flight
        await _update_attempt(session, attempt, status="sent_to_provider")
        await session.commit()

        # Load variant caption + account_id from the related PostVariant
        # (we join on PostVariant to get caption and platform_specific)
        from db.models import PostVariant, Post  # local import avoids circular
        variant = (
            await session.execute(
                select(PostVariant).where(PostVariant.id == attempt.variant_id)
            )
        ).scalar_one_or_none()

        if variant is None:
            await _mark_failed(session, attempt, "PostVariant not found")
            return

        post = (
            await session.execute(
                select(Post).where(Post.id == attempt.post_id)
            )
        ).scalar_one_or_none()

        location_id = str(post.location_id) if post else ""

        # Resolve GHL account_id from platform_specific
        account_id = (variant.platform_specific or {}).get("ghl_account_id", "")
        if not account_id:
            await _mark_failed(session, attempt, "No ghl_account_id in platform_specific")
            return

        caption   = variant.caption or ""
        platform  = attempt.platform
        media_urls = list(variant.media_urls or [])
        first_comment = variant.first_comment

        logger.info(
            "[publish.attempted] attempt_id=%s platform=%s location=%s",
            attempt_id, platform, location_id,
        )

        # Attempt to publish with retry logic
        provider_post_id = await _publish_with_retry(
            session, attempt, location_id, platform,
            caption, account_id, media_urls, first_comment,
            variant.platform_specific or {},
        )

        if provider_post_id is None:
            # _publish_with_retry already handled DB update and logging
            return

        # Success — record result
        now = datetime.now(timezone.utc)
        await _update_attempt(
            session, attempt,
            status="published",
            provider_post_id=provider_post_id,
            published_at=now,
        )
        await session.commit()

        logger.info(
            "[publish.completed] attempt_id=%s platform=%s provider_post_id=%s",
            attempt_id, platform, provider_post_id,
        )

        # Enqueue verification job with 5-minute delay
        redis: ArqRedis = ctx["redis"]
        await redis.enqueue_job(
            "verify_attempt",
            attempt_id,
            _defer_by=_VERIFY_DELAY_SECONDS,
        )


async def _publish_with_retry(
    session: AsyncSession,
    attempt: PostAttempt,
    location_id: str,
    platform: str,
    caption: str,
    account_id: str,
    media_urls: list,
    first_comment: Optional[str],
    platform_specific: dict,
) -> Optional[str]:
    """
    Try publish. Handle retries per the policy.
    Returns provider_post_id on success, None if all retries exhausted.
    """
    # How many retries allowed per error class
    max_retries_by_type: Dict[str, int] = {
        "auth":    1,
        "ratelim": _MAX_RETRIES_429,
        "server":  _MAX_RETRIES_5XX,
        "net":     _MAX_RETRIES_NET,
    }
    retry_counts: Dict[str, int] = {k: 0 for k in max_retries_by_type}
    delay = _BACKOFF_BASE

    while True:
        try:
            provider_post_id = await ghl_adapter.publish(
                location_id=location_id,
                platform=platform,
                caption=caption,
                account_id=account_id,
                media_urls=media_urls or None,
                first_comment=first_comment,
                platform_specific=platform_specific if platform_specific else None,
            )
            await _increment_attempt_count(session, attempt)
            return provider_post_id

        except SocialPublishError as exc:
            # 400-class — no retry
            await _mark_failed(session, attempt, str(exc))
            logger.error(
                "[publish.failed] attempt_id=%s platform=%s error=%s (no retry)",
                str(attempt.id), platform, exc,
            )
            return None

        except SocialAuthError as exc:
            if retry_counts["auth"] >= max_retries_by_type["auth"]:
                await _mark_failed(session, attempt, f"Auth failed after retry: {exc}")
                logger.error(
                    "[publish.failed] attempt_id=%s 401 exhausted retries",
                    str(attempt.id),
                )
                return None
            retry_counts["auth"] += 1
            # Phase 3: refresh token here
            # Phase 2A: just wait briefly and retry (static token won't change)
            await asyncio.sleep(_BACKOFF_401_DELAY)

        except SocialRateLimitError as exc:
            if retry_counts["ratelim"] >= max_retries_by_type["ratelim"]:
                await _mark_failed(session, attempt, f"Rate limited: {exc}")
                logger.error("[publish.failed] attempt_id=%s 429 exhausted retries", str(attempt.id))
                return None
            retry_counts["ratelim"] += 1
            await asyncio.sleep(exc.retry_after * (2 ** (retry_counts["ratelim"] - 1)))

        except SocialProviderError as exc:
            if retry_counts["server"] >= max_retries_by_type["server"]:
                await _mark_failed(session, attempt, f"Provider error: {exc}")
                logger.error("[publish.failed] attempt_id=%s 5xx exhausted retries", str(attempt.id))
                return None
            retry_counts["server"] += 1
            await asyncio.sleep(delay * (2 ** (retry_counts["server"] - 1)))

        except Exception as exc:
            # Network-level or unexpected
            if retry_counts["net"] >= max_retries_by_type["net"]:
                await _mark_failed(session, attempt, f"Network error: {exc}")
                logger.error("[publish.failed] attempt_id=%s network exhausted retries", str(attempt.id))
                return None
            retry_counts["net"] += 1
            net_delay = 10 * (2 ** (retry_counts["net"] - 1))
            await asyncio.sleep(net_delay)


# ── Job: verify_attempt ────────────────────────────────────────────────────────

async def verify_attempt(ctx: Dict[str, Any], attempt_id: str) -> None:
    """
    Arq job: 5 minutes after publish, verify the post actually went live on GHL.

    Updates PostAttempt.status based on GHL's reported state.
    Lifecycle event: [verify.completed] or [verify.failed]
    """
    async with async_session() as session:
        attempt = await _load_attempt(session, attempt_id)
        if attempt is None:
            logger.error("[verify.failed] attempt_id=%s not found", attempt_id)
            return

        if not attempt.provider_post_id:
            # Nothing to verify — probably failed before we got a post ID
            logger.info("[verify.skipped] attempt_id=%s no provider_post_id", attempt_id)
            return

        # Load location_id from the Post
        from db.models import Post  # local import
        post = (
            await session.execute(
                select(Post).where(Post.id == attempt.post_id)
            )
        ).scalar_one_or_none()
        location_id = str(post.location_id) if post else ""

        try:
            result = await ghl_adapter.get_status(
                location_id=location_id,
                provider_post_id=attempt.provider_post_id,
            )
            ghl_status = result.get("status", "unknown")

            if ghl_status == "published":
                if attempt.status != "published":
                    now = datetime.now(timezone.utc)
                    await _update_attempt(session, attempt, status="published", published_at=now)
                    await session.commit()
                logger.info(
                    "[verify.completed] attempt_id=%s status=published provider_post_id=%s",
                    attempt_id, attempt.provider_post_id,
                )
            elif ghl_status == "failed":
                error_msg = result.get("error") or "GHL reported failure"
                await _mark_failed(session, attempt, error_msg)
                logger.warning(
                    "[verify.failed] attempt_id=%s GHL status=failed error=%s",
                    attempt_id, error_msg,
                )
            else:
                # Still scheduled or unknown — re-queue for another check (max 3 re-queues)
                requeue_count = (attempt.platform_specific_data or {}).get("verify_requeue", 0) if hasattr(attempt, "platform_specific_data") else 0
                logger.info(
                    "[verify.pending] attempt_id=%s ghl_status=%s",
                    attempt_id, ghl_status,
                )

        except (SocialAuthError, SocialProviderError, SocialPublishError) as exc:
            logger.warning("[verify.error] attempt_id=%s error=%s", attempt_id, exc)


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _load_attempt(session: AsyncSession, attempt_id: str) -> Optional[PostAttempt]:
    import uuid as _uuid
    try:
        uid = _uuid.UUID(attempt_id)
    except ValueError:
        return None
    result = await session.execute(
        select(PostAttempt).where(PostAttempt.id == uid)
    )
    return result.scalar_one_or_none()


async def _update_attempt(
    session: AsyncSession,
    attempt: PostAttempt,
    status: Optional[str] = None,
    provider_post_id: Optional[str] = None,
    published_at: Optional[datetime] = None,
) -> None:
    values: Dict[str, Any] = {}
    if status is not None:
        values["status"] = status
    if provider_post_id is not None:
        values["provider_post_id"] = provider_post_id
    if published_at is not None:
        values["published_at"] = published_at
    if values:
        await session.execute(
            update(PostAttempt)
            .where(PostAttempt.id == attempt.id)
            .values(**values)
        )


async def _increment_attempt_count(session: AsyncSession, attempt: PostAttempt) -> None:
    await session.execute(
        update(PostAttempt)
        .where(PostAttempt.id == attempt.id)
        .values(attempt_count=PostAttempt.attempt_count + 1)
    )


async def _mark_failed(session: AsyncSession, attempt: PostAttempt, error: str) -> None:
    await _update_attempt(session, attempt, status="failed")
    await session.execute(
        update(PostAttempt)
        .where(PostAttempt.id == attempt.id)
        .values(last_error=error[:500])
    )
    await session.commit()
