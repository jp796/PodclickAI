"""
Lane C — existing-logic wraps (AGENTS_HUB_SPEC §5, row C).

Every test here asserts behaviour, not source text. The 2026-09-30 lesson: a
grep-shaped test passed while the code it claimed to guard was disabled. Each
guard below was proven by breaking it and watching the test fail (see the lane C
report for the mutation log).

Runners are exercised through a FakeCtx (tests/agents_standin.py) so the exact
internal route calls are visible and nothing touches the network, the database,
GHL, YouTube or an LLM. The two route fixes in main.py are exercised through the
real FastAPI app with Foundation and the LLM client replaced.
"""
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tests import agents_standin

agents_standin.install()

from tests.agents_standin import FakeCtx, output_to_dict  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Contract #3 route fix — Trend Radar + Pillar Planner use the Foundation
# ═══════════════════════════════════════════════════════════════════════════

def _brand_ctx(pillars=None, audience="Relocation buyers moving from Chicago",
               full_name="JP Fluellen", sample_count=40):
    from schemas.foundation import (
        BrandContext, BrandProfileOut, VoiceProfileOut, VocabularyOut,
        VoiceSampleOut, RetrievalMetadata,
    )
    return BrandContext(
        brand_profile=BrandProfileOut(full_name=full_name, audience_primary=audience,
                                      market_city="Springfield, MO", pillars=pillars),
        voice_profile=VoiceProfileOut(tone=["direct", "warm"], cadence="short punchy", pov="first-person"),
        vocabulary=VocabularyOut(use=["here's the deal"], avoid=["synergy"]),
        voice_samples=[VoiceSampleOut(text="Here's the deal on Springfield rent.", source="podcast",
                                      weight=1.0, similarity=0.9)],
        foundation_score=0.65,
        metadata=RetrievalMetadata(retrieval_query="q", sample_count=sample_count,
                                   retrieved_at=datetime(2026, 10, 1)),
    )


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def foundation_app(monkeypatch):
    """The real studio_app with Foundation + LLM clients swapped for recorders."""
    import main
    import db.engine
    import services.foundation as foundation
    import pipeline.content as content
    import openai

    state = {"ctx": _brand_ctx(pillars=None), "ready": True, "prompts": [], "task_types": []}

    async def fake_ready(session=None, location_id=None, **kw):
        if not state["ready"]:
            raise foundation.BrandContextError("foundation_not_ready: Need at least 5 voice samples")

    async def fake_ctx(session=None, location_id=None, task_type=None, **kw):
        state["task_types"].append(task_type)
        return state["ctx"]

    class _Completions:
        def create(self, **kw):
            prompt = "\n".join(m["content"] for m in kw["messages"])
            state["prompts"].append(prompt)
            return _Resp(json.dumps({"calendar": [{"week": 1, "title": "t", "pillar": "p"}],
                                     "echo": prompt}))

    class _AsyncCompletions:
        async def create(self, **kw):
            prompt = "\n".join(m["content"] for m in kw["messages"])
            state["prompts"].append(prompt)
            return _Resp(json.dumps({"pillars": [], "echo": prompt}))

    class _Resp:
        def __init__(self, content):
            msg = type("M", (), {"content": content})
            self.choices = [type("C", (), {"message": msg})]

    class _Client:
        def __init__(self, *a, **kw):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    class _AsyncClient:
        def __init__(self, *a, **kw):
            self.chat = type("Chat", (), {"completions": _AsyncCompletions()})()

    monkeypatch.setattr(foundation, "assert_foundation_ready", fake_ready)
    monkeypatch.setattr(foundation, "get_brand_context", fake_ctx)
    monkeypatch.setattr(db.engine, "async_session", lambda: _FakeSession())
    monkeypatch.setattr(content, "_get_client", lambda: _Client())
    monkeypatch.setattr(openai, "AsyncOpenAI", _AsyncClient)

    from fastapi.testclient import TestClient
    return TestClient(main.studio_app), state


BLUEPRINT_A = [{"name": "Moving to Springfield", "weight": 0.5},
               {"name": "Flip Breakdowns", "weight": 0.3},
               {"name": "Ozarks Weekend Life", "weight": 0.2}]
