"""
Lane D — media provider adapters (AGENTS_HUB_SPEC §4).

Every provider HTTP call is answered by an in-process httpx.MockTransport; an
autouse guard transport FAILS the test on any request a test did not expect, so
"zero network calls" is asserted, not assumed. No real credits are spent.

Each guard here was proven by breaking the code it protects (see the lane-D
report for the mutation list).
"""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

from services.media import base, elevenlabs, ffmpeg_local, higgsfield, registry
from services.media.base import (
    GenerationRequest,
    ProviderAuthError,
    ProviderCapExceeded,
    ProviderError,
    ProviderNotConfigured,
    ProviderQuotaError,
    ProviderRateLimited,
    ProviderRejected,
    ProviderUncertainError,
    SpeechRequest,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
_REAL_SETTING = base.setting  # captured before the autouse fixture swaps it


# ── harness ───────────────────────────────────────────────────────────────────

class Net:
    """Records every request; answers via a handler; default handler = fail."""

    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.handler: Optional[Callable[[httpx.Request], httpx.Response]] = None

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler is None:
            raise AssertionError(f"unexpected network call: {request.method} {request.url}")
        return self.handler(request)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._dispatch)


@pytest.fixture
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(base, "HTTP_TRANSPORT", n.transport())
    return n


@pytest.fixture
def cfg(monkeypatch):
    """Settings are a plain dict for every test — never the real .env."""
    values: Dict[str, Any] = {}

    def fake_setting(name, default=None):
        v = values.get(name)
        return default if v is None or v == "" else v

    monkeypatch.setattr(base, "setting", fake_setting)
    return values


@pytest.fixture
def sleeps(monkeypatch):
    recorded: List[float] = []

    async def fake_sleep(s):
        recorded.append(s)

    monkeypatch.setattr(base, "sleep", fake_sleep)
    return recorded


@pytest.fixture(autouse=True)
def _isolate(net, cfg, sleeps, monkeypatch):
    higgsfield._SEMAPHORES.clear()
    monkeypatch.setattr(higgsfield, "_rand", lambda lo, hi: 1.0)  # no jitter unless a test wants it
    yield


@pytest.fixture(scope="session")
def mp3_bytes(tmp_path_factory) -> bytes:
    if not HAS_FFMPEG:
        return b"ID3fake"
    p = tmp_path_factory.mktemp("fx") / "tone.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:a", "libmp3lame", str(p)], check=True)
    return p.read_bytes()


def el_ok(mp3: bytes) -> Callable[[httpx.Request], httpx.Response]:
    return lambda r: httpx.Response(200, content=mp3, headers={"content-type": "audio/mpeg"})


# ── ElevenLabs ────────────────────────────────────────────────────────────────

async def test_elevenlabs_missing_key_raises_before_any_network(net, cfg, tmp_path):
    p = elevenlabs.ElevenLabsProvider()
    assert p.is_configured() is False
    with pytest.raises(ProviderNotConfigured) as ei:
        await p.synthesize_speech(SpeechRequest(text="hello", voice_id="v1"), tmp_path / "o.mp3")
    assert "ELEVENLABS_API_KEY" in ei.value.user_message
    assert net.requests == []


async def test_registry_returns_none_for_tts_without_key_and_makes_no_calls(net, cfg):
    assert registry.get_provider("tts") is None
    assert registry.get_provider("avatar_video") is None
    assert net.requests == []


async def test_elevenlabs_char_cap_enforced_before_any_network(net, cfg, tmp_path):
    cfg.update(elevenlabs_api_key="k", elevenlabs_max_chars=10)
    p = elevenlabs.ElevenLabsProvider()
    with pytest.raises(ProviderCapExceeded) as ei:
        await p.synthesize_speech(SpeechRequest(text="x" * 11, voice_id="v1"), tmp_path / "o.mp3")
    assert "ELEVENLABS_MAX_CHARS" in ei.value.user_message
    assert net.requests == []


async def test_elevenlabs_missing_voice_raises_before_any_network(net, cfg, tmp_path):
    cfg.update(elevenlabs_api_key="k")
    with pytest.raises(ProviderNotConfigured):
        await elevenlabs.ElevenLabsProvider().synthesize_speech(SpeechRequest(text="hi"), tmp_path / "o.mp3")
    assert net.requests == []


