"""
Wave 3 — the eight action types that used to no-op.

ACTION_TIER_MAP had 12 entries; `_dispatch_action` implemented 4. Promoting Brick
to Foreman or GC changed a label in `brick_permits` and a congratulations modal,
and nothing else: `publish_post`, `cut_clip`, `write_show_notes`,
`adjust_calendar`, `queue_draft`, `send_guest_email`, `pitch_sponsor`,
`adjust_vyral_mix` and `replan_calendar` all fell through to `no_op`.

No database and no network — the session and every external call are injected.
"""

import inspect
import re
import uuid
from datetime import datetime, timezone

import pytest

from db.models import Blueprint, Post, Project
from services.brick_agent import ACTION_TIER_MAP, NON_WORK_STATUSES, BrickAgent

LOC = uuid.uuid4()
OTHER_LOC = uuid.uuid4()


class FakeAction:
    def __init__(self, action_type, location_id=LOC):
        self.id = uuid.uuid4()
        self.location_id = location_id
        self.action_type = action_type
        self.payload = {}


class FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    def __init__(self, by_id=None, query_rows=None, by_entity=None):
        self._by_id = by_id or {}
        self._query_rows = query_rows or []
        # {"blueprints": [...], "posts": [...]} for branches that query several
        self._by_entity = by_entity or {}
        self.flushed = 0
        self.deleted = []
        self.added = []
        self.committed = 0

    async def get(self, _model, pk):
        return self._by_id.get(str(pk))

    async def execute(self, stmt):
        # Answer per-entity so a branch that queries a Blueprint and then Posts
        # does not get Posts both times (which is how the first version of this
        # harness produced a bogus AttributeError).
        text = str(stmt)
        if self._by_entity:
            for entity, rows in self._by_entity.items():
                if f"FROM {entity}" in text or f"{entity}." in text:
                    return FakeResult(rows)
            return FakeResult([])
        return FakeResult(self._query_rows)

    async def flush(self):
        self.flushed += 1

    async def delete(self, obj):
        self.deleted.append(obj)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):  # pragma: no cover — branches must never commit
        self.committed += 1


@pytest.fixture
def agent():
    return BrickAgent()


def make_post(status="draft", location_id=LOC, when=None, caption="hello", source="auto_plan"):
    return Post(
        id=uuid.uuid4(), location_id=location_id, bucket="brand",
        base_caption=caption, scheduled_at=when, status=status, source=source,
    )


LATER = datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)


# ── every action type dispatches ──────────────────────────────────────────────

def test_no_action_type_falls_through_to_no_op():
    """
    The headline fix. Every entry in ACTION_TIER_MAP must reach a branch, or
    promoting Brick to that tier still changes nothing.

    Three routing mechanisms count, and all three are proven rather than assumed:
    the explicit dispatch table, the remaining inline comparisons, and the Wave 4
    GENERATOR_ACTIONS registry — which is only accepted as coverage if
    _dispatch_action actually consults it.
    """
    from services.brick_agent import GENERATOR_ACTIONS

    src = inspect.getsource(BrickAgent._dispatch_action)
    routed = set(re.findall(r'"([a-z_]+)": self\._dispatch_', src))
    inline = set(re.findall(r'"([a-z_]+)"', " ".join(re.findall(r"action_type in \(([^)]*)\)", src))))
    inline |= set(re.findall(r'action_type == "([a-z_]+)"', src))

    # Registry-routed actions count only if _dispatch_action really routes to
    # _dispatch_generator. Checking for the substring is not enough: wrapping the
    # branch in `if False and ...` leaves the text intact and the routing dead —
    # which is exactly what happened when this guard was first written. The proof
    # below is behavioural, asserted in its own test.
    registry_routed = set(GENERATOR_ACTIONS) if _registry_routing_is_live() else set()

    covered = routed | inline | registry_routed
    missing = sorted(a for a in ACTION_TIER_MAP if a not in covered)
    assert not missing, f"these still no-op: {missing}"


