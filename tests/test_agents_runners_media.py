"""
Lane D — the four media runners (voiceover, avatar_video, home_tour, market_reel).

Runners are exercised end-to-end against: the real media registry and adapters,
provider HTTP answered by httpx.MockTransport (an unexpected call fails the
test), real ffmpeg on a tiny canvas, and a stand-in AgentContext that follows
the frozen AGENTS_HUB_SPEC §2.1 surface. If lane A's `services.agents.contract`
has not landed, a minimal stand-in module is installed for Output/AgentResult.

The Claude call (`avatar_video._complete`) and Pexels helpers are patched — no
model or stock calls leave the process.
"""

import json
import shutil
import subprocess
import sys
import types
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

# ── contract stand-in (only if lane A has not landed) ─────────────────────────
try:  # pragma: no cover — depends on merge order
    import services.agents.contract  # noqa: F401
except Exception:  # pragma: no cover
    _stub = types.ModuleType("services.agents.contract")

    @dataclass
    class Output:  # minimal §2.2 output shape
        kind: str
        label: str
        value: Any = None
        file: Optional[str] = None
        meta: Dict[str, Any] = field(default_factory=dict)
        schema: Optional[str] = None
        id: Optional[str] = None

    @dataclass
    class AgentResult:
        outputs: List[Any] = field(default_factory=list)
        commit: Any = None

    _stub.Output = Output
    _stub.AgentResult = AgentResult
    sys.modules["services.agents.contract"] = _stub

from services.agents.runners import avatar_video, home_tour, market_reel, voiceover  # noqa: E402
from services.media import base, ffmpeg_local, higgsfield, registry  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")


# ── harness ───────────────────────────────────────────────────────────────────

class Net:
    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.handler: Optional[Callable[[httpx.Request], httpx.Response]] = None

    def _dispatch(self, request):
        self.requests.append(request)
        if self.handler is None:
            raise AssertionError(f"unexpected network call: {request.method} {request.url}")
        return self.handler(request)

    def posts(self, host: str) -> List[httpx.Request]:
        return [r for r in self.requests if r.method == "POST" and r.url.host == host]


class FakeCtx:
    """The §2.1 AgentContext surface, recorded for assertions."""

    def __init__(self, out_dir: Path, bc: Any = None):
        self.job_id = "job123"
        self.location_id = "loc"
        self.initiator = "user"
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
        except BaseException as exc:
            rec["status"] = "failed"
            rec["error"] = str(exc)
            raise

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


BRAND = SimpleNamespace(
    brand_profile=SimpleNamespace(full_name="JP Fluellen", niche_primary="real estate", market_city="Springfield, MO"),
    voice_profile=SimpleNamespace(tone=["direct", "warm"], cadence="short punchy sentences"),
    vocabulary=SimpleNamespace(use=["here's the deal"], avoid=["synergy"]),
    voice_samples=[SimpleNamespace(text="Here's the deal — this market rewards people who move first.")],
)