BLUEPRINT_B = [{"name": "Cheyenne Market Pulse", "weight": 0.6},
               {"name": "Ranch Property Tours", "weight": 0.4}]


def test_trend_radar_route_output_changes_when_blueprint_pillars_change(foundation_app):
    client, state = foundation_app
    state["ctx"] = _brand_ctx(pillars=BLUEPRINT_A)
    r1 = client.post("/api/yt/content-calendar", json={"city": "Springfield, MO"})
    state["ctx"] = _brand_ctx(pillars=BLUEPRINT_B)
    r2 = client.post("/api/yt/content-calendar", json={"city": "Springfield, MO"})

    assert r1.status_code == 200 and r2.status_code == 200
    out1, out2 = r1.json()["echo"], r2.json()["echo"]
    assert out1 != out2, "same output for different Blueprints — Foundation is imported, not wired"
    for p in BLUEPRINT_A:
        assert p["name"] in out1
    for p in BLUEPRINT_B:
        assert p["name"] in out2
    # Blueprint pillars REPLACE the hardcoded five, they don't get appended to them.
    assert "Neighborhood Deep Dive" not in out1
    assert "Neighborhood Deep Dive" not in out2


def test_trend_radar_route_injects_audience_and_voice_and_uses_a_real_task_type(foundation_app):
    from schemas.foundation import BrandContextTaskType
    client, state = foundation_app
    state["ctx"] = _brand_ctx(pillars=BLUEPRINT_A, audience="Out-of-state investors")
    r = client.post("/api/yt/content-calendar", json={"city": "Springfield, MO"})
    assert r.status_code == 200
    prompt = r.json()["echo"]
    assert "Out-of-state investors" in prompt
    assert "Here's the deal on Springfield rent." in prompt  # voice sample reached the model
    assert state["task_types"] == [BrandContextTaskType.podcast_script_outline]
    assert r.json()["_sample_count"] == 40


def test_trend_radar_route_keeps_the_five_defaults_when_blueprint_has_no_pillars(foundation_app):
    client, state = foundation_app
    state["ctx"] = _brand_ctx(pillars=[])
    r = client.post("/api/yt/content-calendar", json={"city": "Springfield, MO"})
    assert r.status_code == 200
    prompt = r.json()["echo"]
    for name in ("Relocation", "Market Updates", "Neighborhood Deep Dive", "Home Tour",
                 "Lifestyle & Community"):
        assert name in prompt


def test_trend_radar_route_refuses_when_foundation_is_not_poured(foundation_app):
    client, state = foundation_app
    state["ready"] = False
    r = client.post("/api/yt/content-calendar", json={"city": "Springfield, MO"})
    assert r.status_code == 422
    assert r.json().get("foundation_not_ready") is True
    assert state["prompts"] == [], "LLM was called without the Foundation"


def test_pillar_plan_route_uses_blueprint_pillars_weights_and_name(foundation_app):
    client, state = foundation_app
    state["ctx"] = _brand_ctx(pillars=BLUEPRINT_B, full_name="JP Fluellen")
    r = client.post("/api/yt/pillar-plan", json={"market": "Cheyenne, WY"})
    assert r.status_code == 200
    prompt = r.json()["echo"]
    assert "Cheyenne Market Pulse" in prompt and "Ranch Property Tours" in prompt
    assert "60%" in prompt and "40%" in prompt  # weights reach the model
    assert "JP Fluellen" in prompt              # agent_name defaults to Blueprint full_name
    assert "Neighborhood Deep Dive" not in prompt

    state["ctx"] = _brand_ctx(pillars=BLUEPRINT_A)
    r2 = client.post("/api/yt/pillar-plan", json={"market": "Cheyenne, WY"})
    assert r2.json()["echo"] != prompt


def test_pillar_plan_route_refuses_when_foundation_is_not_poured(foundation_app):
    client, state = foundation_app
    state["ready"] = False
    r = client.post("/api/yt/pillar-plan", json={"market": "Cheyenne, WY"})
    assert r.status_code == 422 and r.json().get("foundation_not_ready") is True
    assert state["prompts"] == []