def test_every_routed_branch_exists_as_a_method():
    src = inspect.getsource(BrickAgent._dispatch_action)
    for action in re.findall(r'"([a-z_]+)": self\._dispatch_', src):
        assert hasattr(BrickAgent, f"_dispatch_{action}"), f"_dispatch_{action} missing"


def test_no_branch_commits_its_own_session():
    """
    execute_action owns the commit. A branch committing would leave two
    connections writing rows the outer session may hold — the reason
    approve_action deliberately closes its session before dispatching.
    """
    offenders = []
    for name, fn in inspect.getmembers(BrickAgent, predicate=inspect.isfunction):
        if not name.startswith("_dispatch_"):
            continue
        body = inspect.getsource(fn)
        if re.search(r"\bsession\.commit\(\)", body):
            offenders.append(name)
    assert not offenders, f"branches commit their own session: {offenders}"


# ── queue_draft ───────────────────────────────────────────────────────────────

async def test_queue_draft_schedules_a_draft(agent):
    post = make_post(status="draft")
    session = FakeSession({str(post.id): post})
    action = FakeAction("queue_draft")

    out = await agent._dispatch_queue_draft(
        action, {"post_id": str(post.id), "scheduled_at": LATER.isoformat()}, session
    )

    assert out["status"] == "scheduled"
    assert post.status == "scheduled"
    assert post.scheduled_at == LATER
    assert session.flushed == 1


async def test_queue_draft_refuses_a_published_post(agent):
    post = make_post(status="published")
    session = FakeSession({str(post.id): post})
    out = await agent._dispatch_queue_draft(
        FakeAction("queue_draft"), {"post_id": str(post.id), "scheduled_at": LATER.isoformat()}, session
    )
    assert out["status"] == "skipped"
    assert post.status == "published"


async def test_queue_draft_refuses_another_locations_post(agent):
    post = make_post(location_id=OTHER_LOC)
    session = FakeSession({str(post.id): post})
    with pytest.raises(PermissionError, match="another location"):
        await agent._dispatch_queue_draft(
            FakeAction("queue_draft"),
            {"post_id": str(post.id), "scheduled_at": LATER.isoformat()},
            session,
        )


async def test_queue_draft_needs_a_time(agent):
    post = make_post()
    session = FakeSession({str(post.id): post})
    with pytest.raises(ValueError, match="scheduled_at"):
        await agent._dispatch_queue_draft(
            FakeAction("queue_draft"), {"post_id": str(post.id)}, session
        )


async def test_trailing_z_timestamps_parse(agent):
    post = make_post()
    session = FakeSession({str(post.id): post})
    out = await agent._dispatch_queue_draft(
        FakeAction("queue_draft"),
        {"post_id": str(post.id), "scheduled_at": "2026-11-01T15:00:00Z"},
        session,
    )
    assert out["status"] == "scheduled"


# ── adjust_calendar ───────────────────────────────────────────────────────────

async def test_adjust_calendar_moves_a_scheduled_post(agent):
    was = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    post = make_post(status="scheduled", when=was)
    session = FakeSession({str(post.id): post})

    out = await agent._dispatch_adjust_calendar(
        FakeAction("adjust_calendar"),
        {"post_id": str(post.id), "scheduled_at": LATER.isoformat()},
        session,
    )

    assert out["status"] == "updated"
    assert out["was"] == was.isoformat()
    assert post.scheduled_at == LATER
    # moving a post must not change its status
    assert post.status == "scheduled"


async def test_adjust_calendar_refuses_a_published_post(agent):
    post = make_post(status="published", when=LATER)
    session = FakeSession({str(post.id): post})
    out = await agent._dispatch_adjust_calendar(
        FakeAction("adjust_calendar"),
        {"post_id": str(post.id), "scheduled_at": LATER.isoformat()},
        session,
    )
    assert out["status"] == "skipped"


