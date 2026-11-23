"""
services/media/heygen.py — HeyGen adapter: your digital twin, lip-synced to a
voice track PodClick already recorded (capability ``twin_video``).

FINDINGS NOTE. Read from developers.heygen.com on 2026-11-16. No live call was
run from this repo — the account key lives on JP's machine, not here.

- v1/v2 endpoints (``/v2/video/generate``, ``upload.heygen.com/v1/asset``) are
  documented as removed after 2026-10-31. This adapter uses **v3 only**.
- Base ``https://api.heygen.com``. Auth header ``x-api-key: <key>``.
- Upload audio: ``POST /v3/assets`` multipart, field ``file`` (mp3 / wav / m4a,
  32 MB for audio) → ``{data: {asset_id, url, mime_type, size_bytes}}``.
- Create: ``POST /v3/videos`` with
  ``{type: "avatar", avatar_id, audio_asset_id, aspect_ratio, resolution,
  engine?: {type}}`` → ``{data: {video_id, status}}``. ``audio_asset_id`` and
  ``script`` are mutually exclusive; we always send audio, so the voice is the
  ElevenLabs clone and never HeyGen's.
- Poll: ``GET /v3/videos/{video_id}`` → ``data.status`` in
  ``pending | processing | completed | failed`` (create answers ``waiting``).
  Completed carries ``video_url`` (presigned), ``subtitle_url`` (SRT) and
  ``duration``; failed carries ``failure_code`` / ``failure_message``.
- One request renders up to 30 minutes of audio. ``Idempotency-Key`` is honored
  for 24 h on both writes.
- Engines: ``avatar_iii`` | ``avatar_iv`` (HeyGen's default) | ``avatar_v`` |
  ``heygen-avatar-6``. Which ones a twin supports is on the look
  (``supported_api_engines``, ``GET /v3/avatars/looks``).

Why this needs no public URL: the audio is UPLOADED to HeyGen, so — unlike the
Higgsfield talking-head path — nothing has to be reachable from the internet.

Cost discipline (same rules as higgsfield.py): the seconds cap and the file
checks run before any network call; the create call is NEVER retried (timeout,
reset, 5xx and a reply without ``video_id`` are all *uncertain*); only polls and
downloads retry. The key is only ever sent to the configured HeyGen host.

Likeness: the twin id comes from settings (``HEYGEN_AVATAR_ID``), never from a
request. The twin itself is created and consented inside HeyGen, by the person
it depicts. A request cannot point this adapter at anyone else's face.
"""

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from services.media import base
from services.media.base import (
    GenerationRequest,
    MediaAsset,
    MediaProvider,
    ProviderAuthError,
    ProviderCapExceeded,
    ProviderError,
    ProviderJob,
    ProviderNotConfigured,
    ProviderQuotaError,
    ProviderRateLimited,
    ProviderRejected,
    ProviderUncertainError,
)

DEFAULT_BASE_URL = "https://api.heygen.com"
DEFAULT_RESOLUTION = "1080p"
DEFAULT_MAX_SECONDS = 600
DEFAULT_POLL_TIMEOUT_S = 3600
HARD_MAX_SECONDS = 30 * 60            # HeyGen renders at most 30 min of audio per request
MAX_AUDIO_BYTES = 32 * 1024 * 1024
AUDIO_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}
ASPECTS = ("16:9", "9:16", "1:1", "4:5", "5:4")
RESOLUTIONS = ("720p", "1080p", "4k")
ENGINES = ("avatar_iii", "avatar_iv", "avatar_v", "heygen-avatar-6")

POLL_START_S = 5.0
POLL_FACTOR = 1.5
POLL_CAP_S = 30.0

ENV_KEY = "HEYGEN_API_KEY"
ENV_AVATAR = "HEYGEN_AVATAR_ID"

_AUTH_MSG = f"HeyGen turned the key down — check {ENV_KEY} and restart."
_UNCERTAIN_MSG = "Didn't hear back from HeyGen — check your HeyGen videos before running again."
_QUOTA_WORDS = ("credit", "quota", "insufficient", "balance", "payment")


def _error_code(resp: httpx.Response) -> str:
    """HeyGen's machine code + message, lowercased, for classification only."""
    try:
        err = (resp.json() or {}).get("error") or {}
    except ValueError:
        return ""
    if not isinstance(err, dict):
        return str(err).lower()
    return f"{err.get('code') or ''} {err.get('message') or ''}".lower()