# ═══════════════════════════════════════════════════════════════════════════
# 2. Content Scheduler — deterministic plan + idempotent commit
# ═══════════════════════════════════════════════════════════════════════════

class _RecordingSession:
    """Async-session double: records adds/commits, assigns UUIDs on flush."""

    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, obj):
        self.log["added"].append(obj)

    async def flush(self):
        for obj in self.log["added"]:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.log["commits"] += 1


@pytest.fixture
def scheduler_env(monkeypatch, tmp_path):
    from services.agents.runners import content_scheduler as cs
    sched_file = tmp_path / "scheduler.json"
    sched_file.write_text(json.dumps({"shoot_days": ["Monday", "Wednesday", "Friday"],
                                      "market": "Springfield, MO",
                                      "topics": [{"title": "Existing topic", "pillar": "Relocation"}]}))
    log = {"added": [], "commits": 0}
    monkeypatch.setattr(cs, "SCHEDULER_FILE", sched_file)
    monkeypatch.setattr(cs, "_session_factory", lambda: (lambda: _RecordingSession(log)))

    async def fake_mix(location_id):
        return {"viral": 0.4, "brand": 0.3, "personal": 0.2, "conversion": 0.1}
    monkeypatch.setattr(cs, "_load_vyral_mix", fake_mix)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    return cs, sched_file, log, out_dir


TOPICS_TEXT = "Moving to Springfield in 2026\nSpringfield vs Branson cost of living\nBest Springfield suburbs\nExisting topic"


def test_scheduler_plan_is_deterministic_and_respects_cadence_and_shoot_days(scheduler_env):
    cs, _, _, out_dir = scheduler_env
    inp = {"topics": TOPICS_TEXT, "start_date": "2026-10-05", "cadence": "3/week",
           "platforms": ["linkedin", "instagram"]}
    ctx1, ctx2 = FakeCtx(output_dir=out_dir), FakeCtx(output_dir=out_dir)
    res = run(cs.run(ctx1, inp))
    run(cs.run(ctx2, inp))
    rows = ctx1.output("slot_plan")["value"]
    assert rows == ctx2.output("slot_plan")["value"], "placement must be deterministic"
    days = [date.fromisoformat(r["date"]) for r in rows]
    assert [d.strftime("%a") for d in days] == ["Mon", "Wed", "Fri", "Mon"]
    assert days[0] == date(2026, 10, 5)
    assert all(r["bucket"] in ("viral", "brand", "personal", "conversion") for r in rows)
    assert all(r["platforms"] == ["linkedin", "instagram"] for r in rows)
    assert res.commit is not None and "4 topics" in res.commit.summary


def test_scheduler_commit_is_idempotent_approve_twice_same_post_ids(scheduler_env):
    cs, sched_file, log, out_dir = scheduler_env
    ctx = FakeCtx(output_dir=out_dir)
    run(cs.run(ctx, {"topics": TOPICS_TEXT, "start_date": "2026-10-05", "cadence": "daily"}))
    job = ctx.as_job()

    first = run(cs.commit(ctx, job, {}))
    second = run(cs.commit(ctx, job, {}))   # e.g. Walk-through approve after /agents approve

    assert first["post_ids"] == second["post_ids"]
    assert len(first["post_ids"]) == 4
    assert len(log["added"]) == 4 and log["commits"] == 1, "second approve wrote to the DB again"
    titles = [t["title"] for t in json.loads(sched_file.read_text())["topics"]]
    assert titles.count("Moving to Springfield in 2026") == 1
    assert titles.count("Existing topic") == 1, "queue got a duplicate of an existing topic"


