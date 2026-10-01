"""
services/media/base.py — the MediaProvider interface (AGENTS_HUB_SPEC §4.1).

Runners only ever see this module's types: `SpeechRequest`, `GenerationRequest`,
`MediaAsset`, `ProviderJob` and the `MediaProviderError` family. Everything
vendor-specific (URLs, auth headers, status vocabularies) lives in exactly one
adapter file per provider.

Three seams exist on purpose so tests can prove "zero network calls" and
"never retried" instead of assuming them:

- `HTTP_TRANSPORT` — when set (tests), every provider HTTP client uses it.
- `sleep` — every backoff goes through it, so tests can record the delays.
- `setting()` — every key/limit lookup goes through it (config.settings first,
  `os.getenv` only as a fallback — spec constraint 6: `.env` is not exported to
  the process environment, so `os.getenv` alone silently returns "").

Python 3.9: Optional/List/Dict only, no `X | None`.
"""

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional

import httpx

CAPABILITIES: FrozenSet[str] = frozenset(
    {"tts", "voices", "avatar_video", "image_to_video", "text_to_video", "text_to_image", "ken_burns"}
)

# ── Test seams ────────────────────────────────────────────────────────────────

HTTP_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None


async def _real_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


sleep = _real_sleep


def setting(name: str, default: Any = None) -> Any:
    """Read a setting: `config.settings.<name>` first, then `os.getenv(NAME)`.

    Empty strings count as unset. Never logs or returns anything but the value
    to the caller — secrets stay inside the adapter that asked.
    """
    value: Any = None
    try:
        from config import settings as _settings  # lazy: config validates DB/Redis env on import
        value = getattr(_settings, name, None)
    except Exception:
        value = None
    if value is None or value == "":
        value = os.getenv(name.upper())
    if value is None or value == "":
        return default
    return value


def setting_int(name: str, default: int) -> int:
    raw = setting(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def http_client(timeout: float = 60.0) -> httpx.AsyncClient:
    """Every provider request goes through a client built here (one seam for tests)."""
    return httpx.AsyncClient(transport=HTTP_TRANSPORT, timeout=timeout)


# ── Errors (spec §4.1 table) ──────────────────────────────────────────────────

class MediaProviderError(Exception):
    """Base for every provider failure. `user_message` is Brick-voice, secret-free."""

    def __init__(self, message: str, provider: str = "", user_message: Optional[str] = None):
        super().__init__(message)
        self.provider = provider
        self.user_message = user_message or message


class ProviderNotConfigured(MediaProviderError):
    """Key (or voice id / model id) missing. Raised before any network call."""


class ProviderAuthError(MediaProviderError):
    """401/403. Never retried."""


class ProviderQuotaError(MediaProviderError):
    """Out of credits / quota. Never retried."""


class ProviderRateLimited(MediaProviderError):
    """Transient 429 that outlived its backoff budget."""


class ProviderError(MediaProviderError):
    """5xx / network / unexpected response."""


class ProviderUncertainError(ProviderError):
    """A write was sent and we did not hear back. NEVER auto-retried: a resubmit
    can double-charge. The user checks the provider dashboard first."""


class ProviderRejected(ProviderError):
    """The provider finished the job as failed / nsfw / canceled."""


class ProviderCapExceeded(MediaProviderError):
    """A per-job cap (characters, seconds) would be exceeded. Raised before any call."""


class CapabilityNotSupported(MediaProviderError):
    """Programming error: asked a provider for a capability it does not have."""


# ── Data shapes ───────────────────────────────────────────────────────────────

@dataclass
class Voice:
    voice_id: str
    name: str
    category: str = ""


@dataclass
class SpeechRequest:
    text: str
    voice_id: Optional[str] = None
    model_id: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None   # stability, similarity_boost, style, use_speaker_boost, speed
    with_timestamps: bool = False


@dataclass
class MediaAsset:
    path: Path
    mime: str
    duration_s: Optional[float] = None
    alignment: Optional[List[Dict[str, Any]]] = None   # [{word, start, end}]
    usage: Optional[Dict[str, Any]] = None             # {unit, amount}
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationRequest:
    capability: str
    prompt: str
    duration_s: int
    image_url: Optional[str] = None      # public URL the provider fetches
    audio_url: Optional[str] = None      # public URL of the driving audio
    source_image: Optional[Path] = None  # local file behind image_url (likeness check)
    aspect: str = "9:16"
    resolution: Optional[str] = None


@dataclass
class ProviderJob:
    provider: str
    remote_id: str
    status: str
    submitted_at: float
    request_hash: str
    status_url: Optional[str] = None
    cancel_url: Optional[str] = None
    result_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ProviderHealth:
    ok: bool
    reason: str = ""
    quota_remaining: Optional[int] = None


# ── Interface ─────────────────────────────────────────────────────────────────

class MediaProvider(ABC):
    name: str = ""
    capabilities: FrozenSet[str] = frozenset()

    @abstractmethod
    def is_configured(self) -> bool:
        """Keys present. Cheap, no network — GET /api/agents calls this per request."""

    async def health(self) -> ProviderHealth:
        # No live probe in wave 2: the only verified endpoints are billable or
        # create resources, and health must never run in a poll loop (§4.2).
        if self.is_configured():
            return ProviderHealth(ok=True, reason="key present (not live-checked)")
        return ProviderHealth(ok=False, reason="not configured")

    def _unsupported(self, capability: str) -> CapabilityNotSupported:
        return CapabilityNotSupported(
            f"{self.name} does not support {capability}", provider=self.name
        )

    async def list_voices(self) -> List[Voice]:
        raise self._unsupported("voices")

    async def synthesize_speech(self, req: SpeechRequest, dest: Path) -> MediaAsset:
        raise self._unsupported("tts")

    async def submit(self, req: GenerationRequest) -> ProviderJob:
        raise self._unsupported(req.capability)

    async def poll(self, job: ProviderJob) -> ProviderJob:
        raise self._unsupported("poll")

    async def fetch(self, job: ProviderJob, dest: Path) -> MediaAsset:
        raise self._unsupported("fetch")

    async def generate(self, req: GenerationRequest, dest: Path) -> MediaAsset:
        """submit → poll until terminal → fetch, under the provider's concurrency cap."""
        raise self._unsupported(req.capability)


def public_file_url(job_id: str, name: str) -> Optional[str]:
    """Public https URL of a job output file, or None when no public origin is set.

    Higgsfield FETCHES its inputs (photo, driving audio), so they must be
    reachable from the internet. PodClick is local-only, so this stays None
    until PODCLICK_PUBLIC_MEDIA_BASE_URL points at a tunnel/host that serves
    lane A's `GET /api/agents/jobs/{id}/files/{name}`.
    """
    origin = str(setting("podclick_public_media_base_url", "") or "").rstrip("/")
    if not origin.startswith("https://"):
        return None
    return f"{origin}/api/agents/jobs/{job_id}/files/{name}"


def as_user_error(exc: MediaProviderError) -> RuntimeError:
    """Runner-facing: the Brick-voice message, no keys, no provider internals."""
    return RuntimeError(exc.user_message)


def atomic_write_bytes(dest: Path, data: bytes) -> None:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(dest))