def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="session")
def media_fx(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    _ff("-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-c:a", "libmp3lame", str(d / "v3.mp3"))
    _ff("-f", "lavfi", "-i", "sine=frequency=330:duration=20", "-c:a", "libmp3lame", str(d / "v20.mp3"))
    _ff("-f", "lavfi", "-i", "sine=frequency=330:duration=30", "-c:a", "libmp3lame", str(d / "v30.mp3"))
    _ff("-f", "lavfi", "-i", "color=c=blue:s=64x112:r=30:d=2", "-pix_fmt", "yuv420p", str(d / "clip.mp4"))
    _ff("-f", "lavfi", "-i", "color=c=green:s=64x48", "-frames:v", "1", str(d / "photo.png"))
    return {k: (d / f).read_bytes() for k, f in
            (("v3", "v3.mp3"), ("v20", "v20.mp3"), ("v30", "v30.mp3"), ("clip", "clip.mp4"), ("png", "photo.png"))}


@pytest.fixture
def env(monkeypatch, tmp_path, media_fx):
    """Isolated settings, network, filesystem roots and a small canvas."""
    net = Net()
    monkeypatch.setattr(base, "HTTP_TRANSPORT", httpx.MockTransport(net._dispatch))
    values: Dict[str, Any] = {}

    def fake_setting(name, default=None):
        v = values.get(name)
        return default if v is None or v == "" else v

    monkeypatch.setattr(base, "setting", fake_setting)

    async def no_sleep(s):
        return None

    monkeypatch.setattr(base, "sleep", no_sleep)
    monkeypatch.setattr(higgsfield, "_rand", lambda lo, hi: 1.0)
    higgsfield._SEMAPHORES.clear()
    monkeypatch.setattr(ffmpeg_local, "VERTICAL", (108, 192))

    data = tmp_path / "data"
    (data / "ai_persona").mkdir(parents=True)
    (data / "agent_uploads").mkdir(parents=True)
    monkeypatch.setattr(avatar_video, "DATA_DIR", data)
    monkeypatch.setattr(home_tour, "UPLOADS_DIR", data / "agent_uploads")
    monkeypatch.setattr(higgsfield, "LIKENESS_ROOTS", (data / "ai_persona", data / "agent_uploads"))

    drafts: List[Dict[str, str]] = []
    script_queue: List[str] = []

    async def fake_complete(system, user, max_tokens=900):
        drafts.append({"system": system, "user": user})
        return script_queue.pop(0) if script_queue else "Here's the deal. This home has light in every room."

    monkeypatch.setattr(avatar_video, "_complete", fake_complete)

    ctx = FakeCtx(tmp_path / "out", bc=BRAND)
    return SimpleNamespace(net=net, cfg=values, ctx=ctx, data=data, drafts=drafts,
                           scripts=script_queue, fx=media_fx, tmp=tmp_path)


def add_persona(env, photo_id="abc123", path: Optional[Path] = None):
    p = path or (env.data / "ai_persona" / f"{photo_id}.png")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(env.fx["png"])
    store = {"photos": [{"id": photo_id, "path": str(p)}]}
    (env.data / "ai_persona.json").write_text(json.dumps(store))
    return p


def add_uploads(env, n: int) -> List[str]:
    ids = []
    for i in range(n):
        uid = f"up{i}"
        (env.data / "agent_uploads" / f"{uid}.png").write_bytes(env.fx["png"])
        ids.append(uid)
    return ids


def el_answer(env, key="v3"):
    return lambda r: httpx.Response(200, content=env.fx[key])


def hf_and_el(env, voice="v3", fail_first_hf: Optional[Callable] = None):
    """ElevenLabs + Higgsfield + Higgsfield CDN handler."""
    state = {"n": 0}

    def handler(r: httpx.Request):
        host = r.url.host
        if host == "api.elevenlabs.io":
            return httpx.Response(200, content=env.fx[voice])
        if host == "api.higgsfield.ai" and r.method == "POST":
            state["n"] += 1
            if fail_first_hf and state["n"] == 1:
                return fail_first_hf(r)
            rid = f"rq{state['n']}"
            return httpx.Response(200, json={"status": "queued", "request_id": rid,
                                             "status_url": f"https://api.higgsfield.ai/requests/{rid}"})
        if host == "api.higgsfield.ai":
            rid = r.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"status": "completed",
                                             "video": {"url": f"https://cdn.higgsfield.ai/{rid}.mp4"}})
        if host == "cdn.higgsfield.ai":
            return httpx.Response(200, content=env.fx["clip"])
        raise AssertionError(f"unexpected {r.url}")

    return handler


# ── Voiceover ─────────────────────────────────────────────────────────────────

async def test_voiceover_without_key_stops_with_zero_network(env):
    with pytest.raises(RuntimeError) as ei:
        await voiceover.run(env.ctx, {"script": "Hello there."})
    assert "ELEVENLABS_API_KEY" in str(ei.value)
    assert env.net.requests == [] and env.ctx.outputs == []