def test_scheduler_commit_writes_drafts_with_auto_plan_source_and_no_platform_specific(scheduler_env):
    from db.models import Post, PostVariant
    cs, sched_file, log, out_dir = scheduler_env
    ctx = FakeCtx(output_dir=out_dir)
    run(cs.run(ctx, {"topics": "Moving to Springfield in 2026", "start_date": "2026-10-05",
                     "cadence": "daily", "platforms": ["linkedin", "tiktok"]}))
    result = run(cs.commit(ctx, ctx.as_job(), {}))

    posts = [o for o in log["added"] if isinstance(o, Post)]
    assert len(posts) == 1
    p = posts[0]
    assert p.status == "draft" and p.source == "auto_plan"
    assert p.base_caption.startswith("Moving to Springfield in 2026")
    assert p.scheduled_at.isoformat().startswith("2026-10-05")
    # Constraint 5: no agent bookkeeping in platform_specific — so no variants at all here.
    assert not [o for o in log["added"] if isinstance(o, PostVariant)]
    assert ctx.job_id not in json.dumps(p.base_caption)
    assert result["post_ids"] == [str(p.id)]
    queued = json.loads(sched_file.read_text())["topics"][-1]
    assert set(queued) <= {"title", "pillar", "market", "notes"}


def test_scheduler_commit_refuses_after_an_interrupted_commit(scheduler_env):
    cs, _, log, out_dir = scheduler_env
    ctx = FakeCtx(output_dir=out_dir)
    run(cs.run(ctx, {"topics": "One topic", "start_date": "2026-10-05"}))
    (out_dir / cs.COMMIT_STARTED).write_text("{}")  # simulated crash mid-commit
    with pytest.raises(RuntimeError, match="interrupted"):
        run(cs.commit(ctx, ctx.as_job(), {}))
    assert log["added"] == []


def test_scheduler_reads_topics_from_a_finished_trend_radar_job(scheduler_env):
    cs, _, _, out_dir = scheduler_env
    radar_job = {"id": "tr-1", "agent_id": "trend_radar", "status": "done",
                 "input": {"city": "Springfield, MO"},
                 "outputs": [{"id": "topics", "kind": "cards", "value": [
                     {"title": "A", "pillar": "Relocation", "hook": "Hook A"},
                     {"title": "B", "pillar": "Home Tour", "hook": "Hook B"}]}]}
    ctx = FakeCtx(output_dir=out_dir, routes={"GET /api/agents/jobs/tr-1": (200, radar_job)})
    run(cs.run(ctx, {"topics_job": "tr-1", "start_date": "2026-10-05", "cadence": "daily"}))
    rows = ctx.output("slot_plan")["value"]
    assert [r["topic"] for r in rows] == ["A", "B"]
    assert rows[0]["hook"] == "Hook A" and rows[1]["pillar"] == "Home Tour"


def test_scheduler_refuses_an_unfinished_reference_job(scheduler_env):
    cs, _, _, out_dir = scheduler_env
    ctx = FakeCtx(output_dir=out_dir, routes={"GET /api/agents/jobs/tr-2": (
        200, {"id": "tr-2", "agent_id": "trend_radar", "status": "running", "outputs": []})})
    with pytest.raises(RuntimeError):
        run(cs.run(ctx, {"topics_job": "tr-2"}))


# ═══════════════════════════════════════════════════════════════════════════
# 3. Market Scout — quota-empty surfaces the quota message
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def scout(monkeypatch):
    from services.agents.runners import market_scout as ms
    spy = {}
    monkeypatch.setattr(ms, "_spy_jobs", lambda: spy)
    monkeypatch.setattr(ms, "POLL_FIRST_S", 0)
    monkeypatch.setattr(ms, "POLL_S", 0)
    return ms, spy


def _spy_route(spy, result, status="complete"):
    def handler(body):
        spy["spy-1"] = {"status": status, "result": result, "error": None if status != "error" else "boom"}
        return 200, {"job_id": "spy-1", "status": "running"}
    return handler


def test_market_scout_quota_empty_fails_with_the_quota_message(scout, tmp_path):
    ms, spy = scout
    empty = {"market_demand": "", "top_videos_ranked": []}
    ctx = FakeCtx(output_dir=tmp_path, routes={"POST /api/yt/competitor-spy": _spy_route(spy, empty)})
    with pytest.raises(RuntimeError) as ei:
        run(ms.run(ctx, {"city": "Springfield, MO"}))
    assert "quota" in str(ei.value).lower() and "midnight Pacific" in str(ei.value)
    assert ctx.step_status("scan") == "failed"