async def test_elevenlabs_request_contract_and_file_written(net, cfg, tmp_path, mp3_bytes):
    cfg.update(elevenlabs_api_key="SECRET-KEY", elevenlabs_voice_id="voiceJP")
    net.handler = el_ok(mp3_bytes)
    dest = tmp_path / "o.mp3"
    asset = await elevenlabs.ElevenLabsProvider().synthesize_speech(
        SpeechRequest(text="Hello from the job site.", settings=elevenlabs.voice_settings_for("intro")), dest)

    assert len(net.requests) == 1
    req = net.requests[0]
    assert req.method == "POST"
    assert str(req.url).startswith("https://api.elevenlabs.io/v1/text-to-speech/voiceJP")
    assert req.url.params["output_format"] == "mp3_44100_128"
    assert req.headers["xi-api-key"] == "SECRET-KEY"
    body = json.loads(req.content)
    assert body["text"] == "Hello from the job site."
    assert body["model_id"] == "eleven_v4"
    assert set(body["voice_settings"]) == {"stability", "similarity_boost", "style", "use_speaker_boost", "speed"}
    assert dest.read_bytes() == mp3_bytes
    assert asset.usage == {"provider": "elevenlabs", "unit": "characters", "amount": len("Hello from the job site.")}
    # the key never leaks into the asset
    assert "SECRET-KEY" not in json.dumps(asset.usage) + json.dumps(asset.meta)


async def test_elevenlabs_falls_back_to_multilingual_when_model_rejected(net, cfg, tmp_path, mp3_bytes):
    cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v")

    def handler(r):
        if json.loads(r.content)["model_id"] == "eleven_v4":
            return httpx.Response(400, json={"detail": {"status": "model_not_found", "message": "model eleven_v4"}})
        return httpx.Response(200, content=mp3_bytes)

    net.handler = handler
    asset = await elevenlabs.ElevenLabsProvider().synthesize_speech(SpeechRequest(text="hi"), tmp_path / "o.mp3")
    models = [json.loads(r.content)["model_id"] for r in net.requests]
    assert models == ["eleven_v4", "eleven_multilingual_v2"]
    assert asset.meta["model_id"] == "eleven_multilingual_v2"


async def test_elevenlabs_non_model_400_is_not_retried_or_fallen_back(net, cfg, tmp_path):
    cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v")
    net.handler = lambda r: httpx.Response(400, json={"detail": "text too long"})
    with pytest.raises(ProviderError):
        await elevenlabs.ElevenLabsProvider().synthesize_speech(SpeechRequest(text="hi"), tmp_path / "o.mp3")
    assert len(net.requests) == 1


async def test_elevenlabs_auth_and_quota_errors_not_retried(net, cfg, tmp_path):
    cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v")
    net.handler = lambda r: httpx.Response(401, json={"detail": {"status": "invalid_api_key"}})
    with pytest.raises(ProviderAuthError) as ei:
        await elevenlabs.ElevenLabsProvider().synthesize_speech(SpeechRequest(text="hi"), tmp_path / "o.mp3")
    assert "turned the key down" in ei.value.user_message
    assert len(net.requests) == 1

    net.requests.clear()
    net.handler = lambda r: httpx.Response(401, json={"detail": {"status": "quota_exceeded"}})
    with pytest.raises(ProviderQuotaError):
        await elevenlabs.ElevenLabsProvider().synthesize_speech(SpeechRequest(text="hi"), tmp_path / "o.mp3")
    assert len(net.requests) == 1


async def test_elevenlabs_transient_429_backs_off_5_15_45_then_gives_up(net, cfg, sleeps, tmp_path):
    cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v")
    net.handler = lambda r: httpx.Response(429, json={"detail": {"status": "too_many_concurrent_requests"}})
    with pytest.raises(ProviderRateLimited):
        await elevenlabs.ElevenLabsProvider().synthesize_speech(SpeechRequest(text="hi"), tmp_path / "o.mp3")
    assert sleeps == [5.0, 15.0, 45.0]
    assert len(net.requests) == 4


