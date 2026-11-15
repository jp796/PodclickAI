"""Local Story Desk — sourcing filter, guarded drafts, slides, and GHL scheduling on approve."""
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.agents.contract import StepError
from services.agents.runners import local_story as ls

_REAL_BUILD_SLIDESHOW = ls.build_slideshow


def run(coro):
    return asyncio.run(coro)


BRAND = SimpleNamespace(
    brand_profile=SimpleNamespace(full_name="JP Fluellen", niche_primary="real estate", market_city="Springfield, MO"),
    voice_profile=SimpleNamespace(tone=["direct"], cadence="short"),
    vocabulary=SimpleNamespace(use=["here's the deal"], avoid=["synergy"]),
    voice_samples=[SimpleNamespace(text="Here's the deal.")],
    metadata=SimpleNamespace(sample_count=40),
)

NOW = datetime.now(timezone.utc)


def _rfc822(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def bing_feed(items):
    body = "".join(
        f"<item><title>{t}</title><link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fnews.example.com%2f{i}</link>"
        f"<description>{d}</description><pubDate>{_rfc822(p)}</pubDate></item>"
        for i, (t, d, p) in enumerate(items))
    return f"<rss><channel>{body}</channel></rss>".encode()


STORIES = [
    ("Huey Magoo's opens in Springfield", "The chicken tender chain opened its first Springfield location on Battlefield.", NOW),
    ("Springfield police arrest man after shooting", "Police in Springfield said a man was arrested.", NOW),
    ("Sankofa opens on Commercial Street in Springfield", "A $13.5 million mixed-use project opened in Springfield.", NOW),
    ("Old Springfield festival recap", "A Springfield festival from last year.", NOW - timedelta(days=60)),
    ("SPONSORED Springfield home tour", "Sponsored listing in Springfield.", NOW),
    ("Joplin gets new park", "A park opened in Joplin.", NOW),
]


def good_draft(numbers=""):
    return json.dumps({
        "caption": f"Huey Magoo's just opened in Springfield {numbers}. Have you tried it yet?",
        "slides": [{"headline": f"Point {chr(64 + i)}", "body": "Huey Magoo's opened on Battlefield in Springfield."}
                   for i in range(1, 6)],
        "gmb": "Huey Magoo's opened on Battlefield in Springfield. Welcome to the neighborhood.",
        "script": "Hook: New chicken spot in Springfield.\nHuey Magoo's opened on Battlefield.",
        "blog": "# Huey Magoo's opens in Springfield\n\nHuey Magoo's opened on Battlefield.",
    })


class Ctx:
    def __init__(self, out_dir, bc=BRAND):
        self.job_id, self.location_id, self.initiator = "job1abcd", str(uuid.uuid4()), "user"
        self.output_dir = out_dir
        self.outputs, self.warnings, self.steps = [], [], []
        self.bc = bc

    @asynccontextmanager
    async def step(self, key, label):
        self.steps.append(key)
        yield

    async def brand_context(self, task_type, topic=None):
        return self.bc

    def add_output(self, o):
        self.outputs.append(o)

    def warn(self, m):
        self.warnings.append(m)

    def check_cancelled(self):
        return None

    def job(self):
        return {"id": self.job_id, "outputs": [o.to_dict() for o in self.outputs], "input": {}}


@pytest.fixture
def feeds(monkeypatch):
    async def fake_fetch(url):
        if "bing.com" in url and "new+business" in url.replace("%20", "+"):
            return bing_feed(STORIES)
        return b"<rss><channel></channel></rss>"
    monkeypatch.setattr(ls, "_fetch", fake_fetch)


@pytest.fixture
def gen(monkeypatch):
    st = SimpleNamespace(queue=[], calls=[])

    async def fake(system, user, max_tokens=900):
        st.calls.append((system, user))
        if "Pick the" in user:
            return json.dumps([{"index": 0, "why": "New place to eat."}])
        return st.queue.pop(0) if st.queue else good_draft()
    monkeypatch.setattr(ls, "_complete", fake)
    return st


@pytest.fixture(autouse=True)
def fast_slideshow(monkeypatch):
    """Real ffmpeg is exercised once in test_build_slideshow_makes_a_real_vertical_video;
    everywhere else a stub writes a placeholder so the suite stays fast."""
    made = []

    async def fake(paths, out, size=None):
        made.append((list(paths), out))
        Path(out).write_bytes(b"mp4")
        return 17.5
    monkeypatch.setattr(ls, "build_slideshow", fake)
    return made


class FakeGHL:
    def __init__(self, platforms=("instagram", "facebook", "google")):
        self.platforms = platforms
        self.uploads, self.scheduled, self.listed = [], [], 0

    async def list_accounts(self, loc):
        self.listed += 1
        return [{"platform": p, "id": f"acct_{p}"} for p in self.platforms]

    async def upload_media(self, loc, path, filename="", mime=""):
        self.uploads.append((path, mime))
        return f"https://cdn.example/{filename}"

    async def schedule(self, **kw):
        self.scheduled.append(kw)
        return f"ghl_{len(self.scheduled)}"


# ── sourcing ──────────────────────────────────────────────────────────────────

def test_crime_sponsored_stale_and_out_of_town_stories_are_dropped(feeds):
    got = run(ls.gather_candidates("Springfield", "Missouri"))
    titles = [c["title"] for c in got]
    assert "Huey Magoo's opens in Springfield" in titles
    assert "Sankofa opens on Commercial Street in Springfield" in titles
    assert not any("police" in t.lower() for t in titles)
    assert not any("sponsored" in t.lower() for t in titles)
    assert not any("Old Springfield festival" in t for t in titles)
    assert not any("Joplin" in t for t in titles)


def test_bing_links_are_unwrapped_to_the_article():
    items = ls.parse_feed(bing_feed(STORIES[:1]), "bing")
    assert items[0]["url"] == "https://news.example.com/0"
    assert items[0]["summary"].startswith("The chicken tender chain")


def test_picker_falls_back_when_the_model_returns_junk(monkeypatch):
    async def junk(system, user, max_tokens=900):
        return "not json"
    monkeypatch.setattr(ls, "_complete", junk)
    cands = [{"title": f"t{i}", "summary": "s", "source": "x", "url": f"u{i}"} for i in range(4)]
    picks = run(ls.pick_stories("Springfield, MO", cands, 2))
    assert [p["url"] for p in picks] == ["u0", "u1"]


# ── build ─────────────────────────────────────────────────────────────────────

def test_run_builds_every_format_and_five_slide_images(tmp_path, feeds, gen, monkeypatch):
    ghl = FakeGHL()
    monkeypatch.setattr(ls, "_ghl", lambda: ghl)
    ctx = Ctx(tmp_path)
    res = run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1}))
    ids = {o.id for o in ctx.outputs}
    assert {"s1_story", "s1_slides", "s1_caption", "s1_gmb", "s1_script", "s1_blog"} <= ids
    from PIL import Image
    pngs = sorted(tmp_path.glob("story1_slide*.png"))
    assert len(pngs) == 5
    assert Image.open(pngs[0]).size == (1080, 1350)
    assert res.commit is not None
    assert ghl.listed == 0 and not ghl.uploads and not ghl.scheduled   # build never touches GHL


