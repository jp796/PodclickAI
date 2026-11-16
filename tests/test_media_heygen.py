"""
HeyGen adapter + Twin Video runner.

Everything runs against the real media registry and the real adapters. Provider
HTTP is answered by httpx.MockTransport (an unexpected call fails the test),
ffmpeg is real on tiny fixtures, and the Claude call and the stock-footage step
are patched — nothing leaves the process and nothing is spent.
"""

import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

from services.agents.contract import AgentContext
from services.agents.runners import avatar_video, twin_video
from services.media import base, ffmpeg_local, heygen, registry
from services.media.base import GenerationRequest

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")

HG = "api.heygen.com"
EL = "api.elevenlabs.io"
CDN = "files.heygen.ai"
SRT = ("1\n00:00:00,000 --> 00:00:01,500\nHere's the deal on Springfield.\n\n"
       "2\n00:00:01,500 --> 00:00:03,000\nThis market rewards people who move first.\n")


class Net:
    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.handler: Optional[Callable[[httpx.Request], httpx.Response]] = None

    def _dispatch(self, request):
        self.requests.append(request)
        if self.handler is None:
            raise AssertionError(f"unexpected network call: {request.method} {request.url}")
        return self.handler(request)

    def calls(self, method: str, host: str, path_prefix: str = "") -> List[httpx.Request]:
        return [r for r in self.requests
                if r.method == method and r.url.host == host and r.url.path.startswith(path_prefix)]


class FakeCtx:
    def __init__(self, out_dir: Path, bc: Any = None):
        self.job_id = "job123"
        self.location_id = "loc"
        self.output_dir = out_dir
        self.steps: List[Dict[str, Any]] = []
        self.outputs: List[Any] = []
        self.warnings: List[str] = []
        self.usage: List[Dict[str, Any]] = []
        self.bc_calls: List[Any] = []
        self._bc = bc

    @asynccontextmanager
    async def step(self, key, label):
        rec = {"key": key, "label": label, "status": "running"}
        self.steps.append(rec)
        try:
            yield
            rec["status"] = "completed"
        except BaseException:
            rec["status"] = "failed"
            raise

    def skip_step(self, key, label, reason=""):
        self.steps.append({"key": key, "label": label, "status": "skipped"})

    async def brand_context(self, task_type, topic=None):
        self.bc_calls.append((task_type, topic))
        return self._bc

    def media(self, capability):
        return registry.get_provider(capability)

    def add_output(self, output):
        self.outputs.append(output)

    def warn(self, msg):
        self.warnings.append(msg)

    def add_usage(self, usage):
        self.usage.append(usage)

    def check_cancelled(self):
        return None

    def out(self, kind):
        return [o for o in self.outputs if o.kind == kind]

    def step_keys(self, status="completed"):
        return [s["key"] for s in self.steps if s["status"] == status]


BRAND = SimpleNamespace(
    brand_profile=SimpleNamespace(full_name="JP Fluellen", niche_primary="real estate", market_city="Springfield, MO"),
    voice_profile=SimpleNamespace(tone=["direct", "warm"], cadence="short punchy sentences"),
    vocabulary=SimpleNamespace(use=["here's the deal"], avoid=["synergy"]),
    voice_samples=[SimpleNamespace(text="Here's the deal — this market rewards people who move first.")],
)

KEYS = dict(elevenlabs_api_key="el-key", elevenlabs_voice_id="voice1",
            heygen_api_key="hg-key", heygen_avatar_id="twin_look_1")


def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="session")
def fx(tmp_path_factory):
    d = tmp_path_factory.mktemp("twin")
    _ff("-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-c:a", "libmp3lame", str(d / "v3.mp3"))
    _ff("-f", "lavfi", "-i", "color=c=blue:s=192x108:r=30:d=3", "-f", "lavfi", "-i", "sine=frequency=330:duration=3",
        "-shortest", "-pix_fmt", "yuv420p", "-c:a", "aac", str(d / "twin.mp4"))
    return {"v3": (d / "v3.mp3").read_bytes(), "mp4": (d / "twin.mp4").read_bytes()}


