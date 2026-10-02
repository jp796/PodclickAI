"""Facebook Page agent — every guard proven by a break test (behaviour, not grep)."""
import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from services.agents.contract import StepError
from services.agents.runners import facebook_page as fb


def run(coro):
    return asyncio.run(coro)


BRAND = SimpleNamespace(
    brand_profile=SimpleNamespace(full_name="JP Fluellen", niche_primary="real estate", market_city="Springfield, MO"),
    voice_profile=SimpleNamespace(tone=["direct"], cadence="short"),
    vocabulary=SimpleNamespace(use=["here's the deal"], avoid=["synergy"]),
    voice_samples=[SimpleNamespace(text="Here's the deal.")],
    metadata=SimpleNamespace(sample_count=40),
)


class Ctx:
    def __init__(self, out_dir, bc=BRAND, routes=None, bc_error=None):
        self.job_id, self.location_id, self.initiator = "job1", str(uuid.uuid4()), "user"
        self.output_dir = out_dir
        self.outputs, self.warnings, self.steps, self.calls = [], [], [], []
        self.bc, self.bc_error, self.routes = bc, bc_error, routes or {}
        self.bc_calls = []

    @asynccontextmanager
    async def step(self, key, label):
        self.steps.append(key)
        yield

    async def brand_context(self, task_type, topic=None):
        self.bc_calls.append((str(getattr(task_type, "value", task_type)), topic))
        if self.bc_error:
            raise self.bc_error
        return self.bc

    async def call_route(self, path, body=None, method="POST"):
        self.calls.append((method, path))
        return self.routes[f"{method} {path}"]

    def add_output(self, o):
        self.outputs.append(o)

    def warn(self, m):
        self.warnings.append(m)

    def check_cancelled(self):
        return None

    def job(self, **extra):
        j = {"id": self.job_id, "outputs": list(self.outputs), "input": {}}
        j.update(extra)
        return j


@pytest.fixture
def gen(monkeypatch):
    st = SimpleNamespace(queue=[], calls=[], default="Springfield rents are moving. Here's the deal on what to expect.")

    async def fake(system, user, max_tokens=900, **kw):
        st.calls.append((system, user))
        return st.queue.pop(0) if st.queue else st.default

    monkeypatch.setattr(fb, "_complete", fake)
    return st


class Session:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, o):
        self.log["added"].append(o)

    async def flush(self):
        for o in self.log["added"]:
            if getattr(o, "id", None) is None:
                o.id = uuid.uuid4()

    async def commit(self):
        self.log["commits"] += 1


@pytest.fixture
def db(monkeypatch):
    log = {"added": [], "commits": 0}
    monkeypatch.setattr(fb, "_session_factory", lambda: (lambda: Session(log)))
    return log


# ── build ─────────────────────────────────────────────────────────────────────

def test_build_drafts_three_formats_never_publishes(tmp_path, gen, db):
    ctx = Ctx(tmp_path)
    res = run(fb.run(ctx, {"mode": "idea", "topic": "Springfield rents", "link": "https://x.co/a"}))
    assert [o.id for o in res.outputs] == ["page_post", "photo_caption", "reel"]
    assert "https://x.co/a" in ctx.outputs[0].value, "Page post must carry the link"
    assert ctx.bc_calls == [("facebook_post", "Springfield rents")]
    assert res.commit is not None
    # no-publish-in-build: no DB write, no routes, no commit file
    assert db["added"] == [] and db["commits"] == 0
    assert ctx.calls == [] and not (tmp_path / fb.COMMIT_DONE).exists()


def test_foundation_gate_blocks_before_any_generation(tmp_path, gen):
    ctx = Ctx(tmp_path, bc_error=RuntimeError("foundation_not_ready: need 5 samples"))
    with pytest.raises(StepError, match="Foundation"):
        run(fb.run(ctx, {"mode": "idea", "topic": "x"}))
    assert gen.calls == [], "no model call may happen when the Foundation gate fails"


def test_foundation_voice_block_reaches_the_prompt(tmp_path, gen):
    run(fb.run(Ctx(tmp_path), {"mode": "idea", "topic": "x"}))
    assert all("JP Fluellen" in s and "synergy" in s for s, _ in gen.calls)


def test_thin_foundation_warns(tmp_path, gen):
    bc = SimpleNamespace(**dict(vars(BRAND), metadata=SimpleNamespace(sample_count=8)))
    ctx = Ctx(tmp_path, bc=bc)
    run(fb.run(ctx, {"mode": "idea", "topic": "x"}))
    assert any("Thin Foundation" in w for w in ctx.warnings)


