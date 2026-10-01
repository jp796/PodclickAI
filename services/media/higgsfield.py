"""
services/media/higgsfield.py — Higgsfield adapter (spec §4.4).

FINDINGS NOTE (spec §4.4 asks for this before any runner depends on it).
Verified API facts from the lane-D brief, 2026-10-01; they replace the spec's
"assumption-heavy" placeholders:

- Base ``https://api.higgsfield.ai``. Auth header
  ``Authorization: Key {HF_API_KEY}:{HF_API_SECRET}``.
- Submit: ``POST /{model_id}`` with a JSON body →
  ``{status: "queued", request_id, status_url, cancel_url}``.
- Poll: ``GET status_url`` until ``completed | failed | nsfw | canceled``.
  We poll at 2 s, ×1.5 backoff, capped at 10 s, with ±20 % jitter.
- Completed → ``{video: {url}}``. Outputs are retained only ~7 days, so the
  file is DOWNLOADED into the job's output dir immediately; a provider URL is
  never the deliverable.
- Over the account's concurrency limit Higgsfield answers **HTTP 400, not 429**.
  So concurrency is capped client-side with a semaphore held from submit until
  the job is terminal, and a 400 is never blind-retried.
- Optional ``Idempotency-Key`` header: sent, derived from the request payload,
  so an identical re-run after an uncertain submit can be de-duplicated by
  Higgsfield instead of double-charging.
- Optional ``hf_webhook`` query param: NOT used — PodClick is local-only and has
  no public webhook receiver (§2.4 DeploymentBoundary).
- Talking-head route: model ``wan/v2.7/image-to-video`` with ``image_url`` +
  ``audio_url`` (both must be PUBLIC URLs — the provider fetches them) +
  ``prompt`` + ``duration`` (2–15 s) + ``resolution`` (720p | 1080p).
- Higgsfield "Speak"/"Soul" endpoint ids are UNVERIFIED, so every model id is a
  setting (``HIGGSFIELD_AVATAR_MODEL`` etc.). ``text_to_video`` has no verified
  model and stays off unless ``HIGGSFIELD_TEXT_TO_VIDEO_MODEL`` is set.

Cost discipline: per-request duration (2–15 s) and ``HIGGSFIELD_MAX_SECONDS`` are
checked before the request; a submit is NEVER retried (timeout, reset, 5xx and
a reply without ``request_id`` are all *uncertain*); only polls and downloads
retry. The auth header is only ever sent to the configured Higgsfield host.

Likeness (spec §4.4): ``source_image`` must live in the AI Persona library or
the agent uploads directory. Generating anyone else's likeness is out of scope
by construction.
"""

import asyncio
import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
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

DEFAULT_BASE_URL = "https://api.higgsfield.ai"
DEFAULT_AVATAR_MODEL = "wan/v2.7/image-to-video"
DEFAULT_I2V_MODEL = "wan/v2.7/image-to-video"
MIN_SEGMENT_S = 2
MAX_SEGMENT_S = 15
DEFAULT_MAX_SECONDS = 60
DEFAULT_CONCURRENCY = 2
DEFAULT_POLL_TIMEOUT_S = 1200
RESOLUTIONS = ("720p", "1080p")

POLL_START_S = 2.0
POLL_FACTOR = 1.5
POLL_CAP_S = 10.0
POLL_JITTER = 0.2

TERMINAL_FAIL = ("failed", "nsfw", "canceled", "cancelled")

_REPO_DATA = Path(__file__).resolve().parents[2] / "data"
# Only these roots may supply a face. Patchable in tests.
LIKENESS_ROOTS: Tuple[Path, ...] = (_REPO_DATA / "ai_persona", _REPO_DATA / "agent_uploads",
                                    _REPO_DATA / "agent_outputs")

_rand = random.uniform

# One semaphore per event loop (asyncio primitives are loop-bound; tests run a
# loop per test). Shared across provider instances — the registry builds fresh
# instances per lookup, and the limit is per Higgsfield account, not per object.
_SEMAPHORES: Dict[int, Tuple[int, asyncio.Semaphore]] = {}