@pytest.fixture
def env(monkeypatch, tmp_path, fx):
    net = Net()
    monkeypatch.setattr(base, "HTTP_TRANSPORT", httpx.MockTransport(net._dispatch))
    values: Dict[str, Any] = {}

    def fake_setting(name, default=None):
        v = values.get(name)
        return default if v is None or v == "" else v

    monkeypatch.setattr(base, "setting", fake_setting)
    sleeps: List[float] = []

    async def no_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(base, "sleep", no_sleep)

    scripts: List[str] = []
    drafts: List[Dict[str, Any]] = []

    async def fake_complete(system, user, max_tokens=900):
        drafts.append({"system": system, "user": user, "max_tokens": max_tokens})
        return scripts.pop(0) if scripts else "Here's the deal. This market rewards people who move first."

    monkeypatch.setattr(avatar_video, "_complete", fake_complete)

    broll_calls: List[Dict[str, Any]] = []

    def fake_broll(video, segments, dest, cache, openai_key, pexels_key):
        broll_calls.append({"video": video, "segments": segments})
        shutil.copyfile(video, dest)
        return dest

    monkeypatch.setattr(twin_video, "_add_broll_sync", fake_broll)

    ctx = FakeCtx(tmp_path / "out", bc=BRAND)
    return SimpleNamespace(net=net, cfg=values, ctx=ctx, fx=fx, tmp=tmp_path, sleeps=sleeps,
                           scripts=scripts, drafts=drafts, broll=broll_calls)


def heygen_ok(env, polls_before_done: int = 1, srt: Optional[str] = SRT, status_final: str = "completed"):
    """ElevenLabs TTS + the whole HeyGen happy path."""
    state = {"polls": 0}

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.host == EL:
            return httpx.Response(200, content=env.fx["v3"])
        if r.url.host == HG and r.method == "POST" and r.url.path == "/v3/assets":
            return httpx.Response(200, json={"data": {"asset_id": "asset_9", "url": "https://x/a.mp3",
                                                      "mime_type": "audio/mpeg", "size_bytes": 1}})
        if r.url.host == HG and r.method == "POST" and r.url.path == "/v3/videos":
            return httpx.Response(200, json={"data": {"video_id": "v_1", "status": "waiting"}})
        if r.url.host == HG and r.method == "GET" and r.url.path == "/v3/videos/v_1":
            state["polls"] += 1
            if state["polls"] <= polls_before_done:
                return httpx.Response(200, json={"data": {"id": "v_1", "status": "processing"}})
            if status_final == "failed":
                return httpx.Response(200, json={"data": {"id": "v_1", "status": "failed",
                                                          "failure_code": "render_error"}})
            data = {"id": "v_1", "status": "completed", "duration": 3.0,
                    "video_url": f"https://{CDN}/video/v_1.mp4"}
            if srt is not None:
                data["subtitle_url"] = f"https://{CDN}/srt/v_1.srt"
            return httpx.Response(200, json={"data": data})
        if r.url.host == CDN and r.url.path.endswith(".mp4"):
            return httpx.Response(200, content=env.fx["mp4"])
        if r.url.host == CDN and r.url.path.endswith(".srt"):
            return httpx.Response(200, content=(srt or "").encode())
        raise AssertionError(f"unexpected {r.method} {r.url}")

    return handler


def voice_file(env) -> Path:
    p = env.tmp / "voice.mp3"
    p.write_bytes(env.fx["v3"])
    return p


def twin_req(env, **over) -> GenerationRequest:
    kw = dict(capability="twin_video", prompt="", duration_s=3, source_audio=voice_file(env), aspect="16:9")
    kw.update(over)
    return GenerationRequest(**kw)


# ── registry ──────────────────────────────────────────────────────────────────

def test_twin_capability_is_heygen_only_and_never_leaks_into_avatar_video(env):
    assert registry.get_provider("twin_video") is None            # no key → None, never raises
    env.cfg.update(heygen_api_key="k")
    assert registry.get_provider("twin_video").name == "heygen"
    assert registry.get_provider("avatar_video") is None          # HeyGen must not answer the photo path
    assert registry.missing_requirements(["heygen"]) == []
    env.cfg.clear()
    assert registry.missing_requirements(["heygen"]) == ["heygen"]
    assert env.net.requests == []


