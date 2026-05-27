"""
PodClick — Arq publish worker.

Start with:
    venv/bin/python -m arq workers.publish_worker.WorkerSettings

Worker settings:
  - max_jobs=8       (global concurrency cap, SOW section 8 Layer 2)
  - Redis: Upstash TLS (rediss://)
  - Graceful shutdown: arq handles SIGTERM by default (stops accepting jobs,
    lets in-flight jobs finish before exiting)

Python 3.9 — no str | None syntax; Optional[str] only.
"""

from __future__ import annotations

import logging
import ssl as _ssl
import urllib.parse

from arq.connections import RedisSettings

from config import settings
from workers.publish_jobs import publish_variant, verify_attempt

logger = logging.getLogger(__name__)


def _arq_redis_settings() -> RedisSettings:
    """
    Build ArqRedis connection settings from REDIS_URL.

    Handles rediss:// (TLS, Upstash) and redis:// (no TLS, local dev).
    Strips any path component that some redis URL formats include.
    """
    url = settings.redis_url
    parsed = urllib.parse.urlparse(url)

    use_ssl = parsed.scheme == "rediss"
    host    = parsed.hostname or "localhost"
    port    = parsed.port    or 6379
    password = urllib.parse.unquote(parsed.password) if parsed.password else None

    ssl_ctx = _ssl.create_default_context() if use_ssl else None

    return RedisSettings(
        host=host,
        port=port,
        password=password,
        ssl=ssl_ctx,
    )


class WorkerSettings:
    """
    Arq WorkerSettings — consumed by `python -m arq workers.publish_worker.WorkerSettings`.

    max_jobs=8: no more than 8 publish tasks in flight across all workers at once.
    This is the global concurrency cap (SOW section 8, Layer 2).
    """

    functions   = [publish_variant, verify_attempt]
    redis_settings = _arq_redis_settings()
    max_jobs    = 8
    job_timeout = 120   # seconds per job before arq marks it failed

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        logger.info("[publish_worker] started — max_jobs=%d", WorkerSettings.max_jobs)

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        logger.info("[publish_worker] shutdown")