async def test_voiceover_char_cap_checked_before_any_call(env):
    env.cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v", elevenlabs_max_chars=20)
    with pytest.raises(RuntimeError) as ei:
        await voiceover.run(env.ctx, {"script": "x" * 21})
    assert "ELEVENLABS_MAX_CHARS" in str(ei.value)
    assert env.net.requests == []


async def test_voiceover_needs_a_voice_before_any_call(env):
    env.cfg.update(elevenlabs_api_key="k")
    with pytest.raises(RuntimeError):
        await voiceover.run(env.ctx, {"script": "Hello."})
    assert env.net.requests == []


async def test_voiceover_speaks_verbatim_and_ships_audio_alignment_usage(env):
    env.cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="defaultVoice")
    env.net.handler = el_answer(env)
    script = "Springfield's market just shifted.  Here's what it means for you."
    await voiceover.run(env.ctx, {"script": script, "voice": "pickedVoice", "purpose": "ad read"})

    req = env.net.requests[0]
    assert "/v1/text-to-speech/pickedVoice" in str(req.url)
    body = json.loads(req.content)
    assert body["text"] == script                      # verbatim — no rewrite
    assert body["voice_settings"]["stability"] == 0.55  # ad-read preset
    audio = env.ctx.out("audio")[0]
    assert (env.ctx.output_dir / audio.file).stat().st_size > 0
    align = json.loads((env.ctx.output_dir / "alignment.json").read_text())
    assert align["source"] == "estimated" and align["words"][0]["word"] == "Springfield's"
    assert env.ctx.usage == [{"provider": "elevenlabs", "unit": "characters", "amount": len(script)}]
    assert env.ctx.bc_calls == []                      # Foundation untouched on purpose
    assert [s["key"] for s in env.ctx.steps] == ["preflight", "voice", "level"]


# ── Avatar Video ──────────────────────────────────────────────────────────────

AV_KEYS = dict(elevenlabs_api_key="k", elevenlabs_voice_id="v", hf_api_key="id", hf_api_secret="sec",
               podclick_public_media_base_url="https://studio.example.com")


async def test_avatar_missing_higgsfield_spends_nothing(env):
    env.cfg.update(elevenlabs_api_key="k", elevenlabs_voice_id="v",
                   podclick_public_media_base_url="https://studio.example.com")
    add_persona(env)
    with pytest.raises(RuntimeError) as ei:
        await avatar_video.run(env.ctx, {"script": "Hi there.", "persona_photo": "abc123"})
    assert "Higgsfield not connected" in str(ei.value)
    assert env.net.requests == []                      # not even the TTS call


async def test_avatar_without_public_origin_spends_nothing(env):
    env.cfg.update({k: v for k, v in AV_KEYS.items() if k != "podclick_public_media_base_url"})
    add_persona(env)
    with pytest.raises(RuntimeError) as ei:
        await avatar_video.run(env.ctx, {"script": "Hi there.", "persona_photo": "abc123"})
    assert "PODCLICK_PUBLIC_MEDIA_BASE_URL" in str(ei.value)
    assert env.net.requests == []


async def test_avatar_rejects_unknown_or_out_of_library_persona(env):
    env.cfg.update(AV_KEYS)
    add_persona(env, "abc123")
    with pytest.raises(ValueError):
        await avatar_video.run(env.ctx, {"script": "Hi.", "persona_photo": "zzz999"})
    stranger = env.tmp / "elsewhere" / "face.png"
    add_persona(env, "abc123", path=stranger)          # json points outside ai_persona/
    with pytest.raises(ValueError):
        await avatar_video.run(env.ctx, {"script": "Hi.", "persona_photo": "abc123"})
    with pytest.raises(ValueError):
        await avatar_video.run(env.ctx, {"script": "Hi.", "persona_photo": "../abc123"})
    assert env.net.requests == []


async def test_avatar_estimated_length_over_seconds_cap_spends_nothing(env):
    env.cfg.update(AV_KEYS, higgsfield_max_seconds=10)
    add_persona(env)
    script = " ".join(["word"] * 60)                   # ≈23 s spoken
    with pytest.raises(RuntimeError) as ei:
        await avatar_video.run(env.ctx, {"script": script, "persona_photo": "abc123"})
    assert "HIGGSFIELD_MAX_SECONDS" in str(ei.value)
    assert env.net.requests == []