async def test_elevenlabs_429_then_success(net, cfg, sleeps, tmp_path, mp3_bytes):
    cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v")
    answers = [httpx.Response(429, json={"detail": "busy"}), httpx.Response(200, content=mp3_bytes)]
    net.handler = lambda r: answers.pop(0)
    await elevenlabs.ElevenLabsProvider().synthesize_speech(SpeechRequest(text="hi"), tmp_path / "o.mp3")
    assert sleeps == [5.0]
    assert len(net.requests) == 2


async def test_elevenlabs_timeout_after_send_is_uncertain_and_never_retried(net, cfg, tmp_path):
    cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v")

    def handler(r):
        raise httpx.ReadTimeout("no answer", request=r)

    net.handler = handler
    with pytest.raises(ProviderUncertainError) as ei:
        await elevenlabs.ElevenLabsProvider().synthesize_speech(SpeechRequest(text="hi"), tmp_path / "o.mp3")
    assert "history" in ei.value.user_message
    assert len(net.requests) == 1


async def test_elevenlabs_5xx_on_write_is_not_retried(net, cfg, tmp_path):
    cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v")
    net.handler = lambda r: httpx.Response(503, text="down")
    with pytest.raises(ProviderError):
        await elevenlabs.ElevenLabsProvider().synthesize_speech(SpeechRequest(text="hi"), tmp_path / "o.mp3")
    assert len(net.requests) == 1


async def test_elevenlabs_long_script_is_chunked_on_sentences(net, cfg, tmp_path, mp3_bytes, monkeypatch):
    cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v", elevenlabs_max_chars=8000)
    net.handler = el_ok(mp3_bytes)
    concat_calls: List[List[Path]] = []

    async def fake_concat(paths, dest):
        concat_calls.append(list(paths))
        Path(dest).write_bytes(b"".join(Path(p).read_bytes() for p in paths))
        return dest

    monkeypatch.setattr(ffmpeg_local, "concat_audio", fake_concat)
    sentence = "This market is moving and I want you to know exactly how. "
    text = (sentence * 90).strip()   # ~5,300 chars
    await elevenlabs.ElevenLabsProvider().synthesize_speech(SpeechRequest(text=text), tmp_path / "o.mp3")

    sent = [json.loads(r.content)["text"] for r in net.requests]
    assert len(sent) >= 3
    assert all(len(s) <= elevenlabs.CHUNK_CHARS for s in sent)
    assert all(s.endswith(".") for s in sent)            # sentence boundaries
    assert " ".join(sent) == text                         # nothing lost or reordered
    assert len(concat_calls) == 1 and len(concat_calls[0]) == len(sent)


def test_split_for_tts_splits_overlong_sentence_on_words():
    word = "abcdefghij"
    text = " ".join([word] * 600)  # one 6,599-char "sentence"
    chunks = elevenlabs.split_for_tts(text, 2500)
    assert all(len(c) <= 2500 for c in chunks)
    assert " ".join(chunks) == text


def test_voice_settings_presets_and_overrides(cfg):
    assert elevenlabs.voice_settings_for("intro") != elevenlabs.voice_settings_for("ad read")
    assert elevenlabs.voice_settings_for("nonsense") == elevenlabs.voice_settings_for("narration")
    cfg["elevenlabs_voice_settings"] = json.dumps({"stability": 0.9, "intro": {"style": 0.1}, "bogus": 1})
    intro = elevenlabs.voice_settings_for("intro")
    assert intro["stability"] == 0.9 and intro["style"] == 0.1 and "bogus" not in intro
    cfg["elevenlabs_voice_settings"] = "{not json"
    assert elevenlabs.voice_settings_for("intro") == elevenlabs.PRESETS["intro"]


async def test_clone_voice_multipart_and_never_retried(net, cfg, tmp_path):
    cfg.update(elevenlabs_api_key="k")
    sample = tmp_path / "s.mp3"
    sample.write_bytes(b"ID3data")
    net.handler = lambda r: httpx.Response(200, json={"voice_id": "newVoice"})
    vid = await elevenlabs.ElevenLabsProvider().clone_voice("JP", [sample])
    assert vid == "newVoice"
    req = net.requests[0]
    assert str(req.url) == "https://api.elevenlabs.io/v1/voices/add"
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="name"' in req.content and b"JP" in req.content and b'name="files"' in req.content

    net.requests.clear()
    net.handler = lambda r: httpx.Response(500, text="boom")
    with pytest.raises(ProviderError):
        await elevenlabs.ElevenLabsProvider().clone_voice("JP", [sample])
    assert len(net.requests) == 1