# ── adapter: nothing spent on bad config ──────────────────────────────────────

@pytest.mark.parametrize("cfg, needle", [
    (dict(), "HEYGEN_API_KEY"),
    (dict(heygen_api_key="k"), "HEYGEN_AVATAR_ID"),
    (dict(heygen_api_key="k", heygen_avatar_id="a", heygen_engine="avatar_x"), "HEYGEN_ENGINE"),
    (dict(heygen_api_key="k", heygen_avatar_id="a", heygen_resolution="8k"), "HEYGEN_RESOLUTION"),
    (dict(heygen_api_key="k", heygen_avatar_id="a", heygen_base_url="http://evil.example"), "https"),
])
async def test_bad_config_fails_before_any_call(env, cfg, needle):
    env.cfg.update(cfg)
    with pytest.raises(base.ProviderNotConfigured) as ei:
        await heygen.HeyGenProvider().generate(twin_req(env), env.tmp / "o.mp4")
    assert needle in ei.value.user_message
    assert env.net.requests == []


async def test_seconds_cap_and_missing_audio_fail_before_any_call(env):
    env.cfg.update(KEYS, heygen_max_seconds=2)
    p = heygen.HeyGenProvider()
    with pytest.raises(base.ProviderCapExceeded) as ei:
        await p.generate(twin_req(env), env.tmp / "o.mp4")
    assert "HEYGEN_MAX_SECONDS" in ei.value.user_message
    env.cfg.update(heygen_max_seconds=600)
    with pytest.raises(ValueError):
        await p.generate(twin_req(env, source_audio=env.tmp / "nope.mp3"), env.tmp / "o.mp4")
    with pytest.raises(ValueError):
        await p.generate(twin_req(env, aspect="2:1"), env.tmp / "o.mp4")
    assert env.net.requests == []


def test_cap_can_never_exceed_heygens_thirty_minutes(env):
    env.cfg.update(KEYS, heygen_max_seconds=99999)
    assert heygen.HeyGenProvider().max_seconds == 30 * 60


# ── adapter: the happy path, on the v3 surface ────────────────────────────────

async def test_generate_uploads_audio_creates_once_polls_and_downloads(env):
    env.cfg.update(KEYS, heygen_engine="avatar_v")
    env.net.handler = heygen_ok(env, polls_before_done=2)
    dest = env.tmp / "o.mp4"
    asset = await heygen.HeyGenProvider().generate(twin_req(env, aspect="9:16"), dest)

    uploads = env.net.calls("POST", HG, "/v3/assets")
    creates = env.net.calls("POST", HG, "/v3/videos")
    assert len(uploads) == 1 and len(creates) == 1
    assert uploads[0].headers["x-api-key"] == "hg-key"
    assert b'name="file"' in uploads[0].content and env.fx["v3"] in uploads[0].content
    body = json.loads(creates[0].content)
    assert body == {"type": "avatar", "avatar_id": "twin_look_1", "audio_asset_id": "asset_9",
                    "aspect_ratio": "9:16", "resolution": "1080p", "engine": {"type": "avatar_v"}}
    assert "script" not in body and "voice_id" not in body        # the voice is always our recording
    assert creates[0].headers["Idempotency-Key"].startswith("pc-video-")
    assert len(env.net.calls("GET", HG, "/v3/videos/v_1")) == 3
    assert env.sleeps[:3] == [5.0, 7.5, 11.25]

    assert dest.read_bytes() == env.fx["mp4"]
    assert Path(asset.meta["subtitle_path"]).read_text() == SRT
    assert asset.usage == {"provider": "heygen", "unit": "seconds", "amount": 3}
    cdn = [r for r in env.net.requests if r.url.host == CDN]
    assert cdn and all("x-api-key" not in r.headers for r in cdn)  # key never follows a presigned link
    assert not any(r.url.path.startswith(("/v1", "/v2")) for r in env.net.requests)   # retired surface


