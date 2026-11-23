"""Local Story Desk — sourcing filter, guarded drafts, slides, and GHL scheduling on approve."""
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.agents.contract import StepError
from services.agents.runners import local_story as ls

_REAL_BUILD_SLIDESHOW = ls.build_slideshow
_REAL_LISTINGS_FOR = ls._listings_for


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
def no_listings(monkeypatch):
    """No REOS listings feed unless a test supplies one (never shells out to REOS)."""
    async def none(market):
        return [], None
    monkeypatch.setattr(ls, "_listings_for", none)


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
        assert inp.get("listings") is True
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


# ── JP's own MLS listings ─────────────────────────────────────────────────────

def _jpeg(color=(30, 140, 200)):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (800, 600), color).save(buf, "JPEG")
    return buf.getvalue()


LISTING = {
    "mlsId": "102109", "market": "WY", "status": "active",
    "address": "312 Walker Ln, Cheyenne WY 82001", "city": "Cheyenne", "state": "WY",
    "postalCode": "82001", "listPrice": 149000, "beds": 3, "baths": 1.75, "sqft": 1087,
    "lotAcres": None, "yearBuilt": 2025, "propertyType": "Manufactured Home",
    "remarks": "3812 Walker Ln. 3 Bedrooms | 2 Bathrooms. Move-in ready with central A/C and a storage shed.",
    "photos": ["https://img.test/1.jpg", "https://img.test/2.jpg"], "listingUrl": None,
    "listOffice": {"name": "Real Broker, LLC", "phone": "307-555-0199"},
    "mlsSource": "Cheyenne MLS",
    "attribution": "Listing courtesy of Real Broker, LLC. Source: Cheyenne MLS. Information deemed reliable but not guaranteed.",
}


def listing_draft(extra=""):
    return json.dumps({
        "caption": f"Just listed: 312 Walker Ln in Cheyenne, $149,000. 3 beds, 1.75 baths, 1,087 sq ft{extra}. Want a look?",
        "slides": [{"headline": f"Walker Ln {chr(64 + i)}", "body": "Built in 2025, central A/C and a storage shed."}
                   for i in range(1, 6)],
        "gmb": "312 Walker Ln is for sale at $149,000: 3 beds, 1.75 baths, 1,087 sq ft.",
        "script": "Hook: Want to see a 2025 build in Cheyenne?\n3 beds, 1.75 baths.",
        "blog": "# 312 Walker Ln\n\nA 2025 build with central A/C, listed at $149,000.",
    })


def _feed(tmp_path, monkeypatch, markets, agent="jp@example.test", made=None):
    path = tmp_path / "own-listings.json"
    path.write_text(json.dumps({"generatedAt": (made or datetime.now(timezone.utc)).isoformat(),
                                "agentEmail": agent, "markets": markets}))
    monkeypatch.setattr(ls, "LISTINGS_FEED", str(path))
    monkeypatch.setenv("LOCAL_STORY_LISTINGS_EMAIL", "jp@example.test")
    return path


def test_load_listings_keeps_only_this_markets_active_attributed_rows(tmp_path, monkeypatch):
    pending = dict(LISTING, mlsId="2", status="pending")
    unattributed = dict(LISTING, mlsId="3", attribution="")
    _feed(tmp_path, monkeypatch, {"WY": {"ok": True, "listings": [LISTING, pending, unattributed]},
                                  "MO": {"ok": True, "listings": []}})
    rows, warn = ls.load_listings("Cheyenne, WY")
    assert [r["mlsId"] for r in rows] == ["102109"] and warn is None
    assert ls.load_listings("Springfield, MO") == ([], None)


def test_load_listings_refuses_stale_foreign_or_denied_feeds(tmp_path, monkeypatch):
    _feed(tmp_path, monkeypatch, {"WY": {"ok": True, "listings": [LISTING]}},
          made=datetime.now(timezone.utc) - timedelta(days=9))
    assert ls.load_listings("Cheyenne, WY")[0] == []
    _feed(tmp_path, monkeypatch, {"WY": {"ok": True, "listings": [LISTING]}}, agent="other@agent.test")
    rows, warn = ls.load_listings("Cheyenne, WY")
    assert rows == [] and "different agent" in warn
    _feed(tmp_path, monkeypatch, {"WY": {"ok": False, "error": "mls_access_denied: not_granted", "listings": []}})
    rows, warn = ls.load_listings("Cheyenne, WY")
    assert rows == [] and "mls_access_denied" in warn
    monkeypatch.setattr(ls, "LISTINGS_FEED", str(tmp_path / "nope.json"))
    assert ls.load_listings("Cheyenne, WY") == ([], "no listings feed yet")