# ── Higgsfield ────────────────────────────────────────────────────────────────

def _hf_cfg(cfg, tmp_path, **extra):
    persona_root = tmp_path / "ai_persona"
    persona_root.mkdir(exist_ok=True)
    img = persona_root / "me.jpg"
    img.write_bytes(b"\xff\xd8jpeg")
    cfg.update(hf_api_key="KEYID", hf_api_secret="KEYSECRET", **extra)
    return img


@pytest.fixture
def roots(monkeypatch, tmp_path):
    monkeypatch.setattr(higgsfield, "LIKENESS_ROOTS", (tmp_path / "ai_persona", tmp_path / "agent_uploads"))


def _avatar_req(img, duration=8):
    return GenerationRequest(capability="avatar_video", prompt="talk", duration_s=duration,
                             image_url="https://pub.example/persona.jpg",
                             audio_url="https://pub.example/voice.mp3", source_image=img)


async def test_higgsfield_needs_key_and_secret_no_network(net, cfg, tmp_path, roots):
    cfg.update(hf_api_key="only-key")
    p = higgsfield.HiggsfieldProvider()
    assert p.is_configured() is False
    assert registry.get_provider("avatar_video") is None
    with pytest.raises(ProviderNotConfigured) as ei:
        await p.submit(_avatar_req(tmp_path / "x.jpg"))
    assert "HF_API_SECRET" in ei.value.user_message
    assert net.requests == []


async def test_higgsfield_submit_contract(net, cfg, tmp_path, roots):
    img = _hf_cfg(cfg, tmp_path)
    net.handler = lambda r: httpx.Response(200, json={
        "status": "queued", "request_id": "req-1",
        "status_url": "https://api.higgsfield.ai/requests/req-1/status",
        "cancel_url": "https://api.higgsfield.ai/requests/req-1/cancel"})
    job = await higgsfield.HiggsfieldProvider().submit(_avatar_req(img))
    req = net.requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.higgsfield.ai/wan/v2.7/image-to-video"
    assert req.headers["authorization"] == "Key KEYID:KEYSECRET"
    assert req.headers.get("idempotency-key")
    body = json.loads(req.content)
    assert body == {"prompt": "talk", "duration": 8, "resolution": "720p",
                    "image_url": "https://pub.example/persona.jpg",
                    "audio_url": "https://pub.example/voice.mp3"}
    assert job.remote_id == "req-1" and job.status == "queued"


async def test_higgsfield_model_id_is_configurable(net, cfg, tmp_path, roots):
    img = _hf_cfg(cfg, tmp_path, higgsfield_avatar_model="higgsfield/speak/v2")
    net.handler = lambda r: httpx.Response(200, json={"status": "queued", "request_id": "r",
                                                      "status_url": "https://api.higgsfield.ai/s"})
    await higgsfield.HiggsfieldProvider().submit(_avatar_req(img))
    assert str(net.requests[0].url) == "https://api.higgsfield.ai/higgsfield/speak/v2"


@pytest.mark.parametrize("duration", [1, 16])
async def test_higgsfield_segment_duration_window_enforced_before_network(net, cfg, tmp_path, roots, duration):
    img = _hf_cfg(cfg, tmp_path)
    with pytest.raises(ProviderCapExceeded):
        await higgsfield.HiggsfieldProvider().submit(_avatar_req(img, duration=duration))
    assert net.requests == []


async def test_higgsfield_job_seconds_cap_enforced_before_network(net, cfg, tmp_path, roots):
    img = _hf_cfg(cfg, tmp_path, higgsfield_max_seconds=6)
    p = higgsfield.HiggsfieldProvider()
    with pytest.raises(ProviderCapExceeded):
        await p.submit(_avatar_req(img, duration=8))
    with pytest.raises(ProviderCapExceeded) as ei:
        p.check_seconds(45.0)
    assert "HIGGSFIELD_MAX_SECONDS" in ei.value.user_message
    assert net.requests == []