async def test_engine_is_omitted_when_unset_so_heygen_picks_its_default(env):
    env.cfg.update(KEYS)
    env.net.handler = heygen_ok(env)
    await heygen.HeyGenProvider().generate(twin_req(env), env.tmp / "o.mp4")
    assert "engine" not in json.loads(env.net.calls("POST", HG, "/v3/videos")[0].content)


async def test_missing_subtitles_never_fail_a_paid_render(env):
    env.cfg.update(KEYS)
    ok = heygen_ok(env)

    def handler(r):
        if r.url.host == CDN and r.url.path.endswith(".srt"):
            return httpx.Response(404)
        return ok(r)

    env.net.handler = handler
    asset = await heygen.HeyGenProvider().generate(twin_req(env), env.tmp / "o.mp4")
    assert "subtitle_path" not in asset.meta and asset.path.exists()


# ── adapter: the create call is never retried ─────────────────────────────────

@pytest.mark.parametrize("answer, exc, needle", [
    (lambda: httpx.Response(401, json={"error": {"code": "unauthorized"}}), base.ProviderAuthError, "HEYGEN_API_KEY"),
    (lambda: httpx.Response(400, json={"error": {"code": "invalid_parameter",
                                                 "message": "Insufficient credits"}}),
     base.ProviderQuotaError, "credits"),
    (lambda: httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}}), base.ProviderRateLimited, "wait"),
    (lambda: httpx.Response(409, json={"error": {"code": "resource_not_ready"}}), base.ProviderError, "training"),
    (lambda: httpx.Response(400, json={"error": {"code": "avatar_not_usable"}}), base.ProviderError, "consent"),
    (lambda: httpx.Response(503), base.ProviderUncertainError, "before running again"),
    (lambda: httpx.Response(200, json={"data": {}}), base.ProviderUncertainError, "before running again"),
])
async def test_create_failures_map_and_are_never_retried(env, answer, exc, needle):
    env.cfg.update(KEYS)
    ok = heygen_ok(env)

    def handler(r):
        if r.method == "POST" and r.url.path == "/v3/videos":
            return answer()
        return ok(r)

    env.net.handler = handler
    with pytest.raises(exc) as ei:
        await heygen.HeyGenProvider().generate(twin_req(env), env.tmp / "o.mp4")
    assert needle in ei.value.user_message
    assert "hg-key" not in ei.value.user_message and "hg-key" not in str(ei.value)
    assert len(env.net.calls("POST", HG, "/v3/videos")) == 1
    assert not (env.tmp / "o.mp4").exists()


async def test_create_timeout_is_uncertain_not_retried(env):
    env.cfg.update(KEYS)
    ok = heygen_ok(env)

    def handler(r):
        if r.method == "POST" and r.url.path == "/v3/videos":
            raise httpx.ReadTimeout("slow", request=r)
        return ok(r)

    env.net.handler = handler
    with pytest.raises(base.ProviderUncertainError):
        await heygen.HeyGenProvider().generate(twin_req(env), env.tmp / "o.mp4")
    assert len(env.net.calls("POST", HG, "/v3/videos")) == 1


async def test_failed_render_and_poll_timeout(env):
    env.cfg.update(KEYS)
    env.net.handler = heygen_ok(env, status_final="failed")
    with pytest.raises(base.ProviderRejected):
        await heygen.HeyGenProvider().generate(twin_req(env), env.tmp / "o.mp4")

    env.net.requests.clear()
    env.cfg.update(heygen_poll_timeout_s=20)
    env.net.handler = heygen_ok(env, polls_before_done=10 ** 6)
    with pytest.raises(base.ProviderUncertainError) as ei:
        await heygen.HeyGenProvider().generate(twin_req(env), env.tmp / "o.mp4")
    assert "still rendering" in ei.value.user_message
    assert len(env.net.calls("POST", HG, "/v3/videos")) == 1      # gave up waiting, did not resubmit


# ── runner: timing helpers ────────────────────────────────────────────────────