def test_refresh_needs_an_email_and_the_reos_script(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCAL_STORY_LISTINGS_EMAIL", raising=False)
    assert "LOCAL_STORY_LISTINGS_EMAIL" in run(ls.refresh_listings_feed())
    monkeypatch.setenv("LOCAL_STORY_LISTINGS_EMAIL", "jp@example.test")
    monkeypatch.setattr(ls, "REOS_DIR", str(tmp_path))
    assert "own-listings-feed.ts" in run(ls.refresh_listings_feed())


def test_pick_listing_rotates_weekly():
    rows = [dict(LISTING, mlsId=str(i)) for i in range(3)]
    picked = {ls.pick_listing(rows, date(2026, 11, 2) + timedelta(weeks=w))["mlsId"] for w in range(3)}
    assert picked == {"0", "1", "2"}
    assert ls.pick_listing([]) is None


def test_listing_draft_only_uses_record_numbers_and_stamps_attribution(monkeypatch):
    calls = []
    queue = [listing_draft(extra=", 2 full baths"), listing_draft()]

    async def fake(system, user, max_tokens=900):
        calls.append(user)
        return queue.pop(0)
    monkeypatch.setattr(ls, "_complete", fake)
    d = run(ls.draft_listing(BRAND, "Cheyenne, WY", LISTING))
    assert "listing record doesn't have" in calls[-1] and "(2)" in calls[-1]   # remarks' "2 Bathrooms" isn't a record number
    assert "Remarks (description only" in calls[0] and "List price: $149,000" in calls[0]
    for k in ("caption", "gmb", "script", "blog"):
        assert LISTING["attribution"] in d[k]


def test_listing_draft_with_fair_housing_language_is_dropped(monkeypatch):
    async def fake(system, user, max_tokens=900):
        return listing_draft(extra=", perfect for families")
    monkeypatch.setattr(ls, "_complete", fake)
    assert run(ls.draft_listing(BRAND, "Cheyenne, WY", LISTING)) is None


@pytest.fixture
def with_listing(monkeypatch):
    async def rows(market):
        return [LISTING], None
    monkeypatch.setattr(ls, "_listings_for", rows)

    async def fake_fetch(url):
        if url.startswith("https://img.test/"):
            return _jpeg()
        return b"<rss><channel></channel></rss>"
    monkeypatch.setattr(ls, "_fetch", fake_fetch)

    async def fake(system, user, max_tokens=900):
        return listing_draft() if "MY listing" in system else json.dumps([])
    monkeypatch.setattr(ls, "_complete", fake)


def test_run_adds_one_listing_story_with_photo_slides_and_attribution(tmp_path, with_listing, monkeypatch):
    ghl = FakeGHL()
    monkeypatch.setattr(ls, "_ghl", lambda: ghl)
    ctx = Ctx(tmp_path)
    res = run(ls.run(ctx, {"market": "Cheyenne, WY", "stories": 1}))
    by_id = {o.id: o for o in ctx.outputs}
    story = by_id["s1_story"].value
    assert story["kind"] == "listing" and story["mls_id"] == "102109" and story["photos_used"] == 2
    assert {"s1_video", "s1_slides", "s1_caption", "s1_gmb", "s1_script", "s1_blog"} <= set(by_id)
    for k in ("caption", "gmb", "blog"):
        assert "Source: Cheyenne MLS" in by_id[f"s1_{k}"].value
    from PIL import Image
    slide = Image.open(tmp_path / "story1_slide1.png")
    assert slide.size == (1080, 1350)
    assert slide.getpixel((540, 300)) == pytest.approx((30, 140, 200), abs=12)   # the listing photo
    assert "your listing" in res.commit.summary
    assert not ghl.uploads and not ghl.scheduled                                # build never touches GHL


def test_listing_story_schedules_like_any_other_on_approve(tmp_path, feeds, gen, monkeypatch):
    async def rows(market):
        return [LISTING], None
    monkeypatch.setattr(ls, "_listings_for", rows)
    gen.queue = [good_draft(), listing_draft()]
    ghl = FakeGHL()

    async def fake_mirror(loc, rows_):
        return ["p1", "p2"]
    monkeypatch.setattr(ls, "_ghl", lambda: ghl)
    monkeypatch.setattr(ls, "_mirror_posts", fake_mirror)
    ctx = Ctx(tmp_path)
    run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1}))
    assert {o.id for o in ctx.outputs} >= {"s1_story", "s2_story"}
    result = run(ls.commit(ctx, ctx.job(), {}))
    listing_posts = [s for s in ghl.scheduled if "Cheyenne MLS" in s["caption"]]
    assert {s["platform"] for s in listing_posts} == {"instagram", "facebook", "gmb"}
    assert result["posts_scheduled"] == 6