async def test_higgsfield_likeness_outside_persona_library_refused(net, cfg, tmp_path, roots):
    _hf_cfg(cfg, tmp_path)
    stranger = tmp_path / "downloads" / "someone.jpg"
    stranger.parent.mkdir()
    stranger.write_bytes(b"x")
    with pytest.raises(ValueError):
        await higgsfield.HiggsfieldProvider().submit(_avatar_req(stranger))
    traversal = tmp_path / "ai_persona" / ".." / "downloads" / "someone.jpg"
    with pytest.raises(ValueError):
        await higgsfield.HiggsfieldProvider().submit(_avatar_req(traversal))
    assert net.requests == []


async def test_higgsfield_requires_public_https_urls(net, cfg, tmp_path, roots):
    img = _hf_cfg(cfg, tmp_path)
    bad = _avatar_req(img)
    bad.audio_url = "http://localhost:8765/voice.mp3"
    with pytest.raises(ValueError):
        await higgsfield.HiggsfieldProvider().submit(bad)
    assert net.requests == []


@pytest.mark.parametrize("make_response", [
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("t", request=r)),
    lambda r: (_ for _ in ()).throw(httpx.RemoteProtocolError("reset", request=r)),
    lambda r: httpx.Response(502, text="bad gateway"),
    lambda r: httpx.Response(200, json={"status": "queued"}),   # accepted? no id → uncertain
])
async def test_higgsfield_uncertain_submit_never_retried(net, cfg, tmp_path, roots, make_response):
    img = _hf_cfg(cfg, tmp_path)
    net.handler = make_response
    with pytest.raises(ProviderUncertainError) as ei:
        await higgsfield.HiggsfieldProvider().generate(_avatar_req(img), tmp_path / "o.mp4")
    assert "dashboard" in ei.value.user_message
    assert len(net.requests) == 1


async def test_higgsfield_400_concurrency_is_not_blind_retried(net, cfg, tmp_path, roots):
    img = _hf_cfg(cfg, tmp_path)
    net.handler = lambda r: httpx.Response(400, json={"detail": "concurrency limit reached"})
    with pytest.raises(ProviderError) as ei:
        await higgsfield.HiggsfieldProvider().generate(_avatar_req(img), tmp_path / "o.mp4")
    assert not isinstance(ei.value, ProviderUncertainError)
    assert len(net.requests) == 1


def _hf_flow(statuses: List[str], video_bytes: bytes = b"MP4DATA", final: Optional[dict] = None):
    """Handler: submit → status sequence → download."""
    seq = list(statuses)

    def handler(r: httpx.Request) -> httpx.Response:
        if r.method == "POST":
            return httpx.Response(200, json={"status": "queued", "request_id": "rq",
                                             "status_url": "https://api.higgsfield.ai/requests/rq/status"})
        if r.url.host == "api.higgsfield.ai":
            s = seq.pop(0)
            body: Dict[str, Any] = {"status": s, "request_id": "rq"}
            if s == "completed":
                body["video"] = {"url": "https://cdn.higgsfield.ai/out/rq.mp4"}
            if final and s in final:
                body.update(final[s])
            return httpx.Response(200, json=body)
        if r.url.host == "cdn.higgsfield.ai":
            return httpx.Response(200, content=video_bytes)
        raise AssertionError(f"unexpected host {r.url}")

    return handler


async def test_higgsfield_generate_polls_with_backoff_and_downloads(net, cfg, sleeps, tmp_path, roots):
    img = _hf_cfg(cfg, tmp_path)
    net.handler = _hf_flow(["queued", "in_progress", "in_progress", "in_progress", "in_progress", "completed"])
    dest = tmp_path / "shot.mp4"
    asset = await higgsfield.HiggsfieldProvider().generate(_avatar_req(img), dest)
    assert dest.read_bytes() == b"MP4DATA"
    assert sleeps == [2.0, 3.0, 4.5, 6.75, 10.0, 10.0]          # ×1.5, capped at 10
    assert asset.usage == {"provider": "higgsfield", "unit": "seconds", "amount": 8}
    download = [r for r in net.requests if r.url.host == "cdn.higgsfield.ai"][0]
    assert "authorization" not in download.headers                # key never sent to the CDN
    assert sum(1 for r in net.requests if r.method == "POST") == 1