def test_market_scout_slow_empty_run_ships_with_a_warning_not_a_quota_failure(scout, tmp_path, monkeypatch):
    ms, spy = scout
    monkeypatch.setattr(ms, "QUOTA_WINDOW_S", -1)  # the run took longer than the quota window
    ctx = FakeCtx(output_dir=tmp_path, routes={"POST /api/yt/competitor-spy": _spy_route(
        spy, {"market_demand": "low", "top_videos_ranked": []})})
    run(ms.run(ctx, {"city": "Springfield, MO"}))
    assert ctx.output("scout_report")["value"]["market_demand"] == "low"
    assert ctx.warnings


def test_market_scout_ships_report_and_videos_and_skips_remix_when_foundation_not_ready(scout, tmp_path):
    ms, spy = scout
    videos = [{"title": f"V{i}", "channel": "C", "views": 1000 * i, "score": 2.0, "popular": True}
              for i in range(1, 4)]
    result = {"market_demand": "high", "best_format": "tours", "opportunity_gap": "gap",
              "hot_searches": ["x"], "market_standards": ["y"], "top_videos_ranked": videos}
    ctx = FakeCtx(output_dir=tmp_path, routes={
        "POST /api/yt/competitor-spy": _spy_route(spy, result),
        "POST /api/yt/scout-remix": (422, {"error": "foundation_not_ready", "foundation_not_ready": True}),
    })
    run(ms.run(ctx, {"city": "Springfield, MO", "remix_top": 2}))
    assert ctx.output("scout_report")["value"]["market_demand"] == "high"
    assert [v["title"] for v in ctx.output("videos")["value"]] == ["V1", "V2", "V3"]
    assert ctx.output("remixes") is None
    remix_calls = [c for c in ctx.calls if c[1] == "/api/yt/scout-remix"]
    assert len(remix_calls) == 1, "kept hammering scout-remix after Foundation said no"
    assert any("Foundation" in w for w in ctx.warnings)


def test_market_scout_surfaces_a_stalled_spy_job(scout, tmp_path):
    ms, spy = scout
    ctx = FakeCtx(output_dir=tmp_path, routes={"POST /api/yt/competitor-spy": _spy_route(spy, None, "error")})
    with pytest.raises(RuntimeError):
        run(ms.run(ctx, {"city": "Springfield, MO"}))


# ═══════════════════════════════════════════════════════════════════════════
# 4. Click Studio — refuses processing, skips auto-edit when cuts exist
# ═══════════════════════════════════════════════════════════════════════════

PID = "22222222-2222-2222-2222-222222222222"


def _project(status, meta=None, show_notes="Notes"):
    return {"id": PID, "title": "Ep", "status": status, "show_notes": show_notes,
            "legacy_metadata": meta or {}}


@pytest.fixture
def clickstudio(monkeypatch):
    from services.agents.runners import click_studio as cs
    monkeypatch.setattr(cs, "POLL_S", 0)
    monkeypatch.setattr(cs, "SHIP_IT_CEILING_S", 0.5)
    monkeypatch.setattr(cs, "BROLL_CEILING_S", 0.5)
    return cs


def _cs_routes(project_states, clips=None):
    seq = list(project_states)

    def get_project(body):
        return 200, (seq.pop(0) if len(seq) > 1 else seq[0])
    return {
        f"GET /api/projects/{PID}": get_project,
        f"POST /api/projects/{PID}/auto-edit": (200, {"ok": True, "cuts": 3}),
        f"POST /api/projects/{PID}/ship-it": (200, {"ok": True, "status": "processing"}),
        f"GET /api/projects/{PID}/clips": (200, clips if clips is not None else [
            {"id": "c1", "rendered_url": "/v", "status": "pending"},
            {"id": "c2", "rendered_url": "/v", "status": "removed"}]),
    }


def test_click_studio_refuses_a_project_already_on_the_line(clickstudio, tmp_path):
    ctx = FakeCtx(output_dir=tmp_path, routes=_cs_routes([_project("processing")]))
    with pytest.raises(RuntimeError, match="already on the line"):
        run(clickstudio.run(ctx, {"project_id": PID}))
    assert [c for c in ctx.calls if c[0] == "POST"] == [], "touched a project that was mid-processing"