def test_missing_listing_feed_warns_and_keeps_the_news(tmp_path, feeds, gen, monkeypatch):
    async def rows(market):
        return [], "no listings feed yet"
    monkeypatch.setattr(ls, "_listings_for", rows)
    ctx = Ctx(tmp_path)
    run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1}))
    assert any("No listing post this week — no listings feed yet" in w for w in ctx.warnings)
    assert "s2_story" not in {o.id for o in ctx.outputs}


def test_listings_off_skips_the_feed(tmp_path, feeds, gen, monkeypatch):
    async def boom(market):
        raise AssertionError("should not read listings")
    monkeypatch.setattr(ls, "_listings_for", boom)
    run(ls.run(Ctx(tmp_path), {"market": "Springfield, MO", "stories": 1, "listings": False}))


def test_listing_slide_renders_without_a_photo(tmp_path):
    from PIL import Image
    p = tmp_path / "x.png"
    ls.render_listing_slide(str(p), "Walker Ln", "Body", 1, 5, "Cheyenne, WY", None, LISTING["attribution"])
    assert Image.open(p).size == (ls.SLIDE_W, ls.SLIDE_H)


def test_a_failed_refresh_with_a_cached_feed_is_reported(tmp_path, monkeypatch):
    _feed(tmp_path, monkeypatch, {"WY": {"ok": True, "listings": [LISTING]}})

    async def fail():
        return "bun isn't installed"
    monkeypatch.setattr(ls, "refresh_listings_feed", fail)
    monkeypatch.setattr(ls, "_listings_for", _REAL_LISTINGS_FOR)
    rows, warn = run(ls._listings_for("Cheyenne, WY"))
    assert [r["mlsId"] for r in rows] == ["102109"] and "used the last feed" in warn


def test_refresh_runs_the_reos_script_without_podclicks_env(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "own-listings-feed.ts").write_text("")
    (tmp_path / ".env").write_text("")
    monkeypatch.setattr(ls, "REOS_DIR", str(tmp_path))
    monkeypatch.setattr(ls, "LISTINGS_FEED", str(tmp_path / "feed.json"))
    monkeypatch.setattr(ls, "_bun", lambda: "/usr/local/bin/bun")
    monkeypatch.setenv("LOCAL_STORY_LISTINGS_EMAIL", "jp@example.test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://podclick-db")
    seen = {}

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"wrote", b""

    async def fake_exec(*args, **kw):
        seen["args"], seen["kw"] = args, kw
        return Proc()
    monkeypatch.setattr(ls.asyncio, "create_subprocess_exec", fake_exec)
    assert run(ls.refresh_listings_feed()) is None
    assert "DATABASE_URL" not in seen["kw"]["env"] and "PATH" in seen["kw"]["env"]
    assert seen["kw"]["cwd"] == str(tmp_path)
    assert seen["args"][1:] == ("--env-file=.env", "scripts/own-listings-feed.ts", "--email", "jp@example.test",
                                "--out", str(tmp_path / "feed.json"))


def test_refresh_error_is_one_readable_line():
    raw = ("12 | const user = await prisma.user.findFirst({\n"
           "PrismaClientInitializationError: Can't reach database server at `127.0.0.1:1`\n"
           "      at handleAndLogRequestError (/x/library.js:121:6623)\n\nBun v1.3.12 (macOS x64)")
    assert ls._error_line(raw) == "PrismaClientInitializationError: Can't reach database server at `127.0.0.1:1`"


def test_dropped_listing_says_which_rule_broke(tmp_path, with_listing, monkeypatch):
    async def fake(system, user, max_tokens=900):
        return listing_draft(extra=", under $150K") if "MY listing" in system else json.dumps([])
    monkeypatch.setattr(ls, "_complete", fake)
    ctx = Ctx(tmp_path)
    with pytest.raises(StepError):
        run(ls.run(ctx, {"market": "Cheyenne, WY", "stories": 1}))
    assert any("Dropped the listing post" in w and "150" in w for w in ctx.warnings)