async def test_higgsfield_poll_jitter_stays_within_20_percent(net, cfg, sleeps, tmp_path, roots, monkeypatch):
    import random as _r
    rng = _r.Random(7)
    monkeypatch.setattr(higgsfield, "_rand", rng.uniform)
    img = _hf_cfg(cfg, tmp_path)
    net.handler = _hf_flow(["queued"] * 6 + ["completed"])
    await higgsfield.HiggsfieldProvider().generate(_avatar_req(img), tmp_path / "o.mp4")
    nominal = [2.0, 3.0, 4.5, 6.75, 10.0, 10.0, 10.0]
    assert len(sleeps) == len(nominal)
    assert any(abs(s - n) > 1e-9 for s, n in zip(sleeps, nominal))
    assert all(0.8 * n - 1e-9 <= s <= 1.2 * n + 1e-9 for s, n in zip(sleeps, nominal))


@pytest.mark.parametrize("terminal,phrase", [("failed", "couldn't render"), ("nsfw", "flagged"),
                                             ("canceled", "canceled")])
async def test_higgsfield_terminal_failure_raises_and_never_downloads(net, cfg, tmp_path, roots, terminal, phrase):
    img = _hf_cfg(cfg, tmp_path)
    net.handler = _hf_flow(["in_progress", terminal])
    with pytest.raises(ProviderRejected) as ei:
        await higgsfield.HiggsfieldProvider().generate(_avatar_req(img), tmp_path / "o.mp4")
    assert phrase in ei.value.user_message
    assert not any(r.url.host == "cdn.higgsfield.ai" for r in net.requests)
    assert sum(1 for r in net.requests if r.method == "POST") == 1


async def test_higgsfield_never_sends_key_to_foreign_status_url(net, cfg, tmp_path, roots):
    img = _hf_cfg(cfg, tmp_path)

    def handler(r):
        if r.method == "POST":
            return httpx.Response(200, json={"status": "queued", "request_id": "rq",
                                             "status_url": "https://evil.example/steal"})
        raise AssertionError(f"request to {r.url}")

    net.handler = handler
    with pytest.raises(ProviderError):
        await higgsfield.HiggsfieldProvider().generate(_avatar_req(img), tmp_path / "o.mp4")
    assert [r.url.host for r in net.requests] == ["api.higgsfield.ai"]


async def test_higgsfield_poll_retries_transient_5xx(net, cfg, sleeps, tmp_path, roots):
    img = _hf_cfg(cfg, tmp_path)
    flow = _hf_flow(["completed"])
    hiccups = [httpx.Response(503, text="busy")]

    def handler(r):
        if r.method == "GET" and r.url.host == "api.higgsfield.ai" and hiccups:
            return hiccups.pop(0)
        return flow(r)

    net.handler = handler
    await higgsfield.HiggsfieldProvider().generate(_avatar_req(img), tmp_path / "o.mp4")
    polls = [r for r in net.requests if r.method == "GET" and r.url.host == "api.higgsfield.ai"]
    assert len(polls) == 2


async def test_higgsfield_poll_timeout_stops_without_resubmitting(net, cfg, sleeps, tmp_path, roots):
    img = _hf_cfg(cfg, tmp_path, higgsfield_poll_timeout_s=20)
    net.handler = _hf_flow(["in_progress"] * 50)
    with pytest.raises(ProviderUncertainError):
        await higgsfield.HiggsfieldProvider().generate(_avatar_req(img), tmp_path / "o.mp4")
    assert sum(1 for r in net.requests if r.method == "POST") == 1


async def test_higgsfield_completed_without_url_is_an_error_not_a_hang(net, cfg, sleeps, tmp_path, roots):
    img = _hf_cfg(cfg, tmp_path)

    def handler(r):
        if r.method == "POST":
            return httpx.Response(200, json={"status": "queued", "request_id": "rq",
                                             "status_url": "https://api.higgsfield.ai/s"})
        return httpx.Response(200, json={"status": "completed"})

    net.handler = handler
    with pytest.raises(ProviderError):
        await higgsfield.HiggsfieldProvider().generate(_avatar_req(img), tmp_path / "o.mp4")
    assert len(sleeps) <= 1


