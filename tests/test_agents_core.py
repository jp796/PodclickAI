"""
The Crew core — registry, contract, job store, Brick wiring (AGENTS_HUB_SPEC lane A).

No database, no network, no real runners: runner modules are fakes injected into
sys.modules under the spec's own runner path, and every DB/Brick collaborator in
services.agents.jobs is replaced. The Brick dispatch branch is exercised for real
(BrickAgent._dispatch_action) so the Walk-through approval path is the code under
test, not a stand-in.

Each guard here was proven by breaking it and watching the test fail — see the
lane A report. A grep-shaped test that passes on dead code is worse than none.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
import uuid
from typing import Any, Dict, List, Optional

import pytest

from services.agents import jobs
from services.agents import internal
from services.agents.contract import (
    CATEGORY_IDS,
    FIELD_TYPES,
    FOUNDATION_MODES,
    REQUIREMENTS,
    AgentResult,
    CommitPlan,
    Output,
    RouteNotAllowed,
    StepError,
    validate_input,
)
from services.agents.registry import REGISTRY, agent_run_tier, get_agent

LOC = "11111111-1111-1111-1111-111111111111"


# ── shared harness (imported by test_agents_routes) ──────────────────────────

class Harness:
    """Fake collaborators for services.agents.jobs. One per test."""

    def __init__(self) -> None:
        self.actions: Dict[str, Dict[str, Any]] = {}
        self.tier = "draftsman"
        self.foundation = {"tier": "solid", "sample_count": 40, "has_blueprint": True}
        self.unmet: set = set()
        self.brick_rejects: List[str] = []

    async def foundation_summary(self, location_id):
        return dict(self.foundation)

    async def current_tier(self, location_id):
        return self.tier

    async def create_commit_action(self, job, spec, summary):
        aid = str(uuid.uuid4())
        self.actions[aid] = {"status": "pending", "action_type": "agent_commit",
                             "payload": {"job_id": job["id"], "agent_id": spec.id,
                                         "summary": summary}}
        return aid

    async def brick_approve(self, action_id):
        """Mirror BrickAgent.approve_action: pending gate, tier gate, real dispatch."""
        from services.brick_agent import ACTION_TIER_MAP, BrickAgent, _tier_allows

        action = self.actions.get(action_id)
        if action is None:
            raise ValueError("not found")
        if action["status"] != "pending":
            raise ValueError("not pending")
        action["status"] = "approved"
        if not _tier_allows(self.tier, ACTION_TIER_MAP[action["action_type"]]):
            raise PermissionError("tier")
        fake = types.SimpleNamespace(
            id=uuid.UUID(action_id), location_id=uuid.UUID(LOC),
            action_type=action["action_type"], payload=action["payload"],
        )
        result = await BrickAgent()._dispatch_action(fake, None)
        action["status"] = "executed"
        return result

    async def brick_reject(self, action_id, reason):
        action = self.actions.get(action_id)
        if action is None or action["status"] != "pending":
            raise ValueError("not pending")
        action["status"] = "rejected"
        self.brick_rejects.append(action_id)
        return {"ok": True}

    def requirement_met(self, req):
        return req not in self.unmet


def install(monkeypatch, tmp_path) -> Harness:
    h = Harness()
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    jobs._reset_for_tests()
    monkeypatch.setattr(jobs, "foundation_summary", h.foundation_summary)
    monkeypatch.setattr(jobs, "current_tier", h.current_tier)
    monkeypatch.setattr(jobs, "_create_commit_action", h.create_commit_action)
    monkeypatch.setattr(jobs, "_brick_approve", h.brick_approve)
    monkeypatch.setattr(jobs, "_brick_reject", h.brick_reject)
    monkeypatch.setattr(jobs, "requirement_met", h.requirement_met)
    monkeypatch.delenv("PODCLICK_AGENTS_DISABLED", raising=False)
    # Real runners from other lanes must not leak in: unknown ones are not_built.
    for spec in REGISTRY.values():
        monkeypatch.setitem(sys.modules, spec.runner, None)
    return h


def make_runner(monkeypatch, agent_id: str, commit: bool = False,
                gate: Optional[asyncio.Event] = None, fail_with: Optional[Exception] = None,
                commit_fail: Optional[Exception] = None) -> types.ModuleType:
    spec = get_agent(agent_id)
    mod = types.ModuleType(spec.runner)
    mod.calls = {"run": 0, "commit": 0, "step_two": 0, "edits": None}
    mod.ROUTES = ("/api/yt/content-calendar",)

    async def run(ctx, inp):
        mod.calls["run"] += 1
        async with ctx.step("one", "Step one"):
            pass
        if gate is not None:
            await gate.wait()
        if fail_with is not None:
            raise fail_with
        async with ctx.step("two", "Step two"):
            mod.calls["step_two"] += 1
        return AgentResult(
            outputs=[Output(kind="text", label="Post", value="draft text"),
                     Output(kind="json", label="Raw", value={"a": 1})],
            commit=CommitPlan("Put 1 post on the calendar") if commit else None,
        )

    async def do_commit(ctx, job, edits):
        mod.calls["commit"] += 1
        mod.calls["edits"] = dict(edits)
        await asyncio.sleep(0)  # yield, so a missing commit lock is observable
        if commit_fail is not None:
            raise commit_fail
        return {"post_ids": ["p-1"], "n": mod.calls["commit"]}

    mod.run = run
    if commit:
        mod.commit = do_commit
    monkeypatch.setitem(sys.modules, spec.runner, mod)
    return mod


async def settle(job_id: str) -> Dict[str, Any]:
    task = jobs.task_for(job_id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=5)
    return jobs.get_job(job_id)


# ── registry ─────────────────────────────────────────────────────────────────

ROSTER = {
    # id: (category, run, spend, commit, foundation)
    "market_scout":      ("research", "draftsman", "bricklayer", None, "optional"),
    "trend_radar":       ("research", "draftsman", None, None, "required"),
    "pillar_planner":    ("plan", "draftsman", None, None, "required"),
    "click_studio":      ("create", "bricklayer", "bricklayer", None, "inherited"),
    "draftsman":         ("create", "draftsman", None, "bricklayer", "required"),
    "thumbnail":         ("create", "draftsman", "bricklayer", None, "inherited"),
    "voiceover":         ("create", "draftsman", "bricklayer", None, "none"),
    "avatar_video":      ("create", "bricklayer", "bricklayer", None, "optional"),
    "home_tour":         ("create", "draftsman", "bricklayer", None, "required"),
    "market_reel":       ("create", "draftsman", "bricklayer", None, "required"),
    "content_scheduler": ("publish", "draftsman", None, "bricklayer", "none"),
    "repurpose":         ("publish", "draftsman", None, "bricklayer", "inherited"),
    "facebook_page":     ("publish", "draftsman", None, "bricklayer", "required"),
}


def test_registry_holds_exactly_the_twelve_roster_specs():
    assert list(REGISTRY) == list(ROSTER), "roster ids or display order drifted from §1.6"
    for aid, (cat, run, spend, commit, fnd) in ROSTER.items():
        s = REGISTRY[aid]
        assert (s.category, s.run_tier, s.spend_tier, s.commit_tier, s.foundation) == \
            (cat, run, spend, commit, fnd), aid


def test_category_counts_match_the_ui_headers():
    counts = {c: 0 for c in CATEGORY_IDS}
    for s in REGISTRY.values():
        counts[s.category] += 1
    assert counts == {"research": 2, "plan": 1, "create": 7, "publish": 3, "grow": 0}


def test_every_spec_is_internally_consistent():
    tiers = {"owner_builder", "draftsman", "bricklayer", "foreman", "gc"}
    for s in REGISTRY.values():
        assert s.runner == "services.agents.runners." + s.id
        assert s.run_tier in tiers and (s.spend_tier is None or s.spend_tier in tiers)
        assert s.commit_tier is None or s.commit_tier in tiers
        assert s.foundation in FOUNDATION_MODES
        assert set(s.requires) <= REQUIREMENTS, s.id
        if s.spend_tier:
            assert s.spends, "{} spends but never says what".format(s.id)
        names = [f.name for f in s.fields]
        assert len(names) == len(set(names)), s.id
        for f in s.fields:
            assert f.type in FIELD_TYPES, (s.id, f.name)
            for ref in f.job_ref_agents:
                assert ref in REGISTRY, (s.id, f.name, ref)
            if f.type == "select" and f.default is not None:
                assert f.default in f.options, (s.id, f.name)
        for group in s.require_one_of:
            assert all(n in names for n in group), s.id


def test_hard_requirements_match_the_spec():
    assert REGISTRY["market_scout"].requires == ("youtube_api",)
    assert REGISTRY["voiceover"].requires == ("elevenlabs",)
    assert set(REGISTRY["avatar_video"].requires) == {"elevenlabs", "higgsfield"}
    assert REGISTRY["home_tour"].requires == ()  # all soft — ffmpeg fallback


def test_agent_run_tier_is_max_of_run_and_spend():
    assert agent_run_tier(REGISTRY["market_scout"]) == "bricklayer"
    assert agent_run_tier(REGISTRY["trend_radar"]) == "draftsman"
    assert agent_run_tier(REGISTRY["voiceover"]) == "bricklayer"
    assert agent_run_tier(REGISTRY["content_scheduler"]) == "draftsman"


def test_brick_tier_map_gains_agent_commit_and_every_agent_run():
    from services.brick_agent import ACTION_TIER_MAP

    assert ACTION_TIER_MAP["agent_commit"] == "draftsman"
    for aid, spec in REGISTRY.items():
        assert ACTION_TIER_MAP["agent_run:" + aid] == agent_run_tier(spec), aid


# ── validation ───────────────────────────────────────────────────────────────

def test_validation_flags_missing_required_and_applies_defaults():
    clean, errors = validate_input(REGISTRY["market_scout"], {"junk": 1})
    assert errors == {"city": "Required."}
    clean, errors = validate_input(REGISTRY["market_scout"], {"city": " Springfield, MO "})
    assert errors == {}
    assert clean == {"city": "Springfield, MO", "audience": "Relocation Buyers", "remix_top": 0}
    assert "junk" not in clean


def test_validation_enforces_options_bounds_and_types():
    spec = REGISTRY["market_scout"]
    _, errors = validate_input(spec, {"city": "X", "audience": "Aliens", "remix_top": 4})
    assert set(errors) == {"audience", "remix_top"}
    _, errors = validate_input(spec, {"city": "X", "remix_top": "lots"})
    assert set(errors) == {"remix_top"}
    _, errors = validate_input(REGISTRY["repurpose"], {"project_id": "p", "platforms": ["myspace"]})
    assert set(errors) == {"platforms"}
    _, errors = validate_input(REGISTRY["content_scheduler"], {"topics": "a", "start_date": "2026-13-40"})
    assert set(errors) == {"start_date"}


def test_validation_requires_one_of_a_group():
    _, errors = validate_input(REGISTRY["content_scheduler"], {"cadence": "daily"})
    assert set(errors) == {"topics_job", "topics"}
    _, errors = validate_input(REGISTRY["content_scheduler"], {"topics": "one\ntwo"})
    assert errors == {}


def test_job_ref_must_be_a_finished_job_of_an_allowed_agent(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    jobs.AGENT_JOBS["done-tr"] = {"id": "done-tr", "agent_id": "trend_radar", "status": "done"}
    jobs.AGENT_JOBS["run-tr"] = {"id": "run-tr", "agent_id": "trend_radar", "status": "running"}
    jobs.AGENT_JOBS["done-ms"] = {"id": "done-ms", "agent_id": "market_scout", "status": "done"}
    spec = REGISTRY["content_scheduler"]
    for ref, ok in (("done-tr", True), ("run-tr", False), ("done-ms", False), ("nope", False)):
        _, errors = validate_input(spec, {"topics_job": ref}, job_ref_ok=jobs.job_ref_ok)
        assert ("topics_job" not in errors) is ok, ref


# ── start gates ──────────────────────────────────────────────────────────────

async def test_unknown_agent_is_404(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    with pytest.raises(jobs.UnknownAgent) as ei:
        await jobs.start("not_an_agent", {})
    assert ei.value.status_code == 404


async def test_missing_runner_is_not_built_423(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    with pytest.raises(jobs.NotBuilt) as ei:
        await jobs.start("trend_radar", {"city": "X"}, location_id=LOC)
    assert ei.value.status_code == 423
    assert ei.value.payload()["error"] == "not_built"
    assert jobs.agent_state(REGISTRY["trend_radar"]) == ("not_built", [])


async def test_committing_agent_without_commit_is_not_built(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "draftsman", commit=False)  # commit_tier set, no commit()
    assert jobs.load_runner(REGISTRY["draftsman"]) is None


async def test_kill_switch_makes_everything_not_built(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "trend_radar")
    monkeypatch.setenv("PODCLICK_AGENTS_DISABLED", "1")
    with pytest.raises(jobs.NotBuilt):
        await jobs.start("trend_radar", {"city": "X"}, location_id=LOC)


async def test_invalid_input_is_400_with_field_messages(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "trend_radar")
    with pytest.raises(jobs.InvalidInput) as ei:
        await jobs.start("trend_radar", {"audience": "Aliens"}, location_id=LOC)
    assert ei.value.status_code == 400
    assert set(ei.value.payload()["fields"]) == {"city", "audience"}
    assert jobs.AGENT_JOBS == {}, "a refused run must not create a work order"


async def test_missing_provider_is_needs_setup_409_and_never_runs(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    mod = make_runner(monkeypatch, "market_scout")
    h.unmet = {"youtube_api"}
    with pytest.raises(jobs.NeedsSetup) as ei:
        await jobs.start("market_scout", {"city": "X"}, location_id=LOC)
    assert ei.value.status_code == 409
    assert ei.value.payload()["needs_setup"] is True
    assert ei.value.payload()["missing"] == ["youtube_api"]
    assert mod.calls["run"] == 0
    assert jobs.agent_state(REGISTRY["market_scout"]) == ("needs_setup", ["youtube_api"])


def test_requirement_check_reads_settings_before_env(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "pexels_api_key", "", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert jobs.requirement_met("pexels") is False
    monkeypatch.setattr(settings, "pexels_api_key", "k", raising=False)
    assert jobs.requirement_met("pexels") is True
    monkeypatch.setattr(settings, "pexels_api_key", "", raising=False)
    monkeypatch.setenv("PEXELS_API_KEY", "envk")
    assert jobs.requirement_met("pexels") is True
    assert jobs.requirement_met("not_a_requirement") is False


async def test_foundation_not_ready_blocks_required_agents_422(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "trend_radar")
    h.foundation = {"tier": "not_ready", "sample_count": 2}
    with pytest.raises(jobs.FoundationNotReady) as ei:
        await jobs.start("trend_radar", {"city": "X"}, location_id=LOC)
    assert ei.value.status_code == 422
    assert ei.value.payload()["foundation_not_ready"] is True


async def test_foundation_not_ready_does_not_block_optional_agents(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "market_scout")
    h.foundation = {"tier": "not_ready", "sample_count": 2}
    job = await jobs.start("market_scout", {"city": "X"}, location_id=LOC)
    assert (await settle(job["id"]))["status"] == "done"


async def test_thin_foundation_warns_but_runs(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "trend_radar")
    h.foundation = {"tier": "thin", "sample_count": 9}
    job = await jobs.start("trend_radar", {"city": "X"}, location_id=LOC)
    job = await settle(job["id"])
    assert job["status"] == "done"
    assert any("Thin Foundation (9 samples)" in w for w in job["warnings"])


# ── lifecycle ────────────────────────────────────────────────────────────────

async def test_run_without_commit_finishes_done_and_persists(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "trend_radar")
    job = await jobs.start("trend_radar", {"city": "Springfield, MO"}, location_id=LOC)
    assert job["status"] == "queued"
    job = await settle(job["id"])
    assert job["status"] == "done"
    assert [s["status"] for s in job["steps"]] == ["completed", "completed"]
    assert [o["id"] for o in job["outputs"]] == ["o1", "o2"]
    on_disk = json.loads((tmp_path / "agent_jobs" / (job["id"] + ".json")).read_text())
    assert on_disk["status"] == "done" and on_disk["outputs"] == job["outputs"]
    assert not list((tmp_path / "agent_jobs").glob(".*.tmp")), "atomic write left a tmp file"


async def test_step_error_fails_the_job_with_the_user_message(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "trend_radar",
                fail_with=StepError("YouTube quota's spent for today — resets midnight Pacific."))
    job = await settle((await jobs.start("trend_radar", {"city": "X"}, location_id=LOC))["id"])
    assert job["status"] == "failed"
    assert job["error"].startswith("YouTube quota")


async def test_unexpected_exception_fails_without_leaking_its_message(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "trend_radar", fail_with=RuntimeError("sk-secret-key-123"))
    job = await settle((await jobs.start("trend_radar", {"city": "X"}, location_id=LOC))["id"])
    assert job["status"] == "failed"
    assert "sk-secret" not in json.dumps(job)


async def test_user_run_with_commit_lands_on_the_punch_list(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    h.tier = "gc"  # even at GC, a USER-initiated run always stops for approval
    mod = make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "rates"}, location_id=LOC))["id"])
    assert job["status"] == "needs_approval" and job["needs_approval"] is True
    assert job["approval"]["summary"] == "Put 1 post on the calendar"
    assert job["approval"]["action_id"] in h.actions
    assert mod.calls["commit"] == 0


async def test_approve_twice_commits_exactly_once(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    mod = make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "rates"}, location_id=LOC))["id"])
    first = await jobs.approve(job["id"])
    second = await jobs.approve(job["id"])
    assert mod.calls["commit"] == 1
    assert first["status"] == second["status"] == "done"
    assert second["commit_result"] == {"post_ids": ["p-1"], "n": 1}
    assert h.actions[job["approval"]["action_id"]]["status"] == "executed"


async def test_commit_job_called_again_returns_the_stored_result(monkeypatch, tmp_path):
    """
    The dispatch can reach commit_job after it already ran (a Brick-immediate
    commit, a retried punch-list call). The second call must hand back the stored
    result — not raise, not commit again.
    """
    install(monkeypatch, tmp_path)
    mod = make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "rates"}, location_id=LOC))["id"])
    first = await jobs.commit_job(job["id"])
    second = await jobs.commit_job(job["id"])
    assert first == second == {"post_ids": ["p-1"], "n": 1}
    assert mod.calls["commit"] == 1


async def test_concurrent_approvals_commit_once(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    mod = make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "rates"}, location_id=LOC))["id"])
    results = await asyncio.gather(jobs.commit_job(job["id"]), jobs.commit_job(job["id"]))
    assert mod.calls["commit"] == 1
    assert results[0] == results[1]


async def test_walkthrough_approval_and_crew_approval_share_one_commit(monkeypatch, tmp_path):
    """Approve on the punch list first; the /agents approve then returns the stored result."""
    h = install(monkeypatch, tmp_path)
    mod = make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "rates"}, location_id=LOC))["id"])
    walkthrough = await h.brick_approve(job["approval"]["action_id"])
    assert walkthrough["status"] == "committed"
    assert walkthrough["commit_result"] == {"post_ids": ["p-1"], "n": 1}
    again = await jobs.approve(job["id"])
    assert mod.calls["commit"] == 1
    assert again["commit_result"] == walkthrough["commit_result"]


async def test_dispatch_branch_routes_agent_commit_to_commit_job(monkeypatch, tmp_path):
    from services.brick_agent import BrickAgent

    install(monkeypatch, tmp_path)
    seen = []

    async def fake_commit(job_id):
        seen.append(job_id)
        return {"ok": 1}

    monkeypatch.setattr(jobs, "commit_job", fake_commit)
    action = types.SimpleNamespace(id=uuid.uuid4(), location_id=uuid.UUID(LOC),
                                   action_type="agent_commit",
                                   payload={"job_id": "j-9", "agent_id": "draftsman"})
    result = await BrickAgent()._dispatch_action(action, None)
    assert seen == ["j-9"]
    assert result["status"] == "committed" and result["commit_result"] == {"ok": 1}


async def test_approval_at_owner_builder_is_refused(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    mod = make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "rates"}, location_id=LOC))["id"])
    h.tier = "owner_builder"
    with pytest.raises(PermissionError):
        await jobs.approve(job["id"])
    assert mod.calls["commit"] == 0


async def test_edits_reach_the_commit_and_only_text_or_cards_are_editable(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    mod = make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "rates"}, location_id=LOC))["id"])
    with pytest.raises(jobs.InvalidInput):
        await jobs.approve(job["id"], {"o2": {"b": 2}})  # o2 is json — not editable
    assert mod.calls["commit"] == 0
    done = await jobs.approve(job["id"], {"o1": "edited text"})
    assert mod.calls["edits"] == {"o1": "edited text"}
    assert done["outputs"][0]["value"] == "edited text"


async def test_commit_failure_marks_job_failed_and_raises(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "draftsman", commit=True, commit_fail=RuntimeError("db gone"))
    job = await settle((await jobs.start("draftsman", {"topic": "rates"}, location_id=LOC))["id"])
    with pytest.raises(RuntimeError):
        await jobs.approve(job["id"])
    assert jobs.get_job(job["id"])["status"] == "failed"
    assert jobs.get_job(job["id"])["committed_at"] is None


async def test_reject_sends_back_and_never_commits(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    mod = make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "rates"}, location_id=LOC))["id"])
    out = await jobs.reject(job["id"], "not this week")
    assert out["status"] == "rejected"
    assert h.brick_rejects == [job["approval"]["action_id"]]
    with pytest.raises(jobs.JobConflict):
        await jobs.approve(job["id"])
    assert mod.calls["commit"] == 0


async def test_brick_initiated_commit_skips_the_punch_list_when_tier_allows(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    h.tier = "bricklayer"
    mod = make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "r"}, initiator="brick",
                                         location_id=LOC))["id"])
    assert job["status"] == "done" and mod.calls["commit"] == 1
    assert h.actions == {}


async def test_brick_initiated_commit_below_tier_waits_for_a_human(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    h.tier = "draftsman"
    mod = make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "r"}, initiator="brick",
                                         location_id=LOC))["id"])
    assert job["status"] == "needs_approval" and mod.calls["commit"] == 0


async def test_cancel_between_steps_stops_the_runner(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    gate = asyncio.Event()
    mod = make_runner(monkeypatch, "trend_radar", gate=gate)
    job = await jobs.start("trend_radar", {"city": "X"}, location_id=LOC)
    for _ in range(50):
        await asyncio.sleep(0)
        if mod.calls["run"]:
            break
    await jobs.cancel(job["id"])
    gate.set()
    job = await settle(job["id"])
    assert job["status"] == "cancelled"
    assert mod.calls["step_two"] == 0, "the step after Stop still ran"
    assert job["outputs"] == []
    with pytest.raises(jobs.JobConflict):
        await jobs.cancel(job["id"])


async def test_result_finishing_after_stop_is_discarded_not_committed(monkeypatch, tmp_path):
    """
    A runner mid-provider-call never reaches another checkpoint. When it returns
    after Stop, its outputs and commit plan must be thrown away — the job stays
    cancelled and nothing lands on the punch list.
    """
    h = install(monkeypatch, tmp_path)
    gate = asyncio.Event()
    spec = get_agent("draftsman")
    mod = types.ModuleType(spec.runner)
    mod.started = False

    async def run(ctx, inp):
        mod.started = True
        await gate.wait()  # a submitted provider job: no checkpoint until it returns
        return AgentResult(outputs=[Output(kind="text", label="Post", value="late")],
                           commit=CommitPlan("Put 1 post on the calendar"))

    async def commit(ctx, job, edits):
        raise AssertionError("must never commit a stopped job")

    mod.run, mod.commit = run, commit
    monkeypatch.setitem(sys.modules, spec.runner, mod)
    job = await jobs.start("draftsman", {"topic": "r"}, location_id=LOC)
    for _ in range(50):
        await asyncio.sleep(0)
        if mod.started:
            break
    await jobs.cancel(job["id"])
    gate.set()
    job = await settle(job["id"])
    assert job["status"] == "cancelled"
    assert job["outputs"] == [] and job["approval"] is None
    assert h.actions == {}


async def test_cancel_of_a_queued_job_never_runs_it(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    mod = make_runner(monkeypatch, "trend_radar")
    job = await jobs.start("trend_radar", {"city": "X"}, location_id=LOC)
    await jobs.cancel(job["id"])  # before the spawned task gets the loop
    job = await settle(job["id"])
    assert job["status"] == "cancelled" and mod.calls["run"] == 0


async def test_cancel_while_awaiting_approval_withdraws_the_punch_item(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "draftsman", commit=True)
    job = await settle((await jobs.start("draftsman", {"topic": "r"}, location_id=LOC))["id"])
    await jobs.cancel(job["id"])
    assert h.actions[job["approval"]["action_id"]]["status"] == "rejected"


# ── restart recovery ─────────────────────────────────────────────────────────

def _write_job(tmp_path, status, **extra):
    d = tmp_path / "agent_jobs"
    d.mkdir(parents=True, exist_ok=True)
    jid = str(uuid.uuid4())
    job = {"id": jid, "agent_id": "trend_radar", "status": status, "steps": [
        {"key": "one", "label": "One", "status": "running", "error": None}],
        "outputs": [], "warnings": [], "updated_at": "2026-10-01T00:00:00+00:00"}
    job.update(extra)
    (d / (jid + ".json")).write_text(json.dumps(job))
    return jid


def test_restart_marks_in_flight_jobs_failed_and_keeps_finished_ones(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    running = _write_job(tmp_path, "running")
    committing = _write_job(tmp_path, "committing")
    done = _write_job(tmp_path, "done")
    jobs._reset_for_tests()
    assert jobs.recover_jobs() == 2
    for jid in (running, committing):
        j = jobs.get_job(jid)
        assert j["status"] == "failed" and j["error"] == jobs.INTERRUPTED_MSG
        assert j["steps"][0]["status"] == "failed"
        on_disk = json.loads((tmp_path / "agent_jobs" / (jid + ".json")).read_text())
        assert on_disk["status"] == "failed", "recovery must be durable, not memory-only"
    assert jobs.get_job(done)["status"] == "done"


def test_the_store_recovers_lazily_on_first_read(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    jid = _write_job(tmp_path, "queued")
    jobs._reset_for_tests()
    assert jobs.get_job(jid)["status"] == "failed"


# ── list / summaries / files ─────────────────────────────────────────────────

async def test_list_filters_and_strips_heavy_fields(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "trend_radar")
    job = await settle((await jobs.start("trend_radar", {"city": "X"}, location_id=LOC))["id"])
    rows = jobs.list_jobs(agent_id=["trend_radar"], statuses=["done"])
    assert [r["id"] for r in rows] == [job["id"]]
    assert "input" not in rows[0] and all("value" not in o for o in rows[0]["outputs"])
    assert rows[0]["output_count"] == 2
    assert jobs.list_jobs(statuses=["failed"]) == []
    assert jobs.list_jobs(agent_id=["market_scout"]) == []


async def test_output_files_cannot_escape_the_job_dir(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    make_runner(monkeypatch, "trend_radar")
    job = await settle((await jobs.start("trend_radar", {"city": "X"}, location_id=LOC))["id"])
    (tmp_path / "agent_outputs" / job["id"] / "ok.txt").write_text("fine")
    # A sibling of the job dir, so "../secret.txt" names a file that really exists.
    (tmp_path / "agent_outputs" / "secret.txt").write_text("nope")
    assert jobs.resolve_output_file(job["id"], "ok.txt").read_text() == "fine"
    for bad in ("..", "../secret.txt", "..%2Fsecret.txt", "a/b", ".", "", "missing.txt"):
        with pytest.raises(jobs.JobNotFound):
            jobs.resolve_output_file(job["id"], bad)


# ── internal route allowlist ─────────────────────────────────────────────────

def test_route_allowlist_is_exact_never_prefix_or_wildcard():
    allowed = ("/api/yt/content-calendar", "/api/projects/{id}/ship-it", "/api/*")
    assert internal.route_allowed("/api/yt/content-calendar", allowed)
    assert internal.route_allowed("/api/projects/abc-123/ship-it", allowed)
    for path in ("/api/yt/content-calendar/extra", "/api/yt/content", "/api/social/forge",
                 "/api/projects/a/b/ship-it", "/api/projects/../x/ship-it",
                 "/api/yt/content-calendar?x=1", "api/yt/content-calendar"):
        assert not internal.route_allowed(path, allowed), path


async def test_call_route_hits_the_app_in_process_and_refuses_undeclared(monkeypatch):
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/api/yt/content-calendar")
    async def cc(body: dict):
        return {"echo": body}

    @app.post("/api/danger")
    async def danger():
        return {"boom": True}

    monkeypatch.setattr(internal, "APP", app)
    status, data = await internal.call_route("/api/yt/content-calendar", {"city": "X"},
                                             allowed=("/api/yt/content-calendar",))
    assert status == 200 and data == {"echo": {"city": "X"}}
    with pytest.raises(RouteNotAllowed):
        await internal.call_route("/api/danger", {}, allowed=("/api/yt/content-calendar",))


async def test_ctx_call_route_is_limited_to_the_runner_routes(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    captured = {}
    spec = get_agent("trend_radar")
    mod = types.ModuleType(spec.runner)
    mod.ROUTES = ("/api/yt/content-calendar",)

    async def run(ctx, inp):
        captured["allowed"] = ctx.allowed_routes
        with pytest.raises(RouteNotAllowed):
            await ctx.call_route("/api/social/forge", {})
        return AgentResult()

    mod.run = run
    monkeypatch.setitem(sys.modules, spec.runner, mod)
    job = await settle((await jobs.start("trend_radar", {"city": "X"}, location_id=LOC))["id"])
    assert job["status"] == "done"
    assert captured["allowed"] == ("/api/yt/content-calendar",)