def test_click_studio_skips_auto_edit_when_cuts_already_exist(clickstudio, tmp_path):
    states = [_project("review", {"manual_cut_regions": [[1.0, 2.0]]}),
              _project("processing"), _project("review", {"manual_cut_regions": [[1.0, 2.0]]})]
    ctx = FakeCtx(output_dir=tmp_path, routes=_cs_routes(states))
    run(clickstudio.run(ctx, {"project_id": PID, "auto_edit": True, "ship_it": True}))
    posted = [c[1] for c in ctx.calls if c[0] == "POST"]
    assert f"/api/projects/{PID}/auto-edit" not in posted, "auto-edit would cut the take twice"
    assert f"/api/projects/{PID}/ship-it" in posted
    assert any("auto-edit" in w.lower() for w in ctx.warnings)
    assert ctx.output("clips")["value"] == 1
    assert ctx.output("show_notes")["value"] == "Notes"


def test_click_studio_force_reedit_runs_auto_edit_over_existing_cuts(clickstudio, tmp_path):
    states = [_project("review", {"manual_cut_regions": [[1.0, 2.0]]}), _project("processing"),
              _project("review")]
    ctx = FakeCtx(output_dir=tmp_path, routes=_cs_routes(states))
    run(clickstudio.run(ctx, {"project_id": PID, "force_reedit": True}))
    posted = [c[1] for c in ctx.calls if c[0] == "POST"]
    assert posted[:2] == [f"/api/projects/{PID}/auto-edit", f"/api/projects/{PID}/ship-it"]


def test_click_studio_reports_a_failed_ship_it(clickstudio, tmp_path):
    states = [_project("recording_done"), _project("processing"), _project("failed")]
    ctx = FakeCtx(output_dir=tmp_path, routes=_cs_routes(states))
    with pytest.raises(RuntimeError, match="Ship It stalled"):
        run(clickstudio.run(ctx, {"project_id": PID, "auto_edit": False}))
    assert ctx.step_status("ship_it") == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Trend Radar / Pillar Planner runners
# ═══════════════════════════════════════════════════════════════════════════

def test_trend_radar_runner_hands_the_scout_report_to_the_route(tmp_path):
    from services.agents.runners import trend_radar as tr
    scout_job = {"id": "ms-1", "agent_id": "market_scout", "status": "done",
                 "outputs": [{"id": "scout_report", "kind": "json", "value": {"opportunity_gap": "tours"}}]}
    seen = {}

    def calendar(body):
        seen.update(body)
        return 200, {"calendar": [{"week": 1, "title": "T", "pillar": "P"}],
                     "_foundation_thin": True, "_sample_count": 9}
    ctx = FakeCtx(output_dir=tmp_path, routes={"GET /api/agents/jobs/ms-1": (200, scout_job),
                                               "POST /api/yt/content-calendar": calendar})
    run(tr.run(ctx, {"city": "Springfield, MO", "scout_job": "ms-1"}))
    assert seen["competitor_insights"] == {"opportunity_gap": "tours"}
    assert ctx.output("topics")["value"][0]["title"] == "T"
    assert any("Thin Foundation (9 samples)" in w for w in ctx.warnings)


def test_trend_radar_runner_fails_loudly_on_foundation_422(tmp_path):
    from services.agents.runners import trend_radar as tr
    ctx = FakeCtx(output_dir=tmp_path, routes={"POST /api/yt/content-calendar": (
        422, {"error": "foundation_not_ready: pour it", "foundation_not_ready": True})})
    with pytest.raises(RuntimeError):
        run(tr.run(ctx, {"city": "Springfield, MO"}))
    assert ctx.output("topics") is None