def test_srt_timing_keeps_words_inside_their_own_cue():
    cues = twin_video.parse_srt(SRT)
    assert [(c["start"], c["end"]) for c in cues] == [(0.0, 1.5), (1.5, 3.0)]
    words = twin_video.words_from_cues(cues)
    assert words[0]["word"] == "Here's" and words[0]["start"] == 0.0
    first_of_second = next(w for w in words if w["word"] == "This")
    assert first_of_second["start"] == 1.5
    assert all(w["end"] <= 3.0 + 1e-6 and w["end"] >= w["start"] for w in words)


def test_timing_falls_back_to_an_estimate_without_srt(tmp_path):
    words, segs, source = twin_video.build_timing("one two three four", 4.0, None)
    assert source == "estimated" and len(words) == 4 and segs[0]["text"] == "one two three four"
    bad = tmp_path / "junk.srt"
    bad.write_text("not an srt")
    assert twin_video.build_timing("one two", 2.0, str(bad))[2] == "estimated"


# ── runner: nothing spent when it can't finish ────────────────────────────────

async def test_runner_missing_heygen_spends_nothing(env):
    env.cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v")
    with pytest.raises(RuntimeError) as ei:
        await twin_video.run(env.ctx, {"script": "Hi there."})
    assert "HeyGen not connected" in str(ei.value)
    assert env.net.requests == []


async def test_runner_missing_twin_id_spends_nothing(env):
    env.cfg.update({k: v for k, v in KEYS.items() if k != "heygen_avatar_id"})
    with pytest.raises(RuntimeError) as ei:
        await twin_video.run(env.ctx, {"script": "Hi there."})
    assert "HEYGEN_AVATAR_ID" in str(ei.value)
    assert env.net.requests == []


async def test_runner_length_over_voice_cap_stops_before_drafting(env):
    env.cfg.update(KEYS, elevenlabs_max_chars=5000)
    with pytest.raises(RuntimeError) as ei:
        await twin_video.run(env.ctx, {"topic": "the spring market", "length": "8"})
    assert "ELEVENLABS_MAX_CHARS" in str(ei.value)
    assert env.net.requests == [] and env.drafts == [] and env.ctx.bc_calls == []


async def test_runner_exact_seconds_cap_checked_after_voice_before_heygen(env):
    env.cfg.update(KEYS, heygen_max_seconds=2)                    # short script, but 3 s of audio
    env.net.handler = heygen_ok(env)
    with pytest.raises(RuntimeError) as ei:
        await twin_video.run(env.ctx, {"script": "Hi."})
    assert "HEYGEN_MAX_SECONDS" in str(ei.value)
    assert len(env.net.calls("POST", EL)) == 1
    assert [r for r in env.net.requests if r.url.host == HG] == []


# ── runner: the full build ────────────────────────────────────────────────────

async def test_runner_builds_voice_twin_cutaways_and_captions(env):
    env.cfg.update(KEYS, openai_api_key="oa", pexels_api_key="px")
    env.net.handler = heygen_ok(env)
    script = "Here's the deal on Springfield. This market rewards people who move first."
    await twin_video.run(env.ctx, {"script": script, "format": "landscape"})

    assert env.ctx.step_keys() == ["preflight", "voice", "twin", "cutaways", "captions"]
    assert env.ctx.bc_calls == [] and env.drafts == []            # verbatim script → no drafting
    assert json.loads(env.net.calls("POST", HG, "/v3/videos")[0].content)["aspect_ratio"] == "16:9"
    assert [s["text"] for s in env.broll[0]["segments"]] == [                 # cutaways planned on HeyGen's cues
        "Here's the deal on Springfield.", "This market rewards people who move first."]

    video = env.ctx.out("video")[0]
    assert video.label == "Twin video" and video.meta["ai_generated"] is True
    assert video.meta["cutaways"] is True and video.meta["captions"] is True
    assert video.meta["caption_timing"] == "heygen_srt" and video.meta["format"] == "landscape"
    final = env.ctx.output_dir / video.file
    assert abs((await ffmpeg_local.probe_duration(final)) - 3.0) < 0.5
    assert env.ctx.out("text")[0].value == script
    assert {u["provider"] for u in env.ctx.usage} == {"elevenlabs", "heygen"}
    assert env.ctx.warnings == []


