"""
services/media — outside creation tools behind one interface (AGENTS_HUB_SPEC §4).

    from services.media import get_provider
    tts = get_provider("tts")        # None when ElevenLabs isn't configured
"""

from services.media.base import (  # noqa: F401
    CAPABILITIES,
    CapabilityNotSupported,
    GenerationRequest,
    MediaAsset,
    MediaProvider,
    MediaProviderError,
    ProviderAuthError,
    ProviderCapExceeded,
    ProviderError,
    ProviderHealth,
    ProviderJob,
    ProviderNotConfigured,
    ProviderQuotaError,
    ProviderRateLimited,
    ProviderRejected,
    ProviderUncertainError,
    SpeechRequest,
    Voice,
)
from services.media.registry import get_provider, missing_requirements  # noqa: F401