def test_fair_housing_guard_redrafts_then_blocks(tmp_path, gen):
    listing = {"mode": "listing", "address": "12 Oak St", "highlights": "updated kitchen"}
    gen.queue[:] = ["Perfect for families who want a safe neighborhood.", "Updated kitchen at 12 Oak St."]
    ctx = Ctx(tmp_path)
    run(fb.run(ctx, listing))
    assert "families" not in ctx.outputs[0].value.lower()
    assert "red-flag" in gen.calls[1][1], "the redraft must be told what it broke"

    gen2 = ["Great for families."] * 2
    gen.queue[:] = gen2
    ctx2 = Ctx(tmp_path)
    with pytest.raises(StepError, match="Fair Housing"):
        run(fb.run(ctx2, listing))
    assert ctx2.outputs == [], "a flagged draft must never become an output"


def test_invented_numbers_are_blocked_but_supplied_ones_pass(tmp_path, gen):
    listing = {"mode": "listing", "address": "12 Oak St", "price": "$425,000", "beds": 3}
    gen.default = "12 Oak St: 3 beds, listed at $425,000."
    ctx = Ctx(tmp_path)
    run(fb.run(ctx, listing))
    assert len(ctx.outputs) == 3
    gen.default = "12 Oak St: 3 beds and a 7% cap rate."
    with pytest.raises(StepError, match="numbers"):
        run(fb.run(Ctx(tmp_path), listing))


def test_listing_requires_facts_and_gets_listing_post(tmp_path, gen):
    with pytest.raises(StepError, match="listing facts"):
        run(fb.run(Ctx(tmp_path), {"mode": "listing"}))
    ctx = Ctx(tmp_path)
    run(fb.run(ctx, {"mode": "listing", "address": "12 Oak St"}))
    assert [o.id for o in ctx.outputs][0] == "listing_post"


def test_episode_mode_reads_project_and_idea_needs_topic(tmp_path, gen):
    ctx = Ctx(tmp_path, routes={"GET /api/projects/p1": (200, {"title": "Ep 4", "show_notes": "Rent talk"})})
    run(fb.run(ctx, {"mode": "episode", "project_id": "p1"}))
    assert ctx.calls == [("GET", "/api/projects/p1")]
    assert "Ep 4" in gen.calls[0][1]
    with pytest.raises(StepError):
        run(fb.run(Ctx(tmp_path), {"mode": "idea"}))
    with pytest.raises(StepError):
        run(fb.run(Ctx(tmp_path), {"mode": "bogus"}))


def test_ai_disclosure_default_off_and_appended_when_on(tmp_path, gen):
    off = Ctx(tmp_path)
    run(fb.run(off, {"mode": "idea", "topic": "x"}))
    assert all(fb.DISCLOSURE not in o.value for o in off.outputs)
    on = Ctx(tmp_path)
    run(fb.run(on, {"mode": "idea", "topic": "x", "ai_disclosure": True}))
    assert all(o.value.endswith(fb.DISCLOSURE) for o in on.outputs)


# ── commit ────────────────────────────────────────────────────────────────────

def _built(tmp_path, gen, **inp):
    ctx = Ctx(tmp_path)
    run(fb.run(ctx, dict({"mode": "idea", "topic": "x"}, **inp)))
    return ctx


def test_commit_creates_draft_posts_with_clean_platform_specific(tmp_path, gen, db):
    ctx = _built(tmp_path, gen)
    res = run(fb.commit(ctx, ctx.job(), {}))
    posts = [o for o in db["added"] if type(o).__name__ == "Post"]
    variants = [o for o in db["added"] if type(o).__name__ == "PostVariant"]
    assert len(posts) == 3 and len(variants) == 3 and res["posts_created"] == 3
    assert all(p.status == "draft" and p.source == "auto_plan" for p in posts)
    assert all(v.platform == "facebook" and v.platform_specific == {} for v in variants)
    assert len({p.scheduled_at for p in posts}) == 3, "drafts are staggered, not clumped"


def test_commit_uses_edited_text_and_skips_blank(tmp_path, gen, db):
    ctx = _built(tmp_path, gen)
    ctx.outputs[0].value = "My edited post"
    ctx.outputs[1].value = "   "
    run(fb.commit(ctx, ctx.job(), {}))
    caps = sorted(v.caption for v in db["added"] if type(v).__name__ == "PostVariant")
    assert "My edited post" in caps and len(caps) == 2