# ── AI-twin reels (story_reel) ────────────────────────────────────────────────

from services.agents.runners import story_reel as sr
from services.media.base import MediaProviderError


class FakeTTS:
    max_chars = 5000

    def __init__(self):
        self.texts = []

    def check_text(self, text):
        return None

    async def synthesize_speech(self, req, dest):
        self.texts.append(req.text)
        Path(dest).write_bytes(b"mp3")
        return SimpleNamespace(usage={"chars": len(req.text)}, duration_s=None)


class FakeTwin:
    def __init__(self, max_seconds=600, fail=None):
        self.max_seconds, self.fail, self.renders = max_seconds, fail, []

    def check_ready(self):
        return None

    def check_seconds(self, s):
        if s > self.max_seconds:
            raise MediaProviderError("cap", provider="heygen", user_message="Over the HeyGen cap.")

    async def generate(self, req, dest):
        if self.fail:
            raise MediaProviderError("boom", provider="heygen", user_message=self.fail)
        self.renders.append((req.aspect, req.duration_s, str(req.source_audio)))
        Path(dest).write_bytes(b"twin")
        return SimpleNamespace(usage={"seconds": req.duration_s}, meta={})


class MediaCtx(Ctx):
    def __init__(self, out_dir, tts=None, twin=None):
        super().__init__(out_dir)
        self._m = {"tts": tts, "twin_video": twin}

    def media(self, cap):
        return self._m.get(cap)


@pytest.fixture
def fake_render(monkeypatch):
    """No browser, no ffmpeg: screenshots fail (headline card), stack/captions write placeholders."""
    st = SimpleNamespace(shots=[], stacked=[], captioned=[])

    async def shot(url, title, dest):
        st.shots.append(url)
        return False

    async def dur(path):
        return 24.0

    async def stack(panel, raw, voice, duration, dest):
        st.stacked.append((str(panel), duration))
        Path(dest).write_bytes(b"stacked")

    async def burn(video, words, dest):
        st.captioned.append(len(words))
        Path(dest).write_bytes(b"final")
        return dest
    monkeypatch.setattr(sr, "capture_article", shot)
    monkeypatch.setattr(sr.ffmpeg_local, "probe_duration", dur)
    monkeypatch.setattr(sr, "stack", stack)
    monkeypatch.setattr(sr, "burn_captions", burn)
    return st


def test_spoken_text_drops_labels_and_stage_directions():
    script = ("Hook: Springfield just got a new chicken spot.\n[B-roll of storefront]\n"
              "Huey Magoo's opened on Battlefield.\n(show the menu) \nCTA: Follow for local updates.")
    assert sr.spoken_text(script) == ("Springfield just got a new chicken spot. Huey Magoo's opened on "
                                      "Battlefield. Follow for local updates.")


def test_headline_check_rejects_consent_walls():
    title = "Huey Magoo's opens in Springfield"
    assert sr.headline_visible("News-Leader  Huey Magoo's opens in Springfield on Battlefield", title)
    assert not sr.headline_visible("We value your privacy. Accept all cookies to continue.", title)


def test_panel_renders_card_without_a_screenshot_and_photo_with_one(tmp_path):
    from PIL import Image
    card = tmp_path / "card.png"
    sr.render_panel(str(card), "Source: News-Leader.", headline="Huey Magoo's opens in Springfield",
                    source="News-Leader")
    assert Image.open(card).size == (1080, 900)
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (800, 600), (30, 140, 200)).save(photo)
    out = tmp_path / "listing.png"
    sr.render_panel(str(out), "Listing courtesy of Real Broker, LLC. Source: Cheyenne MLS.", shot=str(photo),
                    chip="$149,000 · 3 bd · 1.75 ba")
    img = Image.open(out)
    assert img.size == (1080, 900)
    assert img.getpixel((540, 300)) == pytest.approx((30, 140, 200), abs=12)