async def test_avatar_exact_seconds_cap_checked_after_voice_before_any_submit(env):
    env.cfg.update(AV_KEYS, higgsfield_max_seconds=20)
    add_persona(env)
    env.net.handler = hf_and_el(env, voice="v30")      # short script, but 30 s of audio
    with pytest.raises(RuntimeError) as ei:
        await avatar_video.run(env.ctx, {"script": "Short and sweet.", "persona_photo": "abc123"})
    assert "HIGGSFIELD_MAX_SECONDS" in str(ei.value)
    assert env.net.posts("api.higgsfield.ai") == []
    assert len(env.net.posts("api.elevenlabs.io")) == 1


async def test_avatar_renders_one_shot_per_15s_segment_with_public_urls(env):
    env.cfg.update(AV_KEYS)
    persona = add_persona(env)
    env.net.handler = hf_and_el(env, voice="v20")
    await avatar_video.run(env.ctx, {"script": "Let me walk you through this market.", "persona_photo": "abc123",
                                     "captions": True})

    submits = env.net.posts("api.higgsfield.ai")
    assert len(submits) == 2                           # 20 s → 2 × 10 s
    bodies = [json.loads(r.content) for r in submits]
    base_url = "https://studio.example.com/api/agents/jobs/job123/files/"
    assert {b["audio_url"] for b in bodies} == {base_url + "voice_part_00.mp3", base_url + "voice_part_01.mp3"}
    assert all(b["image_url"] == base_url + "persona.png" for b in bodies)
    assert all(b["duration"] == 10 for b in bodies)
    for name in ("voice_part_00.mp3", "voice_part_01.mp3", "persona.png"):
        assert (env.ctx.output_dir / name).exists()    # what the public URLs will serve
    video = env.ctx.out("video")[0]
    assert video.meta["ai_generated"] is True
    assert abs(video.meta["duration_s"] - 20.0) < 0.5
    final = env.ctx.output_dir / video.file
    dur = await ffmpeg_local.probe_duration(final)
    assert abs(dur - 20.0) < 0.6
    assert {u["provider"] for u in env.ctx.usage} == {"elevenlabs", "higgsfield"}
    assert env.ctx.bc_calls == []                      # verbatim script → no drafting
    assert persona.exists()                            # original never moved


async def test_avatar_topic_drafts_through_foundation(env):
    env.cfg.update(AV_KEYS)
    add_persona(env)
    env.net.handler = hf_and_el(env, voice="v3")
    env.scripts.append("Here's the deal on Springfield right now.")
    await avatar_video.run(env.ctx, {"topic": "why spring listings win", "persona_photo": "abc123",
                                     "length": 30, "captions": False})
    assert len(env.ctx.bc_calls) == 1 and env.ctx.bc_calls[0][0] == "scout_remix_script"
    assert "here's the deal" in env.drafts[0]["system"].lower()       # voice samples injected
    assert "Never invent a statistic" in env.drafts[0]["system"]
    assert env.ctx.out("text")[0].value == "Here's the deal on Springfield right now."
    tts_body = json.loads(env.net.posts("api.elevenlabs.io")[0].content)
    assert tts_body["text"] == "Here's the deal on Springfield right now."


# ── shared script guards ──────────────────────────────────────────────────────

def test_fabricated_numbers_only_allows_user_supplied_figures():
    assert avatar_video.fabricated_numbers("Median is $289k with 31 days on market.",
                                           ["median $289k, DOM 31"]) == []
    assert avatar_video.fabricated_numbers("Prices rose 7% to $1,250,000.", ["median $289k"]) == ["1250000", "7"]
    assert avatar_video.fabricated_numbers("Inventory is tightening.", [""]) == []


def test_fair_housing_flags():
    assert avatar_video.fair_housing_flags("Perfect for families, in a safe neighborhood!") == [
        "perfect for families", "safe neighborhood"]
    assert avatar_video.fair_housing_flags("Three bedrooms and a big fenced yard.") == []
    assert avatar_video.fair_housing_flags("A singlestory ranch") == []    # no substring false-positive


