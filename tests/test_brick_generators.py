"""
Wave 4 — the ~20 generators Brick could not reach.

PodClick ships about twenty content generators behind /api/yt/*, /api/brand/* and
/api/social/*. None appeared in ACTION_TIER_MAP, so promoting Brick to GC gave him
authority over twelve action types while the app's best capabilities stayed out of
reach entirely.

The registry is also a security boundary: the dispatch calls studio_app directly,
bypassing DeploymentBoundary, because it is the owner's own agent acting
internally. That makes the allowlist load-bearing, and most of these tests exist to
keep it honest.
"""

import inspect

import pytest

from services.brick_agent import (
    ACTION_TIER_MAP,
    GENERATOR_ACTIONS,
    TIER_ORDER,
    BrickAgent,
)


# ── the registry is an allowlist, not a wildcard ──────────────────────────────

def test_every_generator_declares_a_path_and_tier():
    for action, spec in GENERATOR_ACTIONS.items():
        assert spec.get("path", "").startswith("/api/"), f"{action} has no /api path"
        assert spec.get("tier") in TIER_ORDER, f"{action} tier {spec.get('tier')!r} not on the ladder"
        assert spec.get("label"), f"{action} has no human label"


def test_every_generator_path_is_a_real_post_route():
    """A registry entry pointing at a route that does not exist is a silent no-op."""
    from pathlib import Path

    main_src = (Path(__file__).resolve().parent.parent / "main.py").read_text()
    for action, spec in GENERATOR_ACTIONS.items():
        needle = '@app.post("' + spec["path"] + '")'
        assert needle in main_src, f"{action} -> {spec['path']} is not a POST route"


def test_no_generator_publishes_disconnects_or_uploads():
    """
    The dispatch bypasses DeploymentBoundary, so the allowlist is the only control.
    Publishing belongs to publish_post and SocialService (contract #5); disconnects
    are destructive; uploads are not generation.
    """
    forbidden = ("publish", "disconnect", "auth", "callback", "upload", "select-page", "photos")
    offenders = [
        (a, s["path"]) for a, s in GENERATOR_ACTIONS.items()
        if any(bad in s["path"] for bad in forbidden)
    ]
    assert not offenders, f"these must not be reachable as generators: {offenders}"


def test_no_generator_path_is_a_prefix_or_wildcard():
    for action, spec in GENERATOR_ACTIONS.items():
        path = spec["path"]
        assert "*" not in path and "{" not in path, f"{action} path is not a literal: {path}"


def test_generators_are_merged_into_the_single_tier_gate():
    """One gate for everything — a tier change must not need a second check."""
    for action, spec in GENERATOR_ACTIONS.items():
        assert ACTION_TIER_MAP.get(action) == spec["tier"], f"{action} not gated"


def test_generation_sits_at_draftsman_unless_it_spends_something():
    """
    A generator produces text for review and reaches no audience, so draftsman is
    the honest floor. The exceptions must declare WHY they cost more.
    """
    for action, spec in GENERATOR_ACTIONS.items():
        if spec["tier"] == "draftsman":
            continue
        assert spec.get("cost"), (
            f"{action} is gated above draftsman with no stated cost — "
            "either justify it or lower the tier"
        )


def test_no_generator_outranks_the_actions_that_reach_an_audience():
    """Generating must never cost more than publishing; that would be backwards."""
    from services.brick_agent import _tier_rank

    publish_rank = _tier_rank(ACTION_TIER_MAP["publish_post"])
    for action, spec in GENERATOR_ACTIONS.items():
        assert _tier_rank(spec["tier"]) < publish_rank, f"{action} costs more than publishing"


def test_the_quota_spender_is_flagged():
    """competitor_spy burns YouTube Data API quota — 100 units per search.list."""
    spy = GENERATOR_ACTIONS["yt_competitor_spy"]
    assert spy["tier"] != "draftsman"
    assert "quota" in spy["cost"].lower()


# ── the dispatch ──────────────────────────────────────────────────────────────

class FakeAction:
    def __init__(self, action_type, payload=None):
        import uuid
        self.id = uuid.uuid4()
        self.location_id = uuid.uuid4()
        self.action_type = action_type
        self.payload = payload or {}