async def test_runner_drafts_from_topic_through_foundation_with_room_for_long_form(env):
    env.cfg.update(KEYS, elevenlabs_max_chars=20000)
    env.net.handler = heygen_ok(env)
    env.scripts.append("Here's the deal on spring listings in Springfield.")
    await twin_video.run(env.ctx, {"topic": "why spring listings win", "length": "5", "format": "vertical",
                                   "broll": False, "captions": False})
    assert len(env.ctx.bc_calls) == 1
    assert "here's the deal" in env.drafts[0]["system"].lower()   # Foundation voice samples injected
    assert "5-minute" in env.drafts[0]["user"]
    assert env.drafts[0]["max_tokens"] > 900                      # long-form needs more than the short default
    assert json.loads(env.net.calls("POST", HG, "/v3/videos")[0].content)["aspect_ratio"] == "9:16"
    assert env.broll == []
    assert "cutaways" in env.ctx.step_keys("skipped") and "captions" not in env.ctx.step_keys()
    video = env.ctx.out("video")[0]
    assert video.meta["cutaways"] is False and video.meta["captions"] is False
    assert (env.ctx.output_dir / video.file).read_bytes() == env.fx["mp4"]


async def test_runner_refuses_a_draft_with_invented_numbers_before_spending(env):
    env.cfg.update(KEYS)
    env.net.handler = heygen_ok(env)
    env.scripts.extend(["Prices jumped 14 percent this spring.", "Prices jumped 14 percent again."])
    with pytest.raises(RuntimeError) as ei:
        await twin_video.run(env.ctx, {"topic": "the spring market", "length": "1"})
    assert "numbers I never gave you" in str(ei.value)
    assert env.net.requests == []


async def test_runner_keeps_you_on_camera_when_cutaways_are_unavailable(env, monkeypatch):
    env.cfg.update(KEYS)                                          # no Pexels / OpenAI key
    env.net.handler = heygen_ok(env)
    await twin_video.run(env.ctx, {"script": "Hi there Springfield.", "captions": False})
    assert env.broll == [] and any("PEXELS_API_KEY" in w for w in env.ctx.warnings)

    env.ctx.warnings.clear()
    env.cfg.update(openai_api_key="oa", pexels_api_key="px")
    monkeypatch.setattr(twin_video, "_add_broll_sync", lambda video, *a: video)   # planner found nothing
    await twin_video.run(env.ctx, {"script": "Hi there Springfield.", "captions": False})
    assert any("on camera the whole way" in w for w in env.ctx.warnings)
    assert env.ctx.out("video")[-1].meta["cutaways"] is False


async def test_runner_ships_clean_when_the_caption_pass_fails(env, monkeypatch):
    env.cfg.update(KEYS)
    env.net.handler = heygen_ok(env)

    async def boom(*a, **k):
        raise ffmpeg_local.FfmpegError("no libass", provider="ffmpeg_local")

    monkeypatch.setattr(ffmpeg_local, "burn_captions", boom)
    await twin_video.run(env.ctx, {"script": "Hi there Springfield.", "broll": False})
    video = env.ctx.out("video")[0]
    assert video.meta["captions"] is False and (env.ctx.output_dir / video.file).exists()
    assert any("without them" in w for w in env.ctx.warnings)


# ── the usage bug this change fixes for every media runner ────────────────────

def test_real_context_records_the_usage_dict_runners_hand_it(tmp_path):
    job = {"id": "j", "steps": [], "outputs": [], "warnings": [], "usage": []}
    ctx = AgentContext(job, tmp_path, persist=lambda: None)
    ctx.add_usage({"provider": "heygen", "unit": "seconds", "amount": 42})   # what runners pass
    ctx.add_usage("elevenlabs", "characters", 900)                           # the original signature
    ctx.add_usage({})                                                        # nothing to record
    assert job["usage"] == [{"provider": "heygen", "unit": "seconds", "amount": 42},
                            {"provider": "elevenlabs", "unit": "characters", "amount": 900}]