def test_pillar_planner_runner_maps_the_plan_into_cards(tmp_path):
    from services.agents.runners import pillar_planner as pp
    plan = {"pillars": [{"name": "Relocation", "lead_type": "fast", "frequency": "2x",
                         "video_ideas": ["Moving here", "Cost of living"]}]}
    ctx = FakeCtx(output_dir=tmp_path, routes={"POST /api/yt/pillar-plan": (200, plan)})
    run(pp.run(ctx, {"market": "Springfield, MO"}))
    cards = ctx.output("pillar_plan")["value"]
    assert cards[0]["pillar"] == "Relocation"
    assert [i["title"] for i in cards[0]["ideas"]] == ["Moving here", "Cost of living"]


def test_scheduler_flattens_a_pillar_planner_job_round_robin(scheduler_env):
    cs, _, _, out_dir = scheduler_env
    job = {"id": "pp-1", "agent_id": "pillar_planner", "status": "done", "input": {"market": "Springfield"},
           "outputs": [{"id": "pillar_plan", "kind": "cards", "value": [
               {"pillar": "A", "ideas": [{"title": "a1"}, {"title": "a2"}]},
               {"pillar": "B", "ideas": [{"title": "b1"}]}]}]}
    ctx = FakeCtx(output_dir=out_dir, routes={"GET /api/agents/jobs/pp-1": (200, job)})
    run(cs.run(ctx, {"topics_job": "pp-1", "start_date": "2026-10-05", "cadence": "daily"}))
    assert [r["topic"] for r in ctx.output("slot_plan")["value"]] == ["a1", "b1", "a2"]


# ═══════════════════════════════════════════════════════════════════════════
# 6. Draftsman — edited text commits and feeds the Foundation
# ═══════════════════════════════════════════════════════════════════════════

FORGE = {"linkedin": "LinkedIn draft about Springfield.", "facebook": "FB draft.",
         "instagram": "IG draft.", "x": "X draft.", "tiktok": "TikTok draft.",
         "_foundation_thin": False, "_sample_count": 120}


def test_draftsman_commit_uses_edited_text_and_ingests_with_the_right_source(monkeypatch, tmp_path):
    from db.models import Post, PostVariant
    from services.agents.runners import draftsman as dm
    log = {"added": [], "commits": 0}
    monkeypatch.setattr(dm, "_session_factory", lambda: (lambda: _RecordingSession(log)))
    ingested = []

    def ingest(body):
        ingested.append(body)
        return 200, {"sample_id": "s"}
    ctx = FakeCtx(output_dir=tmp_path, routes={"POST /api/social/forge": (200, FORGE),
                                               "POST /api/foundation/ingest": ingest})
    res = run(dm.run(ctx, {"mode": "idea", "topic": "Springfield rent", "market": "Springfield, MO"}))
    assert res.commit is not None
    job = ctx.as_job()
    # The human rewrote LinkedIn completely; everything else approved as drafted.
    for o in job["outputs"]:
        if o["id"] == "linkedin":
            o["value"] = "Completely different words I wrote myself, nothing like the draft at all."
    result = run(dm.commit(ctx, job, {}))
    again = run(dm.commit(ctx, job, {}))

    assert result == again and log["commits"] == 1
    posts = [o for o in log["added"] if isinstance(o, Post)]
    variants = {v.platform: v for v in log["added"] if isinstance(v, PostVariant)}
    assert len(posts) == 1 and posts[0].source == "post_forge" and posts[0].status == "draft"
    assert variants["linkedin"].caption.startswith("Completely different words")
    assert set(variants) == {"linkedin", "facebook", "instagram", "x", "tiktok"}
    assert all(not v.platform_specific for v in variants.values())
    by_platform = {b["platform"]: b for b in ingested}
    assert by_platform["linkedin"]["source"] == "social_edited"
    assert by_platform["facebook"]["source"] == "social_approved"
    assert len(ingested) == 5, "second approve re-ingested into the Foundation"


def test_draftsman_surfaces_foundation_not_ready(tmp_path):
    from services.agents.runners import draftsman as dm
    ctx = FakeCtx(output_dir=tmp_path, routes={"POST /api/social/forge": (
        422, {"error": "foundation_not_ready: pour it", "foundation_not_ready": True})})
    with pytest.raises(RuntimeError):
        run(dm.run(ctx, {"mode": "idea", "topic": "x"}))