async def test_higgsfield_concurrency_semaphore_caps_in_flight_jobs(net, cfg, sleeps, tmp_path, roots, monkeypatch):
    img = _hf_cfg(cfg, tmp_path, higgsfield_concurrency=1)
    in_flight = {"now": 0, "max": 0}
    polls: Dict[str, int] = {}

    async def yielding_sleep(s):
        sleeps.append(s)
        await asyncio.sleep(0)

    monkeypatch.setattr(base, "sleep", yielding_sleep)
    counter = {"n": 0}

    def handler(r):
        if r.method == "POST":
            counter["n"] += 1
            rid = f"rq{counter['n']}"
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
            return httpx.Response(200, json={"status": "queued", "request_id": rid,
                                             "status_url": f"https://api.higgsfield.ai/requests/{rid}"})
        if r.url.host == "api.higgsfield.ai":
            rid = r.url.path.rsplit("/", 1)[-1]
            polls[rid] = polls.get(rid, 0) + 1
            if polls[rid] < 3:
                return httpx.Response(200, json={"status": "in_progress"})
            return httpx.Response(200, json={"status": "completed",
                                             "video": {"url": f"https://cdn.higgsfield.ai/{rid}.mp4"}})
        in_flight["now"] -= 1
        return httpx.Response(200, content=b"MP4")

    net.handler = handler
    p = higgsfield.HiggsfieldProvider()
    await asyncio.gather(p.generate(_avatar_req(img), tmp_path / "a.mp4"),
                         p.generate(_avatar_req(img), tmp_path / "b.mp4"),
                         higgsfield.HiggsfieldProvider().generate(_avatar_req(img), tmp_path / "c.mp4"))
    assert counter["n"] == 3
    assert in_flight["max"] == 1


def test_higgsfield_capabilities_follow_model_settings(cfg):
    assert higgsfield.HiggsfieldProvider().capabilities == frozenset({"avatar_video", "image_to_video"})
    cfg["higgsfield_text_to_video_model"] = "some/t2v"
    assert "text_to_video" in higgsfield.HiggsfieldProvider().capabilities


# ── registry ──────────────────────────────────────────────────────────────────

def test_registry_returns_configured_provider_in_preference_order(cfg, monkeypatch):
    class FakeTTS(base.MediaProvider):
        name = "fake_tts"
        capabilities = frozenset({"tts"})

        def is_configured(self):
            return True

    monkeypatch.setitem(registry._FACTORIES, "fake_tts", FakeTTS)
    cfg["elevenlabs_api_key"] = "k"
    assert registry.get_provider("tts").name == "elevenlabs"          # built-in order first
    cfg["podclick_media_preference"] = "fake_tts, elevenlabs"
    assert registry.get_provider("tts").name == "fake_tts"
    cfg["podclick_media_preference"] = "fake_tts"                     # named alone still wins
    assert registry.get_provider("tts").name == "fake_tts"
    assert [p.name for p in registry.all_providers()][0] == "fake_tts"
    cfg["elevenlabs_api_key"] = ""
    cfg["podclick_media_preference"] = ""
    assert registry.get_provider("tts").name == "fake_tts"            # fail-soft to what's configured


def test_registry_unknown_capability_is_a_programming_error(cfg):
    with pytest.raises(ValueError):
        registry.get_provider("teleport")


@needs_ffmpeg
def test_registry_ken_burns_always_available(cfg):
    assert registry.get_provider("ken_burns").name == "ffmpeg_local"


def test_missing_requirements_reports_known_names_only(cfg, net):
    assert registry.missing_requirements(["elevenlabs", "higgsfield", "pexels", "youtube_api"]) == [
        "elevenlabs", "higgsfield", "pexels"]
    cfg.update(elevenlabs_api_key="k", hf_api_key="a", hf_api_secret="b", pexels_api_key="p")
    assert registry.missing_requirements(["elevenlabs", "higgsfield", "pexels"]) == []
    assert net.requests == []


def test_setting_prefers_config_then_env(monkeypatch):
    real = _REAL_SETTING
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "from-env")
    import sys
    fake_cfg = type(sys)("config")
    fake_cfg.settings = type("S", (), {"elevenlabs_model_id": "", "hf_api_key": "from-config"})()
    monkeypatch.setitem(sys.modules, "config", fake_cfg)
    assert real("elevenlabs_model_id", "default") == "from-env"     # empty config → env fallback
    assert real("hf_api_key") == "from-config"
    assert real("nope_not_set", "dflt") == "dflt"