class HeyGenProvider(MediaProvider):
    name = "heygen"
    capabilities = frozenset({"twin_video"})

    # ── config (all through base.setting; nothing cached, nothing logged) ──
    def _key(self) -> str:
        return str(base.setting("heygen_api_key", "") or "")

    def is_configured(self) -> bool:
        return bool(self._key())

    @property
    def avatar_id(self) -> str:
        return str(base.setting("heygen_avatar_id", "") or "").strip()

    @property
    def base_url(self) -> str:
        return str(base.setting("heygen_base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")

    @property
    def max_seconds(self) -> int:
        return min(base.setting_int("heygen_max_seconds", DEFAULT_MAX_SECONDS), HARD_MAX_SECONDS)

    @property
    def engine(self) -> str:
        return str(base.setting("heygen_engine", "") or "").strip()

    @property
    def resolution(self) -> str:
        return str(base.setting("heygen_resolution", DEFAULT_RESOLUTION) or DEFAULT_RESOLUTION).strip()

    def _headers(self) -> Dict[str, str]:
        return {"x-api-key": self._key()}

    # ── checks (all before any network) ──
    def check_ready(self) -> None:
        """Key + twin id + sane engine/resolution. Runners call this in preflight."""
        if not self.is_configured():
            raise ProviderNotConfigured(
                "HeyGen key missing", provider=self.name,
                user_message=f"HeyGen isn't connected — add {ENV_KEY} to .env and restart.")
        if not self.avatar_id:
            raise ProviderNotConfigured(
                "HeyGen avatar id missing", provider=self.name,
                user_message=(f"No twin picked — set {ENV_AVATAR} in .env to your HeyGen twin's look id "
                              f"and restart."))
        if self.engine and self.engine not in ENGINES:
            raise ProviderNotConfigured(
                f"unknown engine {self.engine!r}", provider=self.name,
                user_message="HEYGEN_ENGINE has to be one of: " + ", ".join(ENGINES) + ".")
        if self.resolution not in RESOLUTIONS:
            raise ProviderNotConfigured(
                f"unknown resolution {self.resolution!r}", provider=self.name,
                user_message="HEYGEN_RESOLUTION has to be 720p, 1080p or 4k.")
        if urlparse(self.base_url).scheme != "https":
            raise ProviderNotConfigured(
                "base url not https", provider=self.name,
                user_message="HEYGEN_BASE_URL has to be an https address.")

    def check_seconds(self, total_s: float) -> None:
        """Job-level cap; runners call this before spending anything."""
        if total_s > self.max_seconds:
            raise ProviderCapExceeded(
                f"{total_s:.1f}s > cap {self.max_seconds}s", provider=self.name,
                user_message=(f"That's {total_s:.0f}s of twin video — the cap is {self.max_seconds}s. "
                              f"Shorten it or raise HEYGEN_MAX_SECONDS."))

    def _check_audio(self, req: GenerationRequest) -> Path:
        if req.capability not in self.capabilities:
            raise self._unsupported(req.capability)
        audio = req.source_audio
        if audio is None or not Path(audio).is_file():
            raise ValueError("HeyGen needs the recorded voice track (source_audio) on disk")
        audio = Path(audio)
        if audio.suffix.lower() not in AUDIO_MIME:
            raise ValueError("The voice track has to be mp3, wav or m4a")
        if audio.stat().st_size > MAX_AUDIO_BYTES:
            raise ProviderCapExceeded(
                "audio over 32 MB", provider=self.name,
                user_message="That voice track is over HeyGen's 32 MB limit — shorten the script.")
        if req.aspect not in ASPECTS:
            raise ValueError(f"aspect must be one of {ASPECTS}")
        self.check_seconds(float(req.duration_s))
        return audio

    def build_payload(self, req: GenerationRequest, audio_asset_id: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "type": "avatar",
            "avatar_id": self.avatar_id,
            "audio_asset_id": audio_asset_id,
            "aspect_ratio": req.aspect,
            "resolution": req.resolution or self.resolution,
        }
        if self.engine:
            payload["engine"] = {"type": self.engine}
        return payload

    # ── error mapping shared by both writes ──
    def _raise_for_write(self, resp: httpx.Response, what: str) -> None:
        status = resp.status_code
        if status in (200, 201, 202):
            return
        detail = _error_code(resp)
        if status in (401, 403):
            raise ProviderAuthError(f"{what} auth {status}", provider=self.name, user_message=_AUTH_MSG)
        if status == 402 or any(w in detail for w in _QUOTA_WORDS):
            raise ProviderQuotaError(f"{what} {status}: {detail[:200]}", provider=self.name,
                                     user_message="Out of HeyGen credits — top up, then run it again.")
        if status == 429:
            raise ProviderRateLimited(f"{what} 429", provider=self.name,
                                      user_message="HeyGen is rate-limiting us — wait a minute and run it again.")
        if status == 409 and "request_in_progress" in detail:
            raise ProviderUncertainError(f"{what} 409 in progress", provider=self.name,
                                         user_message=_UNCERTAIN_MSG)
        if status == 409:
            raise ProviderError(f"{what} 409: {detail[:200]}", provider=self.name,
                                user_message="Your twin is still training on HeyGen — run it again once it's ready.")
        if status >= 500:
            raise ProviderUncertainError(f"{what} {status}", provider=self.name, user_message=_UNCERTAIN_MSG)
        if "avatar_not_usable" in detail:
            raise ProviderError(f"{what} {status}: {detail[:200]}", provider=self.name,
                                user_message=(f"HeyGen says that twin can't be used — check {ENV_AVATAR} and that "
                                              f"its consent is approved in HeyGen."))
        raise ProviderError(f"{what} {status}: {detail[:200]}", provider=self.name,
                            user_message=(f"HeyGen refused the job (HTTP {status}) — check the twin id, the engine "
                                          f"your twin supports, and the voice track."))

    # ── upload / submit / poll / fetch ──
    async def upload_audio(self, audio: Path) -> str:
        """Voice track → HeyGen asset id. Not billable; idempotent on the file's hash."""
        self.check_ready()
        audio = Path(audio)
        data = audio.read_bytes()
        headers = dict(self._headers())
        headers["Idempotency-Key"] = "pc-audio-" + hashlib.sha256(data).hexdigest()[:48]
        files = {"file": (audio.name, data, AUDIO_MIME.get(audio.suffix.lower(), "audio/mpeg"))}
        async with base.http_client(timeout=300.0) as client:
            try:
                resp = await client.post(f"{self.base_url}/v3/assets", headers=headers, files=files)
            except httpx.TransportError as exc:
                raise ProviderError(f"upload: {type(exc).__name__}", provider=self.name,
                                    user_message="Couldn't get the voice track up to HeyGen — check the "
                                                 "connection and run it again.")
        self._raise_for_write(resp, "upload")
        try:
            asset_id = ((resp.json() or {}).get("data") or {}).get("asset_id")
        except ValueError:
            asset_id = None
        if not asset_id:
            raise ProviderError("upload: no asset_id", provider=self.name,
                                user_message="HeyGen took the voice track but didn't hand back an id — run it again.")
        return str(asset_id)

    async def submit(self, req: GenerationRequest, audio_asset_id: str = "") -> ProviderJob:
        self.check_ready()
        if not audio_asset_id:
            raise ValueError("submit needs the uploaded audio's asset id")
        payload = self.build_payload(req, audio_asset_id)
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        headers = dict(self._headers())
        headers["Idempotency-Key"] = "pc-video-" + request_hash[:48]
        async with base.http_client(timeout=60.0) as client:
            try:
                resp = await client.post(f"{self.base_url}/v3/videos", headers=headers, json=payload)
            except httpx.TransportError as exc:
                raise ProviderUncertainError(f"submit: {type(exc).__name__}", provider=self.name,
                                             user_message=_UNCERTAIN_MSG)
        self._raise_for_write(resp, "submit")
        try:
            data = (resp.json() or {}).get("data") or {}
        except ValueError:
            data = {}
        video_id = data.get("video_id") if isinstance(data, dict) else None
        if not video_id:
            raise ProviderUncertainError("submit: no video_id", provider=self.name, user_message=_UNCERTAIN_MSG)
        return ProviderJob(
            provider=self.name, remote_id=str(video_id), status=str(data.get("status") or "pending"),
            submitted_at=time.time(), request_hash=request_hash,
            status_url=f"{self.base_url}/v3/videos/{video_id}",
        )

    async def poll(self, job: ProviderJob) -> ProviderJob:
        self.check_ready()
        url = f"{self.base_url}/v3/videos/{job.remote_id}"     # built here: the key never follows a returned link
        last: Optional[str] = None
        async with base.http_client(timeout=30.0) as client:
            for attempt in range(3):
                try:
                    resp = await client.get(url, headers=self._headers())
                except httpx.TransportError as exc:
                    last = type(exc).__name__
                    await base.sleep(2.0 * (attempt + 1))
                    continue
                if resp.status_code in (401, 403):
                    raise ProviderAuthError("auth", provider=self.name, user_message=_AUTH_MSG)
                if resp.status_code >= 500 or resp.status_code == 429:
                    last = f"http {resp.status_code}"
                    await base.sleep(2.0 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    raise ProviderError(f"poll {resp.status_code}", provider=self.name,
                                        user_message="Lost track of the HeyGen render — check your HeyGen videos.")
                try:
                    data = (resp.json() or {}).get("data") or {}
                except ValueError:
                    data = {}
                job.status = str(data.get("status") or job.status)
                if job.status == "completed":
                    job.result_url = data.get("video_url")
                    job.meta = {"subtitle_url": data.get("subtitle_url"), "duration": data.get("duration")}
                elif job.status == "failed":
                    job.error = str(data.get("failure_code") or data.get("failure_message") or "failed")
                return job
        raise ProviderError(f"poll failed: {last}", provider=self.name,
                            user_message="Lost the line to HeyGen while it was rendering — check your HeyGen videos.")

    # The render is already paid for when this runs, so ride out a DNS/CDN blip for
    # a couple of minutes instead of ~12 s (2026-11-22: a short DNS failure on
    # files2.heygen.ai threw away two finished renders).
    DOWNLOAD_BACKOFF_S = (2.0, 5.0, 10.0, 20.0, 30.0, 45.0)

    async def _download(self, url: str, timeout: float) -> bytes:
        last: Optional[str] = None
        async with base.http_client(timeout=timeout) as client:
            for wait in self.DOWNLOAD_BACKOFF_S + (None,):
                try:
                    resp = await client.get(url, follow_redirects=True)   # presigned link: no key sent
                except httpx.TransportError as exc:
                    last = type(exc).__name__
                    if wait is not None:
                        await base.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    last = f"http {resp.status_code}"
                    if wait is not None:
                        await base.sleep(wait)
                    continue
                if resp.status_code != 200 or not resp.content:
                    raise ProviderError(f"download {resp.status_code}", provider=self.name)
                return resp.content
        raise ProviderError(f"download failed: {last}", provider=self.name)

    async def fetch(self, job: ProviderJob, dest: Path) -> MediaAsset:
        url = job.result_url or ""
        if not url.startswith("https://"):
            raise ProviderError("no https result url", provider=self.name,
                                user_message="HeyGen finished but didn't hand over a video link — "
                                             "grab it from your HeyGen videos.")
        dest = Path(dest)
        try:
            video = await self._download(url, timeout=900.0)
        except ProviderError as exc:
            exc.user_message = "Couldn't download the twin video — grab it from your HeyGen videos."
            raise
        base.atomic_write_bytes(dest, video)

        meta: Dict[str, Any] = {"provider": self.name, "remote_id": job.remote_id}
        extra = job.meta or {}
        sub_url = str(extra.get("subtitle_url") or "")
        if sub_url.startswith("https://"):
            # Caption timing is a nice-to-have: never fail a paid render over it.
            try:
                srt = await self._download(sub_url, timeout=60.0)
                sub_path = dest.with_suffix(".srt")
                base.atomic_write_bytes(sub_path, srt)
                meta["subtitle_path"] = str(sub_path)
            except ProviderError:
                pass
        duration: Optional[float] = None
        try:
            duration = float(extra.get("duration")) if extra.get("duration") else None
        except (TypeError, ValueError):
            duration = None
        return MediaAsset(path=dest, mime="video/mp4", duration_s=duration, meta=meta)

    async def generate(self, req: GenerationRequest, dest: Path) -> MediaAsset:
        """validate → upload voice → create (once) → poll (5 s ×1.5, cap 30 s) → download."""
        self.check_ready()
        audio = self._check_audio(req)
        timeout_s = base.setting_int("heygen_poll_timeout_s", DEFAULT_POLL_TIMEOUT_S)
        asset_id = await self.upload_audio(audio)
        job = await self.submit(req, asset_id)
        delay = POLL_START_S
        waited = 0.0
        while True:
            if job.status == "completed":
                if job.result_url:
                    break
                raise ProviderError("completed without video_url", provider=self.name,
                                    user_message="HeyGen finished but didn't hand over a video link — "
                                                 "grab it from your HeyGen videos.")
            if job.status == "failed":
                raise ProviderRejected(f"failed: {job.error}", provider=self.name,
                                       user_message="HeyGen couldn't render that one — check the twin and the "
                                                    "voice track in HeyGen, then run it again.")
            if waited >= timeout_s:
                raise ProviderUncertainError(
                    f"still {job.status} after {waited:.0f}s", provider=self.name,
                    user_message="HeyGen is still rendering after a long wait — check your HeyGen videos "
                                 "before running again.")
            await base.sleep(delay)
            waited += delay
            delay = min(delay * POLL_FACTOR, POLL_CAP_S)
            job = await self.poll(job)
        asset = await self.fetch(job, dest)
        seconds = asset.duration_s or float(req.duration_s)
        asset.duration_s = seconds
        asset.usage = {"provider": self.name, "unit": "seconds", "amount": int(math.ceil(seconds))}
        asset.meta["engine"] = self.engine or "default"
        return asset