def test_dispatch_action_routes_every_registered_generator():
    src = inspect.getsource(BrickAgent._dispatch_action)
    assert "GENERATOR_ACTIONS" in src, "generators are not routed from _dispatch_action"
    assert "_dispatch_generator" in src


async def test_unregistered_action_is_a_no_op(monkeypatch):
    agent = BrickAgent()
    out = await agent._dispatch_generator(FakeAction("not_a_generator"), {}, None)
    assert out["status"] == "no_op"


def test_generator_reuses_the_route_rather_than_reimplementing_it():
    """
    Twenty copied prompt bodies would guarantee drift. The branch must call the
    app in-process, not hold its own generation logic.
    """
    src = inspect.getsource(BrickAgent._dispatch_generator)
    assert "ASGITransport" in src, "the branch no longer calls the real route"
    assert "studio_app" in src
    # and it must not be building its own model calls
    assert "messages.create" not in src
    assert "anthropic" not in src.lower().replace("# ", "")


def test_server_errors_raise_and_client_errors_skip():
    """
    Wave 1 made success_rate the promotion gate, so this split is load-bearing: a
    4xx precondition must not penalise Brick, and a 5xx generator failure must not
    vanish as a free skip.
    """
    src = inspect.getsource(BrickAgent._dispatch_generator)
    assert "status_code >= 500" in src, "5xx is not separated from 4xx"
    assert "raise RuntimeError" in src
    assert '"status": "skipped"' in src


def test_generated_results_are_not_persisted():
    """
    A generator produces text for review; writing it somewhere is a separate,
    higher-tier action. The branch must say so rather than leaving it ambiguous.
    """
    src = inspect.getsource(BrickAgent._dispatch_generator)
    assert '"persisted": False' in src
    assert "session.add" not in src
    assert "session.commit" not in src


def test_foundation_not_ready_is_surfaced_distinctly():
    """It is the common 422 and it is a precondition, not Brick's mistake."""
    src = inspect.getsource(BrickAgent._dispatch_generator)
    assert "foundation_not_ready" in src


def test_main_is_imported_lazily():
    """main imports this module at startup; a top-level import would be circular."""
    src = inspect.getsource(BrickAgent._dispatch_generator)
    assert "import main as _main" in src
    import services.brick_agent as mod
    assert "import main" not in inspect.getsource(mod).split("class BrickAgent")[0]


def test_the_registry_grew_brick_reach_substantially():
    """The point of the wave: twelve action types became thirty-two."""
    assert len(GENERATOR_ACTIONS) >= 20
    assert len(ACTION_TIER_MAP) >= 32


# ── the registry must match the live app, not just main.py's source ───────────

def test_every_registered_path_resolves_as_a_post_route_in_the_live_app():
    """
    Source-grepping for '@app.post("...")' proves the decorator exists; this proves
    the route is actually mounted on studio_app — the app the dispatch calls. A
    typo would 404 at dispatch time and read as a generator failure rather than a
    registry bug, and checking it costs nothing next to 20 LLM calls.
    """
    import main

    routes = {}
    for r in main.studio_app.routes:
        path = getattr(r, "path", None)
        if path:
            routes.setdefault(path, set()).update(getattr(r, "methods", set()) or set())

    problems = []
    for action, spec in sorted(GENERATOR_ACTIONS.items()):
        methods = routes.get(spec["path"])
        if methods is None:
            problems.append(f"{action} -> {spec['path']} is not mounted")
        elif "POST" not in methods:
            problems.append(f"{action} -> {spec['path']} has no POST ({sorted(methods)})")
    assert not problems, "registry does not match the live app:\n  " + "\n  ".join(problems)


def test_no_publish_or_disconnect_route_is_allowlisted_in_the_live_app():
    """
    The dispatch bypasses DeploymentBoundary, so the allowlist is the only control.
    Checked against the real route table rather than a string match on paths, so a
    route renamed into a publishing path would still be caught.
    """
    import main

    dangerous = {
        getattr(r, "path", "") for r in main.studio_app.routes
        if any(bad in (getattr(r, "path", "") or "")
               for bad in ("publish", "disconnect", "/auth", "/callback"))
    }
    listed = {spec["path"] for spec in GENERATOR_ACTIONS.values()}
    overlap = sorted(dangerous & listed)
    assert not overlap, f"allowlist contains dangerous routes: {overlap}"