def test_public_file_url_requires_https_origin(cfg):
    assert base.public_file_url("job1", "a.mp3") is None
    cfg["podclick_public_media_base_url"] = "http://localhost:8765"
    assert base.public_file_url("job1", "a.mp3") is None
    cfg["podclick_public_media_base_url"] = "https://studio.example.com/"
    assert base.public_file_url("job1", "a.mp3") == "https://studio.example.com/api/agents/jobs/job1/files/a.mp3"


# ── ffmpeg_local (real ffmpeg, tiny canvases) ────────────────────────────────

def test_plan_segments():
    segs = ffmpeg_local.plan_segments(37.0)
    assert len(segs) == 3 and all(2 <= s <= 15 for s in segs) and abs(sum(segs) - 37.0) < 1e-9
    assert ffmpeg_local.plan_segments(10.0) == [10.0]
    assert len(ffmpeg_local.plan_segments(15.0)) == 1
    with pytest.raises(ValueError):
        ffmpeg_local.plan_segments(1.0)


def test_estimate_alignment_is_monotonic_and_spans_duration():
    words = ffmpeg_local.estimate_alignment("Prices are moving in Springfield this fall", 4.0)
    assert [w["word"] for w in words] == ["Prices", "are", "moving", "in", "Springfield", "this", "fall"]
    assert words[0]["start"] == 0.0 and words[-1]["end"] == 4.0
    assert all(a["start"] <= a["end"] <= b["start"] + 1e-6 for a, b in zip(words, words[1:]))
    assert ffmpeg_local.estimate_alignment("", 3.0) == []


@needs_ffmpeg
async def test_ffmpeg_local_ken_burns_concat_mux_and_captions(tmp_path, monkeypatch, mp3_bytes):
    monkeypatch.setattr(ffmpeg_local, "VERTICAL", (108, 192))
    img = tmp_path / "p.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x48",
                    "-frames:v", "1", str(img)], check=True)
    a = await ffmpeg_local.ken_burns(img, tmp_path / "a.mp4", 1.0, variant=0)
    b = await ffmpeg_local.ken_burns(img, tmp_path / "b.mp4", 1.0, variant=1)
    cat = await ffmpeg_local.concat_videos([a, b], tmp_path / "cat.mp4")
    assert abs((await ffmpeg_local.probe_duration(cat)) - 2.0) < 0.2
    audio = tmp_path / "t.mp3"
    audio.write_bytes(mp3_bytes)
    muxed = await ffmpeg_local.mux_audio(cat, audio, tmp_path / "m.mp4")
    words = ffmpeg_local.estimate_alignment("hello there friend", 1.0)
    final = await ffmpeg_local.burn_captions(muxed, words, tmp_path / "final.mp4")
    assert final.exists() and final.stat().st_size > 0
    assert (tmp_path / "final.ass").read_text().count("Dialogue:") >= 2


@needs_ffmpeg
async def test_ffmpeg_local_split_and_concat_audio_round_trip(tmp_path, mp3_bytes):
    src = tmp_path / "src.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=6",
                    "-c:a", "libmp3lame", str(src)], check=True)
    parts = await ffmpeg_local.split_audio(src, tmp_path, [3.0, 3.0])
    assert len(parts) == 2
    joined = await ffmpeg_local.concat_audio(parts, tmp_path / "joined.mp3")
    assert abs((await ffmpeg_local.probe_duration(joined)) - 6.0) < 0.3


@needs_ffmpeg
async def test_ffmpeg_failure_surfaces_as_provider_error(tmp_path):
    with pytest.raises(ffmpeg_local.FfmpegError):
        await ffmpeg_local.normalize_vertical(tmp_path / "missing.mp4", tmp_path / "o.mp4")


@needs_ffmpeg
async def test_normalize_holds_last_frame_so_short_shots_never_shorten_the_cut(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg_local, "VERTICAL", (108, 192))
    short = tmp_path / "short.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x112:r=30:d=1",
                    "-pix_fmt", "yuv420p", str(short)], check=True)
    padded = await ffmpeg_local.normalize_vertical(short, tmp_path / "padded.mp4", duration_s=4.0)
    assert abs((await ffmpeg_local.probe_duration(padded)) - 4.0) < 0.15
    trimmed = await ffmpeg_local.normalize_vertical(padded, tmp_path / "trimmed.mp4", duration_s=2.0)
    assert abs((await ffmpeg_local.probe_duration(trimmed)) - 2.0) < 0.15