def test_run_swaps_the_slideshow_for_an_ai_twin_reel(tmp_path, feeds, gen, fake_render):
    tts, twin = FakeTTS(), FakeTwin()
    ctx = MediaCtx(tmp_path, tts, twin)
    res = run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1}))
    by_id = {o.id: o for o in ctx.outputs}
    vid = by_id["s1_video"]
    assert vid.file == "story1_reel.mp4" and (tmp_path / vid.file).is_file()
    assert vid.meta["ai_generated"] is True and vid.meta["slideshow"] == "story1_slideshow.mp4"
    assert by_id["s1_story"].value["video"] == "story1_reel.mp4"           # what approve uploads
    assert ls.REEL_DISCLOSURE in by_id["s1_caption"].value
    assert twin.renders == [("16:9", 24, str(tmp_path / "story1_voice.mp3"))]
    assert "Hook" not in tts.texts[0] and "Huey Magoo's opened on Battlefield." in tts.texts[0]
    assert fake_render.captioned and fake_render.captioned[0] > 0
    assert "AI-twin Reel" in res.commit.summary
    assert not [w for w in ctx.warnings if "reel" in w.lower()]


def test_listing_reel_uses_the_listing_photo_and_attribution(tmp_path, with_listing, fake_render, monkeypatch):
    panels = []
    real = sr.render_panel

    def spy(dest, strip, shot=None, headline="", source="", chip=""):
        panels.append((strip, shot, chip))
        real(dest, strip, shot=shot, headline=headline, source=source, chip=chip)
    monkeypatch.setattr(sr, "render_panel", spy)
    ctx = MediaCtx(tmp_path, FakeTTS(), FakeTwin())
    run(ls.run(ctx, {"market": "Cheyenne, WY", "stories": 1}))
    strip, shot, chip = panels[0]
    assert "Source: Cheyenne MLS" in strip and shot.endswith("story1_photo1.jpg")
    assert chip.startswith("$149,000")
    assert fake_render.shots == []                                       # listings never hit a browser


def test_no_heygen_keeps_the_slideshow_and_never_opens_a_browser(tmp_path, feeds, gen, fake_render):
    ctx = MediaCtx(tmp_path, FakeTTS(), None)
    res = run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1}))
    by_id = {o.id: o for o in ctx.outputs}
    assert by_id["s1_video"].file == "story1_slideshow.mp4"
    assert ls.REEL_DISCLOSURE not in by_id["s1_caption"].value
    assert any("HeyGen isn't connected" in w for w in ctx.warnings)
    assert fake_render.shots == [] and "slideshow Reel" in res.commit.summary


def test_a_failed_render_keeps_that_storys_slideshow(tmp_path, feeds, gen, fake_render):
    ctx = MediaCtx(tmp_path, FakeTTS(), FakeTwin(fail="HeyGen is out of credits."))
    run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1}))
    by_id = {o.id: o for o in ctx.outputs}
    assert by_id["s1_video"].file == "story1_slideshow.mp4"
    assert any("out of credits" in w and "keeps the slideshow" in w for w in ctx.warnings)


def test_reels_off_skips_filming(tmp_path, feeds, gen, fake_render):
    tts, twin = FakeTTS(), FakeTwin()
    ctx = MediaCtx(tmp_path, tts, twin)
    run(ls.run(ctx, {"market": "Springfield, MO", "stories": 1, "reels": False}))
    assert not tts.texts and not twin.renders and "reels" not in ctx.steps


def test_heygen_budget_is_split_across_the_run(tmp_path, fake_render):
    ctx = MediaCtx(tmp_path)
    words = " ".join(["word"] * 60)                                      # ~23 s each
    jobs = [{"n": i, "script": words, "panel": str(tmp_path / "p.png")} for i in (1, 2, 3)]
    made = run(sr.build_all(ctx, FakeTTS(), FakeTwin(max_seconds=50), jobs))
    assert sorted(made) == [1, 2]
    assert any("Story 3" in w and "HEYGEN_MAX_SECONDS" in w for w in ctx.warnings)


def test_overlong_script_gets_no_reel(tmp_path, fake_render):
    ctx = MediaCtx(tmp_path)
    got = run(sr.build_reel(ctx, FakeTTS(), FakeTwin(), tmp_path, 1, " ".join(["word"] * 200),
                            tmp_path / "p.png"))
    assert got is None and "reel limit" in ctx.warnings[0]


def test_spelled_out_numbers_are_checked_like_digits():
    assert ls.spelled_numbers("Five beds, two full baths and three more upstairs. No one asked.") == ["5", "2", "3"]
    d = json.loads(good_draft())
    d["script"] = "Hook: Five beds in town.\nThree more upstairs."
    probs = ls._problems(d, ["Bedrooms 5", "Huey Magoo's opens in Springfield"])
    assert probs and "(3)" in probs[0]                 # 5 is in the record, 3 is not
    d["script"] = "Hook: Five beds in town."
    assert ls._problems(d, ["Bedrooms: five", "Huey Magoo's"]) == []   # spelled in the source counts too


