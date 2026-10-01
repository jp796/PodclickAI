"""
services/media/registry.py — capability → provider lookup with fail-soft (spec §4.2).

`get_provider(capability)` returns the first CONFIGURED provider that supports
the capability, in preference order (`PODCLICK_MEDIA_PREFERENCE` comma list,
else the built-in order). It never raises for a missing key: it returns None
and the runner decides whether that is a hard requirement (needs_setup) or a
soft one (fallback + warning).

Nothing here touches the network — `is_configured()` is key presence only — so
`GET /api/agents` can compute `needs_setup` on every request.
"""

import shutil
from typing import Callable, Dict, Iterable, List, Optional

from services.media import base
from services.media.base import CAPABILITIES, MediaProvider
from services.media.elevenlabs import ElevenLabsProvider
from services.media.ffmpeg_local import FfmpegLocalProvider
from services.media.higgsfield import HiggsfieldProvider

BUILTIN_ORDER = ("elevenlabs", "higgsfield", "ffmpeg_local")

_FACTORIES: Dict[str, Callable[[], MediaProvider]] = {
    "elevenlabs": ElevenLabsProvider,
    "higgsfield": HiggsfieldProvider,
    "ffmpeg_local": FfmpegLocalProvider,
}


def _order() -> List[str]:
    raw = str(base.setting("podclick_media_preference", "") or "")
    preferred = [p.strip() for p in raw.split(",") if p.strip() in _FACTORIES]
    rest = [p for p in BUILTIN_ORDER if p in _FACTORIES and p not in preferred]
    extra = [p for p in _FACTORIES if p not in preferred and p not in rest]
    return preferred + rest + extra


def all_providers() -> List[MediaProvider]:
    """Fresh instances in preference order (instances read settings at build time)."""
    return [_FACTORIES[name]() for name in _order()]


def get_provider(capability: str) -> Optional[MediaProvider]:
    if capability not in CAPABILITIES:
        raise ValueError(f"unknown media capability: {capability}")
    for provider in all_providers():
        if capability in provider.capabilities and provider.is_configured():
            return provider
    return None


def _pexels_ready() -> bool:
    return bool(base.setting("pexels_api_key", ""))


def _ffmpeg_ready() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# `AgentSpec.requires` names this module can answer. Others (youtube_api, ghl,
# openai, anthropic) belong to lane A's checks and are ignored here.
REQUIREMENT_CHECKS: Dict[str, Callable[[], bool]] = {
    "elevenlabs": lambda: ElevenLabsProvider().is_configured(),
    "higgsfield": lambda: HiggsfieldProvider().is_configured(),
    "pexels": _pexels_ready,
    "ffmpeg": _ffmpeg_ready,
}


def missing_requirements(requires: Iterable[str]) -> List[str]:
    """The subset of `requires` that is known here and not configured. No network."""
    missing: List[str] = []
    for name in requires:
        check = REQUIREMENT_CHECKS.get(name)
        if check is not None and not check():
            missing.append(name)
    return missing
