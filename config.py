"""
PodClick — centralised settings.

All environment variables are loaded here via pydantic-settings.
Everywhere else in the codebase, import from here:

    from config import settings, get_current_location_id

Never read os.getenv() directly in route handlers.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # don't error on extra .env keys
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        ...,
        description="Neon Postgres connection string (postgresql://...)",
    )

    # SQLAlchemy async needs the asyncpg driver.
    # We expose a computed property rather than a second env var.
    @property
    def async_database_url(self) -> str:
        """
        Build asyncpg-compatible URL from DATABASE_URL.

        asyncpg does NOT accept URL query params like sslmode= or
        channel_binding=.  Strip everything after '?' and pass ssl
        via connect_args instead (see engine.py / alembic env.py).
        """
        url = self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # Strip all query params — asyncpg uses connect_args, not URL params
        url = url.split("?")[0]
        return url

    @property
    def requires_ssl(self) -> bool:
        """True when DATABASE_URL includes sslmode=require (Neon always does)."""
        return "sslmode=require" in self.database_url

    # ── Redis (Upstash) ───────────────────────────────────────────────────────
    redis_url: str = Field(
        ...,
        description="Upstash Redis connection string (redis://... or rediss://...)",
    )

    # ── Tenancy ───────────────────────────────────────────────────────────────
    titan_location_id: str = Field(
        ...,
        description=(
            "Hardcoded locationId for JP's Titan sub-account. "
            "Phase 1 placeholder — replaced by JWT-derived locationId in Phase 3."
        ),
    )

    # Legacy GHL fields kept for backwards compat with existing main.py code.
    ghl_token: str = Field(default="", description="GHL Private Integration Token")
    ghl_location_id: str = Field(
        default="",
        description="GHL location ID (legacy — prefer titan_location_id for new code)",
    )

    # ── OpenAI ───────────────────────────────────────────────────────────────
    openai_api_key: str = Field(default="", description="OpenAI API key")

    # ── Anthropic ────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="", description="Anthropic API key (Claude)")

    # ── Buzzsprout ────────────────────────────────────────────────────────────
    buzzsprout_api_key: str = Field(default="", description="Buzzsprout API token")
    buzzsprout_podcast_id: str = Field(default="", description="Buzzsprout podcast ID")

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: str = Field(default="", description="Telegram bot token")
    telegram_chat_id: str = Field(default="", description="Telegram chat/channel ID")

    # ── TikTok ───────────────────────────────────────────────────────────────
    tiktok_client_key: str = Field(default="", description="TikTok client key")
    tiktok_client_secret: str = Field(default="", description="TikTok client secret")
    tiktok_redirect_uri: str = Field(default="", description="TikTok OAuth redirect URI")

    # ── YouTube ───────────────────────────────────────────────────────────────
    youtube_data_api_key: str = Field(default="", description="YouTube Data API v3 key")

    # ── Pexels ────────────────────────────────────────────────────────────────
    pexels_api_key: str = Field(default="", description="Pexels API key for b-roll")

    # ── Meta (Facebook/Instagram) ─────────────────────────────────────────────
    meta_app_id: str = Field(default="", description="Meta App ID")
    meta_app_secret: str = Field(default="", description="Meta App Secret")

    # ── LinkedIn ─────────────────────────────────────────────────────────────
    linkedin_client_id: str = Field(default="", description="LinkedIn Client ID")
    linkedin_client_secret: str = Field(default="", description="LinkedIn Client Secret")

    @model_validator(mode="after")
    def _validate_required(self) -> "Settings":
        missing = []
        if not self.database_url:
            missing.append("DATABASE_URL")
        if not self.redis_url:
            missing.append("REDIS_URL")
        if not self.titan_location_id:
            missing.append("TITAN_LOCATION_ID")
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings singleton. Call this everywhere."""
    return Settings()


# Module-level convenience alias — `from config import settings`
settings = get_settings()


def get_current_location_id() -> str:
    """
    Return the active locationId for the current request context.

    Phase 1: returns the hardcoded Titan locationId from TITAN_LOCATION_ID.
    Phase 3: this function will be replaced with a JWT-derived value from
             the request context (FastAPI dependency). The call sites don't change.

    Usage:
        from config import get_current_location_id
        loc_id = get_current_location_id()
    """
    return settings.titan_location_id
