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
        env_ignore_empty=True,   # .env file wins over empty shell env vars
    )

    # Explicit local-only operation until verified customer authentication and
    # tenant-scoped resources are implemented. Cloud deployments default locked.
    podclick_deployment_mode: str = Field(default="locked")

    # Owner background automation (Brick's 04:00 planning cron, the nightly
    # Foundation recompute, the release scheduler, the autopilot worker) in a
    # DEPLOYED install. Separate from podclick_deployment_mode on purpose: that
    # setting governs the HTTP/WebSocket/media perimeter via DeploymentBoundary,
    # while this governs internal scheduling only and opens no network surface.
    #
    # Before Wave 1 the crons were registered inside a block that returned early
    # for any non-local mode, so Brick's planning loop had never run anywhere but
    # a laptop — the entire "wake up to a walk-through" premise was inert in
    # deployment. Defaults False so the fail-closed posture is unchanged until an
    # operator opts in; PODCLICK_AUTOMATION_DISABLED=1 still overrides it.
    podclick_owner_automation: bool = Field(default=False)

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
    ghl_user_id: str = Field(
        default="",
        description="GHL user ID for the location owner — required by social planner POST API",
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

    # ── Media providers (Agents Hub, AGENTS_HUB_SPEC §4.5) ────────────────────
    # Read through services.media.base.setting() — never logged, never sent to
    # the browser, never written to a job file.
    elevenlabs_api_key: str = Field(default="", description="ElevenLabs API key (Voiceover / Avatar)")
    elevenlabs_voice_id: str = Field(default="", description="Default ElevenLabs voice id (your cloned voice)")
    elevenlabs_model_id: str = Field(default="eleven_v4", description="ElevenLabs TTS model; falls back to eleven_multilingual_v2 if rejected")
    elevenlabs_max_chars: int = Field(default=5000, description="Per-job character cap, enforced before any call")
    elevenlabs_voice_settings: str = Field(default="", description="JSON overrides for the voice-settings presets")
    hf_api_key: str = Field(default="", description="Higgsfield API key id")
    hf_api_secret: str = Field(default="", description="Higgsfield API key secret")
    higgsfield_base_url: str = Field(default="https://api.higgsfield.ai", description="Higgsfield API base")
    higgsfield_max_seconds: int = Field(default=60, description="Per-job cap on generated seconds, enforced before submit")
    higgsfield_avatar_model: str = Field(default="wan/v2.7/image-to-video", description="Talking-head model id (image + audio)")
    higgsfield_image_to_video_model: str = Field(default="wan/v2.7/image-to-video", description="Photo-motion model id")
    higgsfield_text_to_video_model: str = Field(default="", description="Unverified — empty keeps text_to_video off")
    higgsfield_resolution: str = Field(default="720p", description="720p or 1080p")
    higgsfield_concurrency: int = Field(default=2, description="Max in-flight Higgsfield jobs (over-limit returns 400)")
    higgsfield_poll_timeout_s: int = Field(default=1200, description="Give up polling (never resubmit) after this long")
    podclick_media_preference: str = Field(default="", description="Comma list of media providers in preference order")
    podclick_public_media_base_url: str = Field(default="", description="Public https origin that serves /api/agents/jobs/{id}/files/* — Higgsfield fetches photos/audio from here")
    podclick_agents_disabled: str = Field(default="", description="1 = Crew kill switch (every agent not_built)")

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