def test_invented_numbers_get_one_redraft(tmp_path, feeds, gen):
    gen.queue = [good_draft(numbers="with 400 new jobs"), good_draft()]
    ctx = Ctx(tmp_path)
    run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1}))
    redraft_prompt = gen.calls[-1][1]
    assert "numbers the story never gave" in redraft_prompt
    caption = next(o for o in ctx.outputs if o.id == "s1_caption").value
    assert "400" not in caption


def test_a_story_that_keeps_breaking_rules_is_dropped_and_run_fails_if_none_left(tmp_path, feeds, gen):
    bad = good_draft(numbers="with 400 new jobs")
    gen.queue = [bad, bad]
    ctx = Ctx(tmp_path)
    with pytest.raises(StepError):
        run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1}))
    assert any("Dropped" in w for w in ctx.warnings)


def test_unknown_market_is_refused(tmp_path):
    with pytest.raises(StepError):
        run(ls.run(Ctx(tmp_path), {"market": "Austin, TX"}))


def test_run_builds_one_slideshow_video_per_story(tmp_path, feeds, gen, fast_slideshow):
    ctx = Ctx(tmp_path)
    run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1}))
    assert len(fast_slideshow) == 1
    paths, out = fast_slideshow[0]
    assert len(paths) == 5 and out.endswith("story1_slideshow.mp4")
    video = [o for o in ctx.job()["outputs"] if ls._get(o, "id") == "s1_video"][0]
    assert ls._get(video, "kind") == "video"


