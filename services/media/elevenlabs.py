"""
services/media/elevenlabs.py — ElevenLabs adapter (spec §4.3, verified facts below).

VERIFIED API FACTS (lane D brief, 2026-10-01 — these replace the spec's §4.3/§4.6
assumptions):

- TTS: ``POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128``
  header ``xi-api-key``, JSON ``{text, model_id, voice_settings: {stability,
  similarity_boost, style, use_speaker_boost, speed}}``. Returns mp3 bytes
  synchronously.
- Current model id ``eleven_v4``; fall back to ``eleven_multilingual_v2`` when the
  account/endpoint rejects the model (a definite 4xx rejection — no characters
  are charged, so trying the fallback is safe).
- Instant voice clone: ``POST /v1/voices/add`` multipart.

NOT VERIFIED, therefore NOT USED: ``/with-timestamps`` (alignment is estimated
locally from the measured duration instead), ``/v1/user/subscription``
(health is key-presence only). ``GET /v1/voices`` is implemented for the voice
picker but flagged unverified; nothing in a run depends on it.

Cost discipline:
- ``ELEVENLABS_MAX_CHARS`` is enforced before any request.
- A missing key or voice id raises ``ProviderNotConfigured`` before any request.
- A timeout / dropped connection after the POST is *uncertain* (characters may
  have been spent) → ``ProviderUncertainError``, never retried.
- 5xx on a write is not retried either (spec §4.1: retries on reads/polls only).
- Transient 429 backs off 5 s / 15 s / 45 s (a 429 is a definite refusal).
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from services.media import base
from services.media.base import (
    MediaAsset,
    MediaProvider,
    ProviderAuthError,
    ProviderCapExceeded,
    ProviderError,
    ProviderNotConfigured,
    ProviderQuotaError,
    ProviderRateLimited,
    ProviderUncertainError,
    SpeechRequest,
    Voice,
)

BASE_URL = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_v4"
FALLBACK_MODEL = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"
CHUNK_CHARS = 2500
DEFAULT_MAX_CHARS = 5000
RATE_LIMIT_BACKOFF = (5.0, 15.0, 45.0)
ENV_KEY = "ELEVENLABS_API_KEY"

# Purpose presets (spec §1.4 Voiceover `purpose`). Narrative delivery, never
# monotone: moderate stability, some style. Tunable via ELEVENLABS_VOICE_SETTINGS.
PRESETS: Dict[str, Dict[str, Any]] = {
    "narration": {"stability": 0.45, "similarity_boost": 0.80, "style": 0.30,
                  "use_speaker_boost": True, "speed": 1.0},
    "intro":     {"stability": 0.40, "similarity_boost": 0.80, "style": 0.45,
                  "use_speaker_boost": True, "speed": 1.0},
    "outro":     {"stability": 0.50, "similarity_boost": 0.80, "style": 0.30,
                  "use_speaker_boost": True, "speed": 0.98},
    "ad read":   {"stability": 0.55, "similarity_boost": 0.85, "style": 0.35,
                  "use_speaker_boost": True, "speed": 1.02},
}


def voice_settings_for(purpose: Optional[str]) -> Dict[str, Any]:
    """Preset for `purpose` (default narration), merged with ELEVENLABS_VOICE_SETTINGS.

    The override JSON may be flat (`{"stability": 0.6}`, applies to every purpose)
    or keyed by purpose (`{"intro": {"style": 0.5}}`). Bad JSON is ignored.
    """
    key = (purpose or "narration").strip().lower()
    out = dict(PRESETS.get(key, PRESETS["narration"]))
    raw = base.setting("elevenlabs_voice_settings", "")
    if raw:
        try:
            overrides = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError):
            overrides = {}
        if isinstance(overrides, dict):
            flat = {k: v for k, v in overrides.items() if k in out}
            out.update(flat)
            scoped = overrides.get(key)
            if isinstance(scoped, dict):
                out.update({k: v for k, v in scoped.items() if k in out})
    return out


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def split_for_tts(text: str, limit: int = CHUNK_CHARS) -> List[str]:
    """Split on sentence boundaries into chunks of at most `limit` characters.

    A single sentence longer than `limit` is split on word boundaries.
    """
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    pieces: List[str] = []
    for sentence in _SENTENCE_END.split(text):
        if len(sentence) <= limit:
            pieces.append(sentence)
            continue
        current = ""
        for word in sentence.split():
            candidate = (current + " " + word).strip()
            if len(candidate) > limit and current:
                pieces.append(current)
                current = word
            else:
                current = candidate
        if current:
            pieces.append(current)
    chunks: List[str] = []
    current = ""
    for piece in pieces:
        candidate = (current + " " + piece).strip()
        if len(candidate) > limit and current:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _body_text(resp: httpx.Response) -> str:
    try:
        return resp.text[:2000].lower()
    except Exception:
        return ""


class ElevenLabsProvider(MediaProvider):
    name = "elevenlabs"
    capabilities = frozenset({"tts", "voices"})

    # ── config (all through base.setting; nothing cached, nothing logged) ──
    def _key(self) -> str:
        return str(base.setting("elevenlabs_api_key", "") or "")

    def is_configured(self) -> bool:
        return bool(self._key())

    @property
    def max_chars(self) -> int:
        return base.setting_int("elevenlabs_max_chars", DEFAULT_MAX_CHARS)

    @property
    def default_voice_id(self) -> str:
        return str(base.setting("elevenlabs_voice_id", "") or "")

    @property
    def model_id(self) -> str:
        return str(base.setting("elevenlabs_model_id", DEFAULT_MODEL) or DEFAULT_MODEL)

    def _headers(self) -> Dict[str, str]:
        return {"xi-api-key": self._key()}

    def _require_key(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "ElevenLabs key missing", provider=self.name,
                user_message=f"ElevenLabs isn't connected — add {ENV_KEY} to .env and restart the server.",
            )

    def check_text(self, text: str) -> None:
        """Cap check, callable by runners before they spend anything else."""
        if len(text) > self.max_chars:
            raise ProviderCapExceeded(
                f"{len(text)} chars > cap {self.max_chars}", provider=self.name,
                user_message=(f"That script is {len(text):,} characters — the cap is {self.max_chars:,}. "
                              f"Trim it or raise ELEVENLABS_MAX_CHARS."),
            )

    # ── tts ──
    async def synthesize_speech(self, req: SpeechRequest, dest: Path) -> MediaAsset:
        self._require_key()
        text = (req.text or "").strip()
        if not text:
            raise ValueError("Nothing to say — the script is empty.")
        self.check_text(text)
        voice_id = req.voice_id or self.default_voice_id
        if not voice_id:
            raise ProviderNotConfigured(
                "no voice id", provider=self.name,
                user_message="Pick a voice, or set ELEVENLABS_VOICE_ID in .env and restart.",
            )
        settings = req.settings or voice_settings_for(None)
        model = req.model_id or self.model_id
        chunks = split_for_tts(text, CHUNK_CHARS)

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part_paths: List[Path] = []
        used_model = model
        async with base.http_client(timeout=120.0) as client:
            for i, chunk in enumerate(chunks):
                audio, used_model = await self._tts_once(client, chunk, voice_id, used_model, settings)
                if len(chunks) == 1:
                    base.atomic_write_bytes(dest, audio)
                else:
                    part = dest.with_name(f"{dest.stem}.part{i:02d}.mp3")
                    base.atomic_write_bytes(part, audio)
                    part_paths.append(part)

        if part_paths:
            from services.media import ffmpeg_local
            await ffmpeg_local.concat_audio(part_paths, dest)
            for p in part_paths:
                p.unlink(missing_ok=True)

        duration: Optional[float] = None
        try:
            from services.media import ffmpeg_local
            duration = await ffmpeg_local.probe_duration(dest)
        except Exception:
            duration = None

        return MediaAsset(
            path=dest, mime="audio/mpeg", duration_s=duration, alignment=None,
            usage={"provider": self.name, "unit": "characters", "amount": len(text)},
            meta={"model_id": used_model, "chunks": len(chunks)},
        )

    async def _tts_once(self, client: httpx.AsyncClient, text: str, voice_id: str,
                        model: str, settings: Dict[str, Any]):
        """One chunk. Returns (mp3 bytes, model actually used)."""
        models = [model] if model == FALLBACK_MODEL else [model, FALLBACK_MODEL]
        url = f"{BASE_URL}/v1/text-to-speech/{voice_id}"
        for model_id in models:
            payload = {"text": text, "model_id": model_id, "voice_settings": settings}
            rate_tries = 0
            while True:
                try:
                    resp = await client.post(url, params={"output_format": OUTPUT_FORMAT},
                                             headers=self._headers(), json=payload)
                except httpx.ConnectError as exc:
                    raise ProviderError(
                        f"connect failed: {type(exc).__name__}", provider=self.name,
                        user_message="Couldn't reach ElevenLabs — check the connection and run it again.",
                    )
                except httpx.TransportError as exc:  # timeout / reset after send: uncertain
                    raise ProviderUncertainError(
                        f"no response: {type(exc).__name__}", provider=self.name,
                        user_message=("Didn't hear back from ElevenLabs — check your ElevenLabs "
                                      "history before running again."),
                    )
                status = resp.status_code
                if status == 200:
                    if not resp.content:
                        raise ProviderError("empty audio", provider=self.name,
                                            user_message="ElevenLabs sent back an empty track.")
                    return resp.content, model_id
                body = _body_text(resp)
                if status in (401, 403):
                    if "quota" in body or "credit" in body:
                        raise self._quota()
                    raise ProviderAuthError(
                        f"auth {status}", provider=self.name,
                        user_message=f"ElevenLabs turned the key down — check {ENV_KEY} and restart.",
                    )
                if status == 402:
                    raise self._quota()
                if status == 429:
                    if "quota" in body or "credit" in body:
                        raise self._quota()
                    if rate_tries >= len(RATE_LIMIT_BACKOFF):
                        raise ProviderRateLimited(
                            "rate limited", provider=self.name,
                            user_message="ElevenLabs is busy — give it a minute and run it again.",
                        )
                    await base.sleep(RATE_LIMIT_BACKOFF[rate_tries])
                    rate_tries += 1
                    continue
                if status in (400, 404, 422) and "model" in body and model_id != FALLBACK_MODEL:
                    break  # model rejected outright (nothing charged) → try the fallback model
                raise ProviderError(
                    f"http {status}", provider=self.name,
                    user_message=f"ElevenLabs refused the request (HTTP {status}).",
                )
        raise ProviderError("no usable model", provider=self.name,
                            user_message="ElevenLabs rejected every voice model we tried.")

    def _quota(self) -> ProviderQuotaError:
        return ProviderQuotaError("quota", provider=self.name,
                                  user_message="Out of ElevenLabs characters for this billing period.")

    # ── voices ──
    async def list_voices(self) -> List[Voice]:
        """GET /v1/voices — UNVERIFIED path/shape (not in the lane-D verified set).
        Read-only, so transient failures retry up to 3 times."""
        self._require_key()
        last_exc: Optional[Exception] = None
        async with base.http_client(timeout=30.0) as client:
            for attempt in range(3):
                try:
                    resp = await client.get(f"{BASE_URL}/v1/voices", headers=self._headers())
                except httpx.TransportError as exc:
                    last_exc = exc
                    await base.sleep(2.0 * (attempt + 1))
                    continue
                if resp.status_code in (401, 403):
                    raise ProviderAuthError("auth", provider=self.name,
                                            user_message=f"ElevenLabs turned the key down — check {ENV_KEY} and restart.")
                if resp.status_code >= 500:
                    last_exc = ProviderError(f"http {resp.status_code}", provider=self.name)
                    await base.sleep(2.0 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    raise ProviderError(f"http {resp.status_code}", provider=self.name,
                                        user_message="Couldn't load your ElevenLabs voices.")
                data = resp.json()
                return [Voice(voice_id=v.get("voice_id", ""), name=v.get("name", ""),
                              category=v.get("category", "") or "")
                        for v in data.get("voices", []) if v.get("voice_id")]
        raise ProviderError(f"voices unavailable: {last_exc}", provider=self.name,
                            user_message="Couldn't load your ElevenLabs voices.")

    async def clone_voice(self, name: str, files: List[Path], description: str = "") -> str:
        """Instant voice clone — POST /v1/voices/add (multipart, verified).

        Creates a resource on the account, so it is never retried. Returns the new
        voice_id. The caller decides where (if anywhere) to remember it.
        """
        self._require_key()
        if not name.strip() or not files:
            raise ValueError("A voice clone needs a name and at least one sample file.")
        multipart = [("files", (Path(f).name, Path(f).read_bytes(), "audio/mpeg")) for f in files]
        data = {"name": name.strip()}
        if description:
            data["description"] = description
        async with base.http_client(timeout=180.0) as client:
            try:
                resp = await client.post(f"{BASE_URL}/v1/voices/add", headers=self._headers(),
                                         data=data, files=multipart)
            except httpx.TransportError as exc:
                raise ProviderUncertainError(
                    f"no response: {type(exc).__name__}", provider=self.name,
                    user_message="Didn't hear back from ElevenLabs — check your Voices page before trying again.",
                )
        if resp.status_code in (401, 403):
            raise ProviderAuthError("auth", provider=self.name,
                                    user_message=f"ElevenLabs turned the key down — check {ENV_KEY} and restart.")
        if resp.status_code != 200:
            raise ProviderError(f"http {resp.status_code}", provider=self.name,
                                user_message=f"ElevenLabs couldn't clone that voice (HTTP {resp.status_code}).")
        voice_id = (resp.json() or {}).get("voice_id")
        if not voice_id:
            raise ProviderUncertainError("no voice_id in response", provider=self.name,
                                         user_message="ElevenLabs answered without a voice id — check your Voices page.")
        return str(voice_id)