async def test_draft_script_redrafts_once_then_refuses_invented_numbers(env):
    env.scripts.extend(["Prices jumped 12% this year.", "Prices jumped 14% this year."])
    with pytest.raises(RuntimeError) as ei:
        await avatar_video.draft_script(env.ctx, topic="t", brief="b", seconds=30, number_sources=[""])
    assert "numbers I never gave you" in str(ei.value)
    assert len(env.drafts) == 2
    assert "broke the rules" in env.drafts[1]["user"]


async def test_draft_script_accepts_clean_redraft(env):
    env.scripts.extend(["Prices jumped 12% this year.", "Prices are climbing this year."])
    out = await avatar_video.draft_script(env.ctx, topic="t", brief="b", seconds=30, number_sources=[""])
    assert out == "Prices are climbing this year."


# ── Home Tour ─────────────────────────────────────────────────────────────────

TOUR = dict(address="123 Elm St", price="$289,000", beds="3", baths="2", sqft="1,850",
            highlights="new roof, big yard", length=30)


async def test_home_tour_without_providers_falls_back_to_slow_zoom_with_warnings(env):
    ids = add_uploads(env, 4)
    env.scripts.append("Step inside 123 Elm St. Three bedrooms, two baths, a new roof and a big yard.")
    await home_tour.run(env.ctx, dict(TOUR, photos=ids, voice="on"))
    assert env.net.requests == []
    assert any("No voice" in w for w in env.ctx.warnings)
    video = env.ctx.out("video")[0]
    assert video.meta["motion_shots"]["higgsfield"] == 0 and video.meta["voice"] is False
    assert abs((await ffmpeg_local.probe_duration(env.ctx.output_dir / video.file)) - 30.0) < 0.6
    assert "Fair Housing" in env.drafts[0]["system"]


async def test_home_tour_photo_count_checked_before_anything(env):
    with pytest.raises(ValueError):
        await home_tour.run(env.ctx, dict(TOUR, photos=add_uploads(env, 3)))
    assert env.drafts == [] and env.net.requests == []


async def test_home_tour_upload_ids_cannot_escape_uploads_dir(env):
    (env.data / "secret.png").write_bytes(env.fx["png"])
    ids = add_uploads(env, 3) + ["../secret"]
    with pytest.raises(ValueError):
        await home_tour.run(env.ctx, dict(TOUR, photos=ids))
    assert env.drafts == []


async def test_home_tour_symlinked_upload_cannot_point_outside_uploads_dir(env):
    secret = env.data / "secret.png"
    secret.write_bytes(env.fx["png"])
    (env.data / "agent_uploads" / "evil.png").symlink_to(secret)   # id passes the regex
    ids = add_uploads(env, 3) + ["evil"]
    with pytest.raises(ValueError):
        await home_tour.run(env.ctx, dict(TOUR, photos=ids))
    assert env.drafts == []


async def test_home_tour_fair_housing_redraft_then_refuse(env):
    ids = add_uploads(env, 4)
    env.scripts.extend(["Perfect for families!", "Great for kids and close to churches."])
    with pytest.raises(RuntimeError) as ei:
        await home_tour.run(env.ctx, dict(TOUR, photos=ids))
    assert "Fair Housing" in str(ei.value)
    assert env.net.requests == []


async def test_home_tour_uses_higgsfield_per_photo_when_it_fits(env):
    env.cfg.update(hf_api_key="id", hf_api_secret="sec", podclick_public_media_base_url="https://studio.example.com")
    ids = add_uploads(env, 4)
    env.net.handler = hf_and_el(env)
    await home_tour.run(env.ctx, dict(TOUR, photos=ids, voice="off"))
    submits = env.net.posts("api.higgsfield.ai")
    assert len(submits) == 4                            # 30 s / 4 photos = 7.5 s → 8 s shots
    bodies = [json.loads(r.content) for r in submits]
    assert all(b["duration"] == 8 and "audio_url" not in b for b in bodies)
    assert bodies[0]["image_url"] == "https://studio.example.com/api/agents/jobs/job123/files/photo_00.png"
    video = env.ctx.out("video")[0]
    assert video.meta["motion_shots"]["higgsfield"] == 4 and video.meta["ai_generated"] is True