def test_build_slideshow_makes_a_real_vertical_video(tmp_path):
    import shutil, subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    real = _REAL_BUILD_SLIDESHOW
    paths = []
    for i in range(3):
        p = tmp_path / f"s{i}.png"
        ls.render_slide(str(p), f"Point {chr(65 + i)}", "Body text", i + 1, 3, "Springfield, MO")
        paths.append(str(p))
    out = tmp_path / "v.mp4"
    dur = run(real(paths, str(out), size=(216, 384)))
    assert dur == pytest.approx(3 * ls.SLIDE_SECONDS - 2 * ls.FADE_SECONDS)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert probe.stdout.strip() == "216,384"


def test_every_slide_carries_the_headshot_and_still_renders_without_it(tmp_path, monkeypatch):
    from PIL import Image
    branded = tmp_path / "b.png"
    ls.render_slide(str(branded), "Point A", "Body", 1, 5, "Springfield, MO")
    assert Path(ls.BRAND_PHOTO).exists()
    cx, cy = 96 + 75, ls.SLIDE_H - ls.FOOTER_H + (ls.FOOTER_H - 150) // 2 + 6 + 75
    assert Image.open(branded).getpixel((cx, cy)) != (20, 22, 26)      # photo, not background
    monkeypatch.setattr(ls, "BRAND_PHOTO", str(tmp_path / "missing.png"))
    bare = tmp_path / "n.png"
    ls.render_slide(str(bare), "Point A", "Body", 1, 5, "Springfield, MO")
    assert Image.open(bare).size == (ls.SLIDE_W, ls.SLIDE_H)


# ── commit ────────────────────────────────────────────────────────────────────

def _built(tmp_path, feeds_gen_ctx=None):
    ctx = Ctx(tmp_path)
    run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1}))
    return ctx


def test_approve_uploads_slides_and_schedules_ig_fb_and_gmb(tmp_path, feeds, gen, monkeypatch):
    ghl = FakeGHL()
    mirrored = []

    async def fake_mirror(loc, rows):
        mirrored.extend(rows)
        return ["post-1"]
    monkeypatch.setattr(ls, "_ghl", lambda: ghl)
    monkeypatch.setattr(ls, "_mirror_posts", fake_mirror)
    ctx = _built(tmp_path)
    result = run(ls.commit(ctx, ctx.job(), {}))
    assert sorted(m for _, m in ghl.uploads) == ["image/png", "video/mp4"]
    by_plat = {s["platform"]: s for s in ghl.scheduled}
    assert set(by_plat) == {"instagram", "facebook", "gmb"}
    assert by_plat["instagram"]["media_urls"] == by_plat["facebook"]["media_urls"]
    assert len(by_plat["instagram"]["media_urls"]) == 1
    assert by_plat["instagram"]["media_urls"][0].endswith("slideshow.mp4")
    assert by_plat["gmb"]["media_urls"][0].endswith("slide1.png")
    assert by_plat["gmb"]["caption"].startswith("Huey Magoo's opened")
    assert by_plat["instagram"]["scheduled_at"].endswith("Z")
    assert result["posts_scheduled"] == 3 and result["post_ids"] == ["post-1"]
    assert result["scripts_to_film"][0]["script"].startswith("Hook:")
    assert len(mirrored) == 1