def test_content_box_finds_the_picture_inside_light_grey_bars(tmp_path):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1080, 1920), (242, 242, 244))
    d = ImageDraw.Draw(im)
    for x in range(0, 1080, 40):                                   # a busy 4:3 picture in the middle
        d.rectangle([x, 555, x + 19, 1364], fill=(30, 30, 30))
    p = tmp_path / "f.png"
    im.save(p)
    w, h, x, y = sr.content_box(str(p))
    assert (x, y) == (0, 555) and abs(w - 1060) <= 20 and h == 810
    Image.new("RGB", (1080, 1920), (242, 242, 244)).save(p)
    assert sr.content_box(str(p)) is None                          # flat frame: leave it alone


# ── daily cron + no repeats ───────────────────────────────────────────────────

def _cron_fakes(monkeypatch):
    from services.agents import jobs as _jobs
    started, sent = [], []

    async def fake_start(agent_id, inp, initiator="user", location_id=None):
        started.append(dict(inp, initiator=initiator))
        return {"id": f"j{len(started)}"}
    monkeypatch.setattr(_jobs, "start", fake_start)
    monkeypatch.setattr(_jobs, "task_for", lambda jid: None)
    monkeypatch.setattr(_jobs, "get_job", lambda jid: {"status": "awaiting_approval",
                                                       "outputs": [{"id": "s1_story"}]})

    async def fake_notify(text):
        sent.append(text)
        return True
    monkeypatch.setattr(ls, "_notify", fake_notify)
    return started, sent


def test_daily_run_one_story_per_market_listing_only_on_monday(monkeypatch):
    from datetime import date
    started, sent = _cron_fakes(monkeypatch)
    run(ls.daily_run(today=date(2026, 11, 24)))          # a Tuesday
    assert [s["market"] for s in started] == ["Springfield, MO", "Cheyenne, WY"]
    assert all(s["stories"] == 1 and s["listings"] is False and s["reels"] is True for s in started)
    assert all(s["initiator"] == "cron" for s in started)
    assert "daily" in sent[0] and "2 local stories drafted" in sent[0]
    started.clear()
    run(ls.daily_run(today=date(2026, 11, 23)))          # a Monday
    assert all(s["listings"] is True for s in started)


def test_used_story_keys_skips_recent_drafts_for_that_market(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from services.agents import jobs as _jobs
    now = datetime.now(timezone.utc)
    story = {"id": "s1_story", "value": {"url": "https://x.test/a", "title": "Big New Park Opens!"}}
    jobs = {
        "recent": {"id": "recent", "agent_id": "local_story", "status": "needs_approval",
                   "input": {"market": "Cheyenne, WY"}, "created_at": now.isoformat(), "outputs": [story]},
        "old": {"id": "old", "agent_id": "local_story", "status": "done",
                "input": {"market": "Cheyenne, WY"}, "created_at": (now - timedelta(days=40)).isoformat(),
                "outputs": [{"id": "s1_story", "value": {"url": "https://x.test/old", "title": "Old"}}]},
        "failed": {"id": "failed", "agent_id": "local_story", "status": "failed",
                   "input": {"market": "Cheyenne, WY"}, "created_at": now.isoformat(),
                   "outputs": [{"id": "s1_story", "value": {"url": "https://x.test/f", "title": "F"}}]},
        "other": {"id": "other", "agent_id": "local_story", "status": "done",
                  "input": {"market": "Springfield, MO"}, "created_at": now.isoformat(),
                  "outputs": [{"id": "s1_story", "value": {"url": "https://x.test/s", "title": "S"}}]},
    }
    monkeypatch.setattr(_jobs, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(_jobs, "AGENT_JOBS", jobs)
    used = ls.used_story_keys("Cheyenne, WY")
    cands = [{"url": "https://x.test/a", "title": "x"},
             {"url": "https://y.test/syndicated", "title": "Big new park opens"},
             {"url": "https://x.test/old", "title": "Old"},
             {"url": "https://x.test/f", "title": "F"},
             {"url": "https://x.test/s", "title": "S"}]
    assert [c["url"] for c in ls.drop_used(cands, used)] == \
        ["https://x.test/old", "https://x.test/f", "https://x.test/s"]
    assert ls.used_story_keys("Cheyenne, WY", exclude_job="recent") == set()