async def test_home_tour_uncertain_higgsfield_never_resubmits_and_falls_back(env):
    env.cfg.update(hf_api_key="id", hf_api_secret="sec", podclick_public_media_base_url="https://studio.example.com")
    ids = add_uploads(env, 4)

    def timeout(r):
        raise httpx.ReadTimeout("t", request=r)

    env.net.handler = hf_and_el(env, fail_first_hf=timeout)
    await home_tour.run(env.ctx, dict(TOUR, photos=ids, voice="off"))
    assert len(env.net.posts("api.higgsfield.ai")) == 1
    assert any("dashboard" in w and "slow-zoom" in w for w in env.ctx.warnings)
    assert env.ctx.out("video")[0].meta["motion_shots"]["higgsfield"] == 0


async def test_home_tour_without_public_origin_skips_higgsfield(env):
    env.cfg.update(hf_api_key="id", hf_api_secret="sec")
    ids = add_uploads(env, 4)
    await home_tour.run(env.ctx, dict(TOUR, photos=ids, voice="off"))
    assert env.net.requests == []
    assert any("PODCLICK_PUBLIC_MEDIA_BASE_URL" in w for w in env.ctx.warnings)


# ── Market Reel ───────────────────────────────────────────────────────────────

async def test_market_reel_captions_only_and_plates_without_providers(env, monkeypatch):
    calls = []
    monkeypatch.setattr(market_reel, "_pexels_search", lambda q, k, d: calls.append(q))
    env.scripts.append("Springfield is heating up. Median sits at $289k and homes sell in 31 days.")
    await market_reel.run(env.ctx, {"area": "Springfield, MO", "numbers": "median $289k, DOM 31", "length": 30})
    assert env.net.requests == [] and calls == []      # no key → Pexels never called
    assert any("captions only" in w for w in env.ctx.warnings)
    assert any("PEXELS_API_KEY" in w for w in env.ctx.warnings)
    video = env.ctx.out("video")[0]
    assert video.meta["voice"] is False
    assert abs((await ffmpeg_local.probe_duration(env.ctx.output_dir / video.file)) - 30.0) < 0.6
    assert "median $289k, DOM 31" in env.drafts[0]["user"]


async def test_market_reel_uses_stock_footage_with_key(env, monkeypatch):
    env.cfg.update(pexels_api_key="PX", elevenlabs_api_key="k", elevenlabs_voice_id="v")
    env.net.handler = el_answer(env, "v20")
    seen = []

    def fake_search(q, key, min_duration):
        seen.append((q, key))
        return f"https://videos.pexels.com/{len(seen)}.mp4"

    def fake_download(url, dest):
        Path(dest).write_bytes(env.fx["clip"])
        return True

    monkeypatch.setattr(market_reel, "_pexels_search", fake_search)
    monkeypatch.setattr(market_reel, "_pexels_download", fake_download)
    await market_reel.run(env.ctx, {"area": "Cheyenne, WY", "length": 30})
    assert seen and all(k == "PX" for _, k in seen)
    assert seen[0][0] == "Cheyenne, WY aerial"
    assert not any("plain plate" in w for w in env.ctx.warnings)
    video = env.ctx.out("video")[0]
    assert video.meta["voice"] is True
    assert abs((await ffmpeg_local.probe_duration(env.ctx.output_dir / video.file)) - 20.0) < 0.6


async def test_market_reel_never_ships_an_invented_stat(env):
    env.scripts.extend(["Prices rose 9% this quarter.", "Up 9% again, folks."])
    with pytest.raises(RuntimeError):
        await market_reel.run(env.ctx, {"area": "Springfield, MO", "length": 30})
    assert env.net.requests == []
    assert not (env.ctx.output_dir / "reel.mp4").exists()