def test_second_approve_schedules_nothing_new(tmp_path, feeds, gen, monkeypatch):
    ghl = FakeGHL()

    async def fake_mirror(loc, rows):
        return ["post-1"]
    monkeypatch.setattr(ls, "_ghl", lambda: ghl)
    monkeypatch.setattr(ls, "_mirror_posts", fake_mirror)
    ctx = _built(tmp_path)
    first = run(ls.commit(ctx, ctx.job(), {}))
    second = run(ls.commit(ctx, ctx.job(), {}))
    assert first == second
    assert len(ghl.scheduled) == 3


def test_missing_accounts_are_skipped_and_reported(tmp_path, feeds, gen, monkeypatch):
    ghl = FakeGHL(platforms=("facebook",))

    async def fake_mirror(loc, rows):
        return ["post-1"]
    monkeypatch.setattr(ls, "_ghl", lambda: ghl)
    monkeypatch.setattr(ls, "_mirror_posts", fake_mirror)
    ctx = _built(tmp_path)
    result = run(ls.commit(ctx, ctx.job(), {}))
    assert [s["platform"] for s in ghl.scheduled] == ["facebook"]
    assert set(result["skipped_platforms"]) == {"instagram", "gmb"}


def test_no_connected_accounts_refuses_without_scheduling(tmp_path, feeds, gen, monkeypatch):
    ghl = FakeGHL(platforms=())
    monkeypatch.setattr(ls, "_ghl", lambda: ghl)
    ctx = _built(tmp_path)
    with pytest.raises(StepError):
        run(ls.commit(ctx, ctx.job(), {}))
    assert not ghl.scheduled
    assert not (tmp_path / ls.COMMIT_STARTED).exists()


def test_all_posts_rejected_fails_the_job_and_allows_a_retry(tmp_path, feeds, gen, monkeypatch):
    ghl = FakeGHL()

    async def reject(**kw):
        raise RuntimeError("GHL returned 422: nope")
    ghl.schedule = reject
    monkeypatch.setattr(ls, "_ghl", lambda: ghl)
    ctx = _built(tmp_path)
    with pytest.raises(StepError, match="nothing was scheduled"):
        run(ls.commit(ctx, ctx.job(), {}))
    assert not (tmp_path / ls.COMMIT_STARTED).exists()
    assert not (tmp_path / ls.COMMIT_DONE).exists()


def test_ghl_create_payload_matches_the_live_api(monkeypatch):
    """Shape GHL accepted on 2026-11-15: scheduleDate (not scheduledAt), MIME media
    types, and an empty media array for text-only posts."""
    from services import ghl_adapter as ga
    a = ga.GHLAdapter()
    p = a._build_payload("loc", "acct", "hi", status="scheduled",
                         scheduled_at="2026-11-16T16:00:00Z",
                         media_urls=["https://cdn/x/slide1.png", "https://cdn/x/v.mp4"])
    assert "scheduledAt" not in p and p["scheduleDate"] == "2026-11-16T16:00:00Z"
    assert [m["type"] for m in p["media"]] == ["image/png", "video/mp4"]
    text_only = a._build_payload("loc", "acct", "hi", status="scheduled",
                                 scheduled_at="2026-11-16T16:00:00Z")
    assert text_only["media"] == []


# ── weekly cron ───────────────────────────────────────────────────────────────

def test_weekly_run_starts_both_markets_and_pings_telegram(monkeypatch):
    from services.agents import jobs as _jobs
    started, sent = [], []

    async def fake_start(agent_id, inp, initiator="user", location_id=None):
        started.append((agent_id, inp["market"], initiator))
        return {"id": f"j{len(started)}"}
    monkeypatch.setattr(_jobs, "start", fake_start)
    monkeypatch.setattr(_jobs, "task_for", lambda jid: None)
    monkeypatch.setattr(_jobs, "get_job", lambda jid: {"status": "awaiting_approval",
                                                       "outputs": [{"id": "s1_story"}, {"id": "s2_story"}]})

    async def fake_notify(text):
        sent.append(text)
        return True
    monkeypatch.setattr(ls, "_notify", fake_notify)
    ids = run(ls.weekly_run())
    assert ids == ["j1", "j2"]
    assert [m for _, m, _ in started] == ["Springfield, MO", "Cheyenne, WY"]
    assert all(i == "cron" for _, _, i in started)
    assert "4 local stories drafted" in sent[0]