def _semaphore(limit: int) -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    held = _SEMAPHORES.get(loop_id)
    if held is None or held[0] != limit:
        held = (limit, asyncio.Semaphore(limit))
        _SEMAPHORES[loop_id] = held
    return held[1]


def _within(path: Path, roots: Tuple[Path, ...]) -> bool:
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


class HiggsfieldProvider(MediaProvider):
    name = "higgsfield"

    def __init__(self) -> None:
        caps = set()
        if self._model("avatar_video"):
            caps.add("avatar_video")
        if self._model("image_to_video"):
            caps.add("image_to_video")
        if self._model("text_to_video"):
            caps.add("text_to_video")
        self.capabilities = frozenset(caps)

    # ── config ──
    def _creds(self) -> Tuple[str, str]:
        return (str(base.setting("hf_api_key", "") or ""), str(base.setting("hf_api_secret", "") or ""))

    def is_configured(self) -> bool:
        key, secret = self._creds()
        return bool(key and secret)

    @property
    def base_url(self) -> str:
        return str(base.setting("higgsfield_base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")

    @property
    def max_seconds(self) -> int:
        return base.setting_int("higgsfield_max_seconds", DEFAULT_MAX_SECONDS)

    def _model(self, capability: str) -> str:
        if capability == "avatar_video":
            return str(base.setting("higgsfield_avatar_model", DEFAULT_AVATAR_MODEL) or "")
        if capability == "image_to_video":
            return str(base.setting("higgsfield_image_to_video_model", DEFAULT_I2V_MODEL) or "")
        if capability == "text_to_video":
            return str(base.setting("higgsfield_text_to_video_model", "") or "")
        return ""

    def _auth(self) -> Dict[str, str]:
        key, secret = self._creds()
        return {"Authorization": f"Key {key}:{secret}"}

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Higgsfield key pair missing", provider=self.name,
                user_message="Higgsfield isn't connected — add HF_API_KEY and HF_API_SECRET to .env and restart.",
            )

    # ── request validation (all before any network) ──
    def check_seconds(self, total_s: float) -> None:
        """Job-level cap; runners call this with the sum of all segments before submitting."""
        if total_s > self.max_seconds:
            raise ProviderCapExceeded(
                f"{total_s:.1f}s > cap {self.max_seconds}s", provider=self.name,
                user_message=(f"That's {total_s:.0f}s of generated video — the cap is {self.max_seconds}s. "
                              f"Shorten it or raise HIGGSFIELD_MAX_SECONDS."),
            )

    def build_payload(self, req: GenerationRequest) -> Tuple[str, Dict[str, Any]]:
        if req.capability not in self.capabilities:
            raise self._unsupported(req.capability)
        duration = int(req.duration_s)
        if duration < MIN_SEGMENT_S or duration > MAX_SEGMENT_S:
            raise ProviderCapExceeded(
                f"duration {duration}s outside {MIN_SEGMENT_S}-{MAX_SEGMENT_S}", provider=self.name,
                user_message=f"Each generated shot has to be {MIN_SEGMENT_S}–{MAX_SEGMENT_S} seconds.",
            )
        self.check_seconds(duration)
        resolution = req.resolution or str(base.setting("higgsfield_resolution", "720p") or "720p")
        if resolution not in RESOLUTIONS:
            raise ValueError(f"resolution must be one of {RESOLUTIONS}")
        payload: Dict[str, Any] = {"prompt": req.prompt, "duration": duration, "resolution": resolution}
        if req.capability in ("avatar_video", "image_to_video"):
            if not req.image_url or not req.image_url.startswith("https://"):
                raise ValueError("Higgsfield needs a public https image_url")
            if req.source_image is None or not _within(req.source_image, LIKENESS_ROOTS):
                raise ValueError("source_image must come from the AI Persona library or an agent upload")
            payload["image_url"] = req.image_url
        if req.capability == "avatar_video":
            if not req.audio_url or not req.audio_url.startswith("https://"):
                raise ValueError("Higgsfield needs a public https audio_url for a talking head")
            payload["audio_url"] = req.audio_url
        return self._model(req.capability), payload

    # ── submit / poll / fetch ──
    async def submit(self, req: GenerationRequest) -> ProviderJob:
        self._require_configured()
        model_id, payload = self.build_payload(req)
        request_hash = hashlib.sha256(
            json.dumps({"model": model_id, **payload}, sort_keys=True).encode()
        ).hexdigest()
        headers = dict(self._auth())
        headers["Idempotency-Key"] = request_hash[:64]
        uncertain = ("Didn't hear back from Higgsfield — check your Higgsfield dashboard "
                     "before running again.")
        async with base.http_client(timeout=60.0) as client:
            try:
                resp = await client.post(f"{self.base_url}/{model_id}", headers=headers, json=payload)
            except httpx.TransportError as exc:
                raise ProviderUncertainError(f"submit: {type(exc).__name__}", provider=self.name,
                                             user_message=uncertain)
        status = resp.status_code
        if status in (401, 403):
            raise ProviderAuthError(f"auth {status}", provider=self.name,
                                    user_message="Higgsfield turned the key down — check HF_API_KEY / HF_API_SECRET and restart.")
        if status == 402:
            raise ProviderQuotaError("credits", provider=self.name,
                                     user_message="Out of Higgsfield credits.")
        if status == 429:
            raise ProviderRateLimited("429", provider=self.name,
                                      user_message="Higgsfield is rate-limiting us — wait a minute and run it again.")
        if status == 400:
            # Over-concurrency also arrives as 400. Never blind-retry it.
            raise ProviderError(f"submit 400: {resp.text[:200]}", provider=self.name,
                                user_message=("Higgsfield refused the job (HTTP 400) — usually too many renders "
                                              "at once, or a bad photo/audio link."))
        if status >= 500:
            raise ProviderUncertainError(f"submit {status}", provider=self.name, user_message=uncertain)
        if status not in (200, 201, 202):
            raise ProviderError(f"submit {status}", provider=self.name,
                                user_message=f"Higgsfield refused the job (HTTP {status}).")
        try:
            data = resp.json()
        except ValueError:
            data = {}
        request_id = data.get("request_id") if isinstance(data, dict) else None
        if not request_id:
            raise ProviderUncertainError("submit: no request_id", provider=self.name, user_message=uncertain)
        return ProviderJob(
            provider=self.name, remote_id=str(request_id), status=str(data.get("status") or "queued"),
            submitted_at=time.time(), request_hash=request_hash,
            status_url=data.get("status_url"), cancel_url=data.get("cancel_url"),
        )

    def _same_host(self, url: str) -> bool:
        target = urlparse(url)
        mine = urlparse(self.base_url)
        return target.scheme == "https" and target.netloc == mine.netloc

    async def poll(self, job: ProviderJob) -> ProviderJob:
        self._require_configured()
        if not job.status_url or not self._same_host(job.status_url):
            # Never send the key pair to a host we did not configure.
            raise ProviderError("status_url not on the Higgsfield host", provider=self.name,
                                user_message="Higgsfield sent back a status link we don't trust — check your dashboard.")
        last: Optional[str] = None
        async with base.http_client(timeout=30.0) as client:
            for attempt in range(3):
                try:
                    resp = await client.get(job.status_url, headers=self._auth())
                except httpx.TransportError as exc:
                    last = type(exc).__name__
                    await base.sleep(2.0 * (attempt + 1))
                    continue
                if resp.status_code in (401, 403):
                    raise ProviderAuthError("auth", provider=self.name,
                                            user_message="Higgsfield turned the key down — check HF_API_KEY / HF_API_SECRET and restart.")
                if resp.status_code >= 500 or resp.status_code == 429:
                    last = f"http {resp.status_code}"
                    await base.sleep(2.0 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    raise ProviderError(f"poll {resp.status_code}", provider=self.name,
                                        user_message="Lost track of the Higgsfield render — check your dashboard.")
                data = resp.json() or {}
                job.status = str(data.get("status") or job.status)
                if job.status == "completed":
                    video = data.get("video") or {}
                    job.result_url = video.get("url") if isinstance(video, dict) else None
                elif job.status in TERMINAL_FAIL:
                    job.error = str(data.get("error") or data.get("detail") or job.status)
                return job
        raise ProviderError(f"poll failed: {last}", provider=self.name,
                            user_message="Lost the line to Higgsfield while it was rendering — check your dashboard.")

    async def fetch(self, job: ProviderJob, dest: Path) -> MediaAsset:
        url = job.result_url or ""
        if not url.startswith("https://"):
            raise ProviderError("no https result url", provider=self.name,
                                user_message="Higgsfield finished but didn't hand over a video link.")
        last: Optional[str] = None
        async with base.http_client(timeout=300.0) as client:
            for attempt in range(3):
                try:
                    # No auth header: the result URL is a CDN link, not the API host.
                    resp = await client.get(url)
                except httpx.TransportError as exc:
                    last = type(exc).__name__
                    await base.sleep(2.0 * (attempt + 1))
                    continue
                if resp.status_code >= 500:
                    last = f"http {resp.status_code}"
                    await base.sleep(2.0 * (attempt + 1))
                    continue
                if resp.status_code != 200 or not resp.content:
                    raise ProviderError(f"download {resp.status_code}", provider=self.name,
                                        user_message="Couldn't download the Higgsfield video — grab it from your dashboard within 7 days.")
                base.atomic_write_bytes(Path(dest), resp.content)
                return MediaAsset(path=Path(dest), mime="video/mp4",
                                  meta={"provider": self.name, "remote_id": job.remote_id})
        raise ProviderError(f"download failed: {last}", provider=self.name,
                            user_message="Couldn't download the Higgsfield video — grab it from your dashboard within 7 days.")

    async def generate(self, req: GenerationRequest, dest: Path) -> MediaAsset:
        """submit → poll (2 s ×1.5, cap 10 s, jitter) → download, inside the concurrency cap."""
        self._require_configured()
        self.build_payload(req)  # validate before queueing behind the semaphore
        limit = max(1, base.setting_int("higgsfield_concurrency", DEFAULT_CONCURRENCY))
        timeout_s = base.setting_int("higgsfield_poll_timeout_s", DEFAULT_POLL_TIMEOUT_S)
        async with _semaphore(limit):
            job = await self.submit(req)
            delay = POLL_START_S
            waited = 0.0
            while True:
                if job.status == "completed":
                    if job.result_url:
                        break
                    raise ProviderError("completed without video.url", provider=self.name,
                                        user_message="Higgsfield finished but didn't hand over a video link — check your dashboard.")
                if job.status in TERMINAL_FAIL:
                    if job.status == "nsfw":
                        msg = "Higgsfield flagged the content and wouldn't render it."
                    elif job.status in ("canceled", "cancelled"):
                        msg = "The Higgsfield render was canceled."
                    else:
                        msg = "Higgsfield couldn't render that shot."
                    raise ProviderRejected(f"{job.status}: {job.error}", provider=self.name, user_message=msg)
                if waited >= timeout_s:
                    raise ProviderUncertainError(
                        f"still {job.status} after {waited:.0f}s", provider=self.name,
                        user_message=("Higgsfield is still rendering after a long wait — check your dashboard "
                                      "before running again."),
                    )
                pause = delay * _rand(1.0 - POLL_JITTER, 1.0 + POLL_JITTER)
                await base.sleep(pause)
                waited += pause
                delay = min(delay * POLL_FACTOR, POLL_CAP_S)
                job = await self.poll(job)
            asset = await self.fetch(job, dest)
        asset.duration_s = float(req.duration_s)
        asset.usage = {"provider": self.name, "unit": "seconds", "amount": int(req.duration_s)}
        return asset