async def test_adjust_calendar_checks_location_even_though_the_route_does_not(agent):
    post = make_post(location_id=OTHER_LOC, status="scheduled")
    session = FakeSession({str(post.id): post})
    with pytest.raises(PermissionError):
        await agent._dispatch_adjust_calendar(
            FakeAction("adjust_calendar"),
            {"post_id": str(post.id), "scheduled_at": LATER.isoformat()},
            session,
        )


# ── send_guest_email — the one that must NOT send ─────────────────────────────

def test_send_guest_email_never_calls_the_mailer():
    """
    approve-send is documented as the only path that emails a guest, and that is
    structural: the send sits in the route AHEAD of approve_action so the human
    review gate cannot be bypassed, and it is the only place that turns an expired
    token into a 409 the reconnect modal can act on. A branch calling
    gmail_send.send_message would be a second path with neither property.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(BrickAgent._dispatch_send_guest_email)))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
    assert "send_message" not in called, "the branch calls the mailer"
    # and it must not import the mailer either
    imported = {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    } | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not any("gmail" in (m or "") for m in imported), "the branch imports the mailer"


async def test_send_guest_email_points_at_the_review_route(agent):
    package = FakeAction("guest_asset_package")
    package.payload = {"recipient": "guest@example.com", "email": "Subject: hi\n\nbody"}
    session = FakeSession(query_rows=[package])

    out = await agent._dispatch_send_guest_email(FakeAction("send_guest_email"), {}, session)

    assert out["status"] == "needs_approve_send"
    assert out["status"] in NON_WORK_STATUSES or out["status"] == "needs_approve_send"
    assert out["recipient"] == "guest@example.com"
    assert f"/api/brick/actions/{package.id}/approve-send" == out["review_at"]


async def test_send_guest_email_skips_with_no_package(agent):
    out = await agent._dispatch_send_guest_email(
        FakeAction("send_guest_email"), {}, FakeSession(query_rows=[])
    )
    assert out["status"] == "skipped"
    assert "Build the package" in out["reason"]


# ── pitch_sponsor — generates, never sends ───────────────────────────────────

def test_pitch_sponsor_has_no_send_path():
    body = inspect.getsource(BrickAgent._dispatch_pitch_sponsor)
    for forbidden in ("send_message", "gmail_send", "publish("):
        assert forbidden not in body, f"pitch_sponsor references {forbidden}"


async def test_pitch_sponsor_needs_a_company(agent):
    out = await agent._dispatch_pitch_sponsor(
        FakeAction("pitch_sponsor"), {}, FakeSession()
    )
    assert out["status"] == "skipped"
    assert "company" in out["reason"]


async def test_pitch_sponsor_returns_a_draft_marked_unsent(agent, monkeypatch):
    async def fake_gen(self, location_id, company, angle):
        return f"Pitch for {company}"

    monkeypatch.setattr(BrickAgent, "_generate_sponsor_pitch", fake_gen)
    out = await agent._dispatch_pitch_sponsor(
        FakeAction("pitch_sponsor"), {"company": "DealCheck"}, FakeSession()
    )
    assert out["status"] == "drafted"
    assert out["sent"] is False
    assert "DealCheck" in out["pitch"]


async def test_pitch_sponsor_skips_when_generation_is_empty(agent, monkeypatch):
    async def empty(self, *a, **k):
        return ""

    monkeypatch.setattr(BrickAgent, "_generate_sponsor_pitch", empty)
    out = await agent._dispatch_pitch_sponsor(
        FakeAction("pitch_sponsor"), {"company": "X"}, FakeSession()
    )
    assert out["status"] == "skipped"


# ── write_show_notes ──────────────────────────────────────────────────────────

def make_project(transcript="a real transcript", notes=None, location_id=LOC):
    return Project(
        id=uuid.uuid4(), location_id=location_id, type="episode", title="EP 1",
        transcript=transcript, show_notes=notes, status="review",
    )


async def test_write_show_notes_persists(agent, monkeypatch):
    async def fake_gen(self, location_id, title, transcript):
        return "## Episode Summary\nreal notes"

    monkeypatch.setattr(BrickAgent, "_generate_show_notes", fake_gen)
    project = make_project()
    session = FakeSession({str(project.id): project})

    out = await agent._dispatch_write_show_notes(
        FakeAction("write_show_notes"), {"project_id": str(project.id)}, session
    )

    assert out["status"] == "written"
    assert project.show_notes.startswith("## Episode Summary")
    assert session.flushed == 1


async def test_write_show_notes_skips_without_a_transcript(agent):
    project = make_project(transcript="")
    session = FakeSession({str(project.id): project})
    out = await agent._dispatch_write_show_notes(
        FakeAction("write_show_notes"), {"project_id": str(project.id)}, session
    )
    assert out["status"] == "skipped"
    assert "transcript" in out["reason"]


async def test_write_show_notes_will_not_clobber_existing_notes(agent):
    project = make_project(notes="notes a human edited")
    session = FakeSession({str(project.id): project})
    out = await agent._dispatch_write_show_notes(
        FakeAction("write_show_notes"), {"project_id": str(project.id)}, session
    )
    assert out["status"] == "skipped"
    assert project.show_notes == "notes a human edited"


async def test_write_show_notes_overwrites_when_asked(agent, monkeypatch):
    async def fake_gen(self, *a, **k):
        return "fresh notes"

    monkeypatch.setattr(BrickAgent, "_generate_show_notes", fake_gen)
    project = make_project(notes="old")
    session = FakeSession({str(project.id): project})
    out = await agent._dispatch_write_show_notes(
        FakeAction("write_show_notes"),
        {"project_id": str(project.id), "overwrite": True},
        session,
    )
    assert out["status"] == "written"
    assert project.show_notes == "fresh notes"


async def test_write_show_notes_refuses_another_locations_project(agent):
    project = make_project(location_id=OTHER_LOC)
    session = FakeSession({str(project.id): project})
    with pytest.raises(PermissionError):
        await agent._dispatch_write_show_notes(
            FakeAction("write_show_notes"), {"project_id": str(project.id)}, session
        )


# ── adjust_vyral_mix ──────────────────────────────────────────────────────────

def make_blueprint(mix=None, location_id=LOC):
    return Blueprint(
        id=uuid.uuid4(), location_id=location_id,
        vyral_mix=mix if mix is not None else {"viral": 0.4, "brand": 0.6},
        pillars=[{"name": "Market intelligence", "weight": 1.0}],
    )


async def test_adjust_vyral_mix_writes_a_valid_mix(agent):
    bp = make_blueprint()
    session = FakeSession(query_rows=[bp])
    new = {"viral": 0.5, "brand": 0.3, "personal": 0.2}

    out = await agent._dispatch_adjust_vyral_mix(
        FakeAction("adjust_vyral_mix"), {"vyral_mix": new}, session
    )

    assert out["status"] == "updated"
    assert bp.vyral_mix == new
    assert out["was"] == {"viral": 0.4, "brand": 0.6}


async def test_adjust_vyral_mix_refuses_an_unknown_bucket(agent):
    """
    An unknown key here would mint Posts that violate the posts.bucket CHECK
    constraint at auto-plan time — a failure a long way from its cause.
    """
    bp = make_blueprint()
    session = FakeSession(query_rows=[bp])
    out = await agent._dispatch_adjust_vyral_mix(
        FakeAction("adjust_vyral_mix"), {"vyral_mix": {"memes": 1.0}}, session
    )
    assert out["status"] == "skipped"
    assert any("unknown bucket" in p for p in out["problems"])
    assert bp.vyral_mix == {"viral": 0.4, "brand": 0.6}  # untouched


async def test_adjust_vyral_mix_refuses_weights_that_do_not_sum_to_one(agent):
    session = FakeSession(query_rows=[make_blueprint()])
    out = await agent._dispatch_adjust_vyral_mix(
        FakeAction("adjust_vyral_mix"), {"vyral_mix": {"viral": 1.0, "brand": 1.0}}, session
    )
    assert out["status"] == "skipped"
    assert any("sum" in p for p in out["problems"])


async def test_adjust_vyral_mix_skips_without_a_blueprint(agent):
    out = await agent._dispatch_adjust_vyral_mix(
        FakeAction("adjust_vyral_mix"),
        {"vyral_mix": {"viral": 0.5, "brand": 0.5}},
        FakeSession(query_rows=[]),
    )
    assert out["status"] == "skipped"
    assert "Blueprint" in out["reason"]


# ── replan_calendar ───────────────────────────────────────────────────────────

async def test_replan_clears_then_creates(agent):
    stale = [make_post(status="draft", when=LATER, source="auto_plan") for _ in range(3)]
    session = FakeSession(by_entity={"blueprints": [make_blueprint()], "posts": stale})
    out = await agent._dispatch_replan_calendar(
        FakeAction("replan_calendar"), {"slot_count": 7}, session
    )
    assert out["status"] == "replanned"
    assert out["created"] == 7
    assert out["cleared"] == 3
    assert len(session.deleted) == 3
    assert len(session.added) == 7


async def test_replan_respects_the_slot_bounds(agent):
    for bad in (0, 61, -1, "lots"):
        out = await agent._dispatch_replan_calendar(
            FakeAction("replan_calendar"), {"slot_count": bad}, FakeSession()
        )
        assert out["status"] == "skipped", f"slot_count {bad} was accepted"


async def test_replan_only_clears_auto_plan_drafts():
    """
    Replanning is not a licence to unpublish or to discard something a human
    wrote. Assert the query is constrained rather than trusting the comment.
    """
    body = inspect.getsource(BrickAgent._dispatch_replan_calendar)
    assert 'Post.status == "draft"' in body
    assert 'Post.source == "auto_plan"' in body
    assert "scheduled_at >=" in body or "Post.scheduled_at >=" in body


async def test_replan_creates_only_valid_buckets(agent):
    from services.vyral import VALID_BUCKETS

    session = FakeSession(query_rows=[])
    await agent._dispatch_replan_calendar(
        FakeAction("replan_calendar"), {"slot_count": 30}, session
    )
    for post in session.added:
        assert post.bucket in VALID_BUCKETS
        assert post.status == "draft"
        assert post.source == "auto_plan"

def _registry_routing_is_live():
    """
    Behavioural check: does _dispatch_action actually hand a registered generator
    to _dispatch_generator? Swap in a sentinel and see where the call lands.
    """
    import asyncio

    from services.brick_agent import GENERATOR_ACTIONS

    sample = next(iter(GENERATOR_ACTIONS))
    agent = BrickAgent()
    landed = {}

    async def sentinel(self, action, payload, session):
        landed["hit"] = action.action_type
        return {"action_type": action.action_type, "status": "generated"}

    original = BrickAgent._dispatch_generator
    BrickAgent._dispatch_generator = sentinel
    try:
        out = asyncio.get_event_loop().run_until_complete(
            agent._dispatch_action(FakeAction(sample), FakeSession())
        ) if False else None
        # _dispatch_action takes (action, session); call it directly.
        out = asyncio.new_event_loop().run_until_complete(
            agent._dispatch_action(FakeAction(sample), FakeSession())
        )
    finally:
        BrickAgent._dispatch_generator = original

    return landed.get("hit") == sample and (out or {}).get("status") != "no_op"


async def test_registry_routing_is_behaviourally_live():
    """
    The guard above trusts this. A registered generator must reach
    _dispatch_generator, not fall through to no_op.
    """
    from services.brick_agent import GENERATOR_ACTIONS

    sample = next(iter(GENERATOR_ACTIONS))
    agent = BrickAgent()
    landed = {}

    async def sentinel(action, payload, session):
        landed["hit"] = action.action_type
        return {"action_type": action.action_type, "status": "generated"}

    agent._dispatch_generator = sentinel
    out = await agent._dispatch_action(FakeAction(sample), FakeSession())

    assert landed.get("hit") == sample, f"{sample} did not reach _dispatch_generator"
    assert out["status"] != "no_op"