# ═══════════════════════════════════════════════════════════════════════════
# 7. Repurpose (Clip Dispatcher)
# ═══════════════════════════════════════════════════════════════════════════

def test_repurpose_previews_top_clips_and_commits_through_distribute_shorts(tmp_path):
    from services.agents.runners import repurpose as rp
    clips = [
        {"id": "a", "rendered_url": "/x", "status": "pending", "virality_score": 3, "clip_caption": "Cap A", "hook_text": "h"},
        {"id": "b", "rendered_url": "/x", "status": "pending", "virality_score": 9, "clip_caption": None, "hook_text": "raw b"},
        {"id": "c", "rendered_url": "/x", "status": "removed", "virality_score": 99, "clip_caption": "nope"},
        {"id": "d", "rendered_url": None, "status": "pending", "virality_score": 50, "clip_caption": "unrendered"},
    ]
    sent = {}

    def distribute(body):
        sent.update(body)
        return 200, {"ok": True, "created": [{"platform": "instagram"}], "skipped": [],
                     "planner_url": "https://app.gohighlevel.com/"}
    ctx = FakeCtx(output_dir=tmp_path, routes={f"GET /api/projects/{PID}/clips": (200, clips),
                                               f"POST /api/projects/{PID}/distribute-shorts": distribute})
    res = run(rp.run(ctx, {"project_id": PID, "max_clips": 2}))
    cards = ctx.output("clips")["value"]
    assert [c["clip_id"] for c in cards] == ["b", "a"]
    assert cards[0]["video_url"].endswith("/clips/b/video.mp4")
    assert cards[0]["raw_hook"] is True and cards[0]["caption"] == "raw b"
    assert any("raw hook only" in w for w in ctx.warnings)
    assert res.commit is not None
    assert not [c for c in ctx.calls if "distribute-shorts" in c[1]], "build phase must not draft anything"

    result = run(rp.commit(ctx, ctx.as_job(input={"project_id": PID, "max_clips": 2,
                                                  "platforms": ["instagram", "tiktok"]}), {}))
    assert sent == {"platforms": ["instagram", "tiktok"], "max_clips": 2}
    assert result["created"] == [{"platform": "instagram"}]


# ═══════════════════════════════════════════════════════════════════════════
# 8. Painter (thumbnail) — renders real PNGs
# ═══════════════════════════════════════════════════════════════════════════

def test_render_thumbnail_makes_a_1280x720_png(tmp_path):
    from PIL import Image
    from services.agents.render_thumbnail import render_thumbnail
    persona = tmp_path / "face.png"
    Image.new("RGB", (600, 800), (200, 150, 120)).save(persona)
    out = tmp_path / "t.png"
    render_thumbnail(str(out), "MOVING HERE IN 2026?", background_color="#1a1a2e",
                     text_color="#ffd400", persona_path=str(persona))
    img = Image.open(out)
    assert img.size == (1280, 720)
    # Persona on the left, plate on the right — the composite actually happened.
    assert img.convert("RGB").getpixel((100, 600)) == (200, 150, 120), "persona photo not composited"


def test_thumbnail_runner_renders_three_variants_without_a_provider(tmp_path, monkeypatch):
    from services.agents.runners import thumbnail as th
    monkeypatch.setattr(th, "_persona_photos", lambda: [])
    concepts = {"variants": [
        {"thumbnail_text": f"TEXT {i}", "background_color": "#0d1b2a", "text_color": "#ffffff",
         "layout_description": "l", "image_prompt": "p"} for i in range(3)], "titles": ["a"]}
    ctx = FakeCtx(output_dir=tmp_path, routes={"POST /api/yt/cover-forge": (200, concepts)})
    run(th.run(ctx, {"format": "youtube_thumbnail", "title": "Moving to Springfield"}))
    images = ctx.output("variants")["value"]
    assert len(images) == 3
    for item in images:
        assert (tmp_path / item["file"]).exists()
        assert item["url"] == f"/api/agents/jobs/{ctx.job_id}/files/{item['file']}"
    assert ctx.output("concepts")["value"]["titles"] == ["a"]