def test_commit_idempotent_on_double_approve(tmp_path, gen, db):
    ctx = _built(tmp_path, gen)
    first = run(fb.commit(ctx, ctx.job(), {}))
    second = run(fb.commit(ctx, ctx.job(), {}))          # same job, file marker
    third = run(fb.commit(ctx, ctx.job(commit_result=first), {}))  # stored result
    assert first == second == third
    assert db["commits"] == 1 and len(db["added"]) == 6


def test_concurrent_double_approve_creates_once(tmp_path, gen, db):
    ctx = _built(tmp_path, gen)

    async def both():
        return await asyncio.gather(fb.commit(ctx, ctx.job(), {}), fb.commit(ctx, ctx.job(), {}))

    a, b = run(both())
    assert a == b and db["commits"] == 1


def test_interrupted_commit_refuses_rerun_and_failed_write_is_retryable(tmp_path, gen, db, monkeypatch):
    ctx = _built(tmp_path, gen)
    (tmp_path / fb.COMMIT_STARTED).write_text("{}")
    with pytest.raises(StepError, match="interrupted"):
        run(fb.commit(ctx, ctx.job(), {}))
    (tmp_path / fb.COMMIT_STARTED).unlink()

    async def boom(*a):
        raise RuntimeError("db down")

    monkeypatch.setattr(fb, "_create_posts", boom)
    with pytest.raises(RuntimeError):
        run(fb.commit(ctx, ctx.job(), {}))
    assert not (tmp_path / fb.COMMIT_STARTED).exists(), "failed write must stay retryable"


def test_commit_reappends_disclosure_if_user_edited_it_away(tmp_path, gen, db):
    ctx = _built(tmp_path, gen, ai_disclosure=True)
    ctx.outputs[0].value = "Edited, disclosure deleted"
    run(fb.commit(ctx, ctx.job(input={"ai_disclosure": True}), {}))
    assert all(v.caption.endswith(fb.DISCLOSURE) for v in db["added"] if type(v).__name__ == "PostVariant")


def test_commit_never_touches_meta_ghl_or_routes(tmp_path, gen, db):
    ctx = _built(tmp_path, gen)
    run(fb.commit(ctx, ctx.job(), {}))
    assert ctx.calls == []
    src = open(fb.__file__).read()
    assert "graph.facebook" not in src and "gohighlevel" not in src.lower()


def test_empty_commit_refused(tmp_path, db):
    with pytest.raises(StepError, match="Nothing"):
        run(fb.commit(Ctx(tmp_path), {"id": "job1", "outputs": []}, {}))


def test_registry_entry():
    from services.agents.registry import get_agent
    s = get_agent("facebook_page")
    assert s.category == "publish" and s.name == "Facebook Page"
    assert s.foundation == "required" and s.commit_tier == "bricklayer"
    assert s.runner == "services.agents.runners.facebook_page"


# ── Muse routing ──────────────────────────────────────────────────────────────

@pytest.fixture
def muse(monkeypatch):
    st = SimpleNamespace(calls=[])

    async def fake(system, user, pii):
        st.calls.append(pii)
        return "Muse drafted this one."

    monkeypatch.setattr(fb, "_complete_muse", fake)
    return st


def test_default_provider_never_touches_muse(tmp_path, gen, muse):
    run(fb.run(Ctx(tmp_path), {"mode": "idea", "topic": "x"}))
    assert muse.calls == [] and len(gen.calls) == 3


def test_muse_provider_routes_passes_pii_and_forces_disclosure(tmp_path, gen, muse):
    ctx = Ctx(tmp_path)
    run(fb.run(ctx, {"mode": "idea", "topic": "x", "provider": "muse", "contains_lead_data": True}))
    assert muse.calls == [True, True, True] and gen.calls == []
    assert ctx.bc_calls, "Foundation gate still runs for Muse"
    assert all(o.value.endswith(fb.DISCLOSURE) for o in ctx.outputs)
    run(fb.run(Ctx(tmp_path), {"mode": "idea", "topic": "x", "provider": "muse"}))
    assert muse.calls[-1] is False


def test_muse_output_still_hits_the_guards(tmp_path, gen, monkeypatch):
    async def bad(system, user, pii):
        return "Perfect for families, with 99 acres."
    monkeypatch.setattr(fb, "_complete_muse", bad)
    with pytest.raises(StepError):
        run(fb.run(Ctx(tmp_path), {"mode": "idea", "topic": "x", "provider": "muse"}))


def test_bad_provider_rejected(tmp_path, gen):
    with pytest.raises(StepError, match="Provider"):
        run(fb.run(Ctx(tmp_path), {"mode": "idea", "topic": "x", "provider": "other"}))
