"""
Local Story Desk (`local_story`) — the Own the Market play: local stories, many formats.

Build: pull recent local news for a market (Bing News RSS for headline + summary,
Google News RSS for extra headlines — both free, no key), drop crime/safety stories
(Fair Housing: never characterise an area's safety), let the model pick the N
stories homeowners would care about, then draft per story, in the Foundation voice:
  - caption + 5 slides (1080x1350 PNGs), stitched into one 9:16 slideshow MP4
  - Google Business Profile update
  - 30-45s reel script for the human to film
  - blog post (markdown)
Every draft runs the shared deterministic guards (no invented numbers, no Fair
Housing red flags); one redraft, then the story is dropped rather than shipped.
The build phase never touches GHL.

Commit (on approval — the human review IS the gate): per story, upload the slideshow
video and cover slide to GHL's media library, then SCHEDULE the video on Instagram +
Facebook and the update (with the cover slide) on Google Business through services/ghl_adapter (contract #5), staggered one
story per day. A PodClick calendar Post mirrors each story so /calendar shows it.
Platforms with no connected GHL account are skipped and reported. Idempotent via
commit.json — a second approve schedules nothing new.
"""
import asyncio
import html
import json
import os
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse

from services.agents.contract import AgentResult, CommitPlan, Output, StepError, foundation_tier
from services.agents.runners import avatar_video as _av

ROUTES: Tuple[str, ...] = ()

# market label -> (city, state name used in search queries)
MARKETS: Dict[str, Tuple[str, str]] = {
    "Springfield, MO": ("Springfield", "Missouri"),
    "Cheyenne, WY": ("Cheyenne", "Wyoming"),
}
TOPICS = (
    "new business opening", "development project", "city council", "construction",
    "housing", "road project", "new restaurant", "school district",
)
MAX_AGE_DAYS = 21
MAX_CANDIDATES = 40
SLIDE_COUNT = 5
SLIDE_W, SLIDE_H = 1080, 1350
POST_HOUR_UTC = 16           # 10am Central
SCHEDULE_PLATFORMS = ("instagram", "facebook", "gmb")
FORMATS = ("caption", "gmb", "script", "blog")
LABELS = {"caption": "Carousel caption", "gmb": "Google Business update",
          "script": "Reel script (you film this)", "blog": "Blog post"}
COMMIT_DONE = "commit.json"
COMMIT_STARTED = "commit.started"
WEEKLY_WAIT_S = 30 * 60

# Fair Housing + brand: never build content on crime or area safety.
_EXCLUDE = re.compile(
    r"\b(crime|criminal|shoot\w*|shot|gun\w*|arrest\w*|police|murder\w*|homicide|kill\w*|stab\w*|"
    r"robber\w*|assault\w*|theft|burglar\w*|drug\w*|sentenc\w*|charged|inmate|jail|prison|"
    r"crash\w*|fatal\w*|dead|death\w*|dies|died|obituar\w*|missing|amber alert|sex offender|"
    r"unsafe|dangerous neighborhood)\b", re.I)
_SPONSORED = re.compile(r"\bsponsored\b", re.I)

_commit_locks: Dict[str, asyncio.Lock] = {}


# ── helpers ───────────────────────────────────────────────────────────────────

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(str(tmp), str(path))


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower())[:70]


async def _complete(system: str, user: str, max_tokens: int = 900) -> str:
    """The model call; patched in tests."""
    return await _av._complete(system, user, max_tokens)


async def _fetch(url: str) -> bytes:
    """One RSS GET; patched in tests."""
    import httpx
    async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0 (PodClick Local Story Desk)"}) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


def _feed_urls(city: str, state: str) -> List[Tuple[str, str]]:
    urls = []
    for term in TOPICS:
        q = f"{city} {state} {term}"
        urls.append(("bing", f"https://www.bing.com/news/search?q={quote(q)}&format=rss"))
        g = f'"{city}" {state} {term} when:{MAX_AGE_DAYS}d'
        urls.append(("google", "https://news.google.com/rss/search?q=" + quote(g) +
                     "&hl=en-US&gl=US&ceid=US:en"))
    return urls


def _real_link(link: str) -> str:
    """Bing wraps article links in apiclick.aspx?...&url=<real>; unwrap it."""
    try:
        qs = parse_qs(urlparse(link).query)
        if qs.get("url"):
            return qs["url"][0]
    except ValueError:
        pass
    return link


def parse_feed(raw: bytes, origin: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter("item"):
        title = _strip_html(it.findtext("title") or "")
        source = _strip_html(it.findtext("source") or "")
        if origin == "google" and " - " in title:      # "Headline - Outlet"
            title, _, outlet = title.rpartition(" - ")
            source = source or outlet
        summary = _strip_html(it.findtext("description") or "") if origin == "bing" else ""
        published = None
        pub = it.findtext("pubDate")
        if pub:
            try:
                published = parsedate_to_datetime(pub)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published = None
        if title:
            items.append({"title": title, "summary": summary, "source": source,
                          "url": _real_link(it.findtext("link") or ""),
                          "published": published.isoformat() if published else None})
    return items


def keep_story(item: Dict[str, Any], city: str, now: Optional[datetime] = None) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    if _EXCLUDE.search(text) or _SPONSORED.search(text):
        return False
    if city.lower() not in text.lower():
        return False
    pub = item.get("published")
    if pub:
        try:
            age = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(pub)
            if age > timedelta(days=MAX_AGE_DAYS):
                return False
        except ValueError:
            pass
    return True


async def gather_candidates(city: str, state: str, extra: str = "") -> List[Dict[str, Any]]:
    urls = _feed_urls(city, state)
    for term in [t.strip() for t in extra.split(",") if t.strip()][:5]:
        q = f"{city} {state} {term}"
        urls.append(("bing", f"https://www.bing.com/news/search?q={quote(q)}&format=rss"))
    results = await asyncio.gather(*(_fetch(u) for _, u in urls), return_exceptions=True)
    seen, out = set(), []
    # Bing first: its items carry a summary, so they win the dedupe.
    pairs = sorted(zip(urls, results), key=lambda p: p[0][0] != "bing")
    for (origin, _), raw in pairs:
        if isinstance(raw, BaseException):
            continue
        for item in parse_feed(raw, origin):
            key = _norm_title(item["title"])
            if key in seen or not keep_story(item, city):
                continue
            seen.add(key)
            out.append(item)
    out.sort(key=lambda i: (not i["summary"], i["published"] is None))
    return out[:MAX_CANDIDATES]


def _json_block(raw: str) -> Any:
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
    return json.loads(m.group(1) if m else text)


async def pick_stories(market: str, candidates: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    listing = "\n".join(f"{i}. {c['title']} — {c['summary'][:240]} ({c['source']})"
                        for i, c in enumerate(candidates))
    system = ("You pick local news stories for a real estate investor-agent's social content. "
              "Homeowners in the market should care: new businesses and restaurants opening, "
              "developments, housing, roads, schools, city decisions that change daily life or "
              "property. Skip national news, ads, crime, and anything about how safe an area is. "
              "Answer with JSON only.")
    user = (f"Market: {market}\nPick the {n} best stories. Return a JSON list of objects "
            f'{{"index": <number>, "why": "<one sentence on why locals care>"}}.\n\n{listing}')
    picks: List[Dict[str, Any]] = []
    try:
        data = _json_block(await _complete(system, user, 600))
        for p in data if isinstance(data, list) else []:
            i = int(p.get("index"))
            if 0 <= i < len(candidates) and all(x["url"] != candidates[i]["url"] for x in picks):
                picks.append(dict(candidates[i], why=str(p.get("why") or "").strip()))
    except (ValueError, TypeError, AttributeError):
        picks = []
    for c in candidates:                       # model misfire: fall back to the newest with summaries
        if len(picks) >= n:
            break
        if all(x["url"] != c["url"] for x in picks):
            picks.append(dict(c, why=""))
    return picks[:n]


DRAFT_BRIEF = f"""Return JSON only, with these keys:
"caption": Instagram/Facebook carousel caption, 2-4 short sentences, ends with a question, max 3 hashtags.
"slides": a list of exactly {SLIDE_COUNT} objects {{"headline": "<max 7 words>", "body": "<max 22 words>"}}. Slide 1 hooks, slides 2-4 give the facts, slide 5 is a soft call to follow for local updates.
"gmb": a Google Business Profile update, 2-3 sentences, plain text, no hashtags.
"script": a 30-45 second talk-to-camera reel script. First line starts "Hook: ". Then short spoken lines.
"blog": a 300-450 word blog post in markdown with a "# " title, written for locals.
"""


def _all_text(d: Dict[str, Any]) -> str:
    slides = " ".join(f"{s.get('headline', '')} {s.get('body', '')}" for s in d.get("slides") or [])
    return " ".join(str(d.get(k) or "") for k in FORMATS) + " " + slides


def _problems(d: Any, sources: List[str]) -> List[str]:
    if not isinstance(d, dict):
        return ["it wasn't the JSON I asked for"]
    probs = []
    for k in FORMATS:
        if not str(d.get(k) or "").strip():
            probs.append(f"the {k} was empty")
    slides = d.get("slides")
    if not isinstance(slides, list) or len(slides) != SLIDE_COUNT:
        probs.append(f"it needs exactly {SLIDE_COUNT} slides")
    text = _all_text(d)
    bad = _av.fabricated_numbers(text, sources)
    if bad:
        probs.append("it used numbers the story never gave (" + ", ".join(bad) + ")")
    flags = _av.fair_housing_flags(text)
    if flags:
        probs.append("it used Fair Housing red-flag language (" + ", ".join(flags) + ")")
    if _EXCLUDE.search(text):
        probs.append("it talked about crime or safety")
    return probs


async def draft_story(bc: Any, market: str, story: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """All formats for one story, guarded. None if two attempts both break the rules."""
    system = ("You write local-community content in my voice for my real estate brand. I'm the "
              "neighbor who knows what's going on in town, not a salesperson.\n" + _av._voice_block(bc) +
              "\n\nRules:\n- Plain language. Never mention being an AI.\n- " + _av.NO_FABRICATION_RULE +
              "\n- Fair Housing: never describe who an area or home is 'perfect for', never mention "
              "family status, age, religion, race, disability, or how safe an area is."
              "\n- Only state facts from the story below. If a detail isn't there, leave it out."
              "\n\n" + DRAFT_BRIEF)
    facts = f"Market: {market}\nHeadline: {story['title']}\nSummary: {story.get('summary') or '(headline only)'}\nSource: {story.get('source') or ''}"
    sources = [market, story["title"], story.get("summary") or "", story.get("source") or ""]
    problems: List[str] = []
    for _attempt in range(2):
        user = facts
        if problems:
            user += "\n\nYour last draft broke the rules: " + "; ".join(problems) + ". Fix those."
        try:
            d = _json_block(await _complete(system, user, 2200))
        except (ValueError, TypeError):
            d = None
        problems = _problems(d, sources)
        if not problems:
            d["slides"] = [{"headline": str(s.get("headline") or "").strip(),
                            "body": str(s.get("body") or "").strip()} for s in d["slides"]]
            for k in FORMATS:
                d[k] = str(d[k]).strip()
            return d
    return None


# ── slide rendering (sync PIL; run in an executor) ───────────────────────────

def _font(size: int):
    from services.agents import render_thumbnail as _rt
    return _rt._font(size)


def _wrap(draw, text: str, font, width: int) -> List[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# Brand footer on every slide (and so on the slideshow video and the GBP cover).
# Override with env vars; a missing photo just drops the circle, never the slide.
BRAND_PHOTO = os.environ.get(
    "LOCAL_STORY_BRAND_PHOTO",
    str(Path(__file__).resolve().parents[3] / "frontend" / "static" / "brand" / "jp_headshot.png"))
BRAND_NAME = os.environ.get("LOCAL_STORY_BRAND_NAME", "JP Fluellen")
BRAND_LINE = os.environ.get("LOCAL_STORY_BRAND_LINE", "Titan Real Estate Team · Real Broker")
BRAND_HANDLE = os.environ.get("LOCAL_STORY_BRAND_HANDLE", "@jp_fluellen")
FOOTER_H = 230


def _brand_footer(img, d, accent, fg, muted, index: int, total: int) -> None:
    from PIL import Image, ImageDraw
    pad, size = 96, 150
    top = SLIDE_H - FOOTER_H
    d.line([(pad, top), (SLIDE_W - pad, top)], fill=(48, 50, 56), width=2)
    cy = top + (FOOTER_H - size) // 2 + 6
    x = pad
    try:
        photo = Image.open(BRAND_PHOTO).convert("RGB").resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size * 4, size * 4), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
        mask = mask.resize((size, size), Image.LANCZOS)
        d.ellipse([x - 5, cy - 5, x + size + 4, cy + size + 4], fill=accent)
        img.paste(photo, (x, cy), mask)
        x += size + 36
    except Exception:
        pass
    d.text((x, cy + 8), BRAND_NAME, font=_font(48), fill=fg)
    d.text((x, cy + 70), BRAND_LINE, font=_font(30), fill=muted)
    d.text((x, cy + 110), BRAND_HANDLE, font=_font(30), fill=accent)
    label = f"{index}/{total}"
    lf = _font(34)
    lw = d.textlength(label, font=lf)
    d.text((SLIDE_W - pad - lw, cy + 14), label, font=lf, fill=muted)


def render_slide(path: str, headline: str, body: str, index: int, total: int, market: str) -> None:
    from PIL import Image, ImageDraw
    bg, accent, fg, muted = (20, 22, 26), (214, 108, 52), (245, 243, 238), (176, 172, 164)
    img = Image.new("RGB", (SLIDE_W, SLIDE_H), bg)
    d = ImageDraw.Draw(img)
    pad = 96
    d.rectangle([0, 0, SLIDE_W, 14], fill=accent)
    d.text((pad, 110), market.upper(), font=_font(34), fill=accent)
    y = 230
    hf = _font(84)
    for line in _wrap(d, headline, hf, SLIDE_W - 2 * pad)[:4]:
        d.text((pad, y), line, font=hf, fill=fg)
        y += 104
    y += 50
    bf = _font(46)
    floor = SLIDE_H - FOOTER_H - 40          # body text never runs into the footer
    for line in _wrap(d, body, bf, SLIDE_W - 2 * pad):
        if y + 64 > floor:
            break
        d.text((pad, y), line, font=bf, fill=muted)
        y += 64
    _brand_footer(img, d, accent, fg, muted, index, total)
    img.save(path, "PNG")


# ── build ─────────────────────────────────────────────────────────────────────

async def run(ctx, inp: Dict[str, Any]) -> AgentResult:
    market = str(inp.get("market") or "").strip()
    if market not in MARKETS:
        raise StepError("Pick a market: " + " or ".join(MARKETS) + ".")
    city, state = MARKETS[market]
    try:
        n = max(1, min(5, int(inp.get("stories") or 3)))
    except (TypeError, ValueError):
        n = 3
    extra = str(inp.get("extra_topics") or "")

    async with ctx.step("source", f"Reading this week's {city} news"):
        candidates = await gather_candidates(city, state, extra)
        if not candidates:
            raise StepError(f"No usable {city} stories in the last {MAX_AGE_DAYS} days — the news "
                            "feeds came back empty. Run it again later.")

    async with ctx.step("pick", "Picking the stories locals will care about"):
        ctx.check_cancelled()
        picks = await pick_stories(market, candidates, n)

    async with ctx.step("foundation", "Checking the Foundation"):
        try:
            from schemas.foundation import BrandContextTaskType
            task_type: Any = BrandContextTaskType.instagram_caption
        except Exception:  # pragma: no cover
            task_type = "instagram_caption"
        try:
            bc = await ctx.brand_context(task_type, topic=f"local news in {market}")
        except StepError:
            raise
        except Exception as exc:
            raise StepError("Your Foundation isn't ready, so I won't write in a voice that isn't yours. "
                            f"({exc})")
        count = getattr(getattr(bc, "metadata", None), "sample_count", None)
        if isinstance(count, int) and foundation_tier(count) == "thin":
            ctx.warn(f"Thin Foundation ({count} samples) — drafts will sound less like you.")

    outputs: List[Any] = []
    stories: List[Dict[str, Any]] = []
    out_dir = Path(ctx.output_dir)
    loop = asyncio.get_running_loop()
    async with ctx.step("draft", "Writing each story in your voice"):
        for story in picks:
            ctx.check_cancelled()
            d = await draft_story(bc, market, story)
            if d is None:
                ctx.warn(f"Dropped \"{story['title']}\" — the drafts kept breaking the guardrails.")
                continue
            s = len(stories) + 1
            images = []
            for j, slide in enumerate(d["slides"], 1):
                name = f"story{s}_slide{j}.png"
                await loop.run_in_executor(None, render_slide, str(out_dir / name),
                                           slide["headline"], slide["body"], j, SLIDE_COUNT, market)
                images.append({"file": name, "url": f"/api/agents/jobs/{ctx.job_id}/files/{name}",
                               "label": slide["headline"]})
            video = VIDEO_NAME.format(n=s)
            duration = await build_slideshow([str(out_dir / i["file"]) for i in images], str(out_dir / video))
            stories.append({"n": s, "title": story["title"], "source": story.get("source", ""),
                            "url": story.get("url", ""), "why": story.get("why", ""),
                            "summary": story.get("summary", ""), "slides": d["slides"],
                            "images": [i["file"] for i in images], "video": video})
            head = Output(id=f"s{s}_story", kind="json", label=f"Story {s}: {story['title']}",
                          value=stories[-1])
            outs = [head,
                    Output(id=f"s{s}_video", kind="video", label=f"Story {s} slideshow (Reel)", file=video,
                           meta={"duration_s": round(duration, 1), "slides": len(images)}),
                    Output(id=f"s{s}_slides", kind="images", label=f"Story {s} slides", value=images)]
            for k in FORMATS:
                outs.append(Output(id=f"s{s}_{k}", kind="text", label=f"Story {s} — {LABELS[k]}",
                                   value=d[k], meta={"editable": True, "original": d[k], "story": s,
                                                     "format": k}))
            for o in outs:
                ctx.add_output(o)
                outputs.append(o)

    if not stories:
        raise StepError("Every story I picked broke a guardrail, so nothing is ready to review. "
                        "Run it again.")
    summary = (f"Schedule {len(stories)} {city} stor{'y' if len(stories) == 1 else 'ies'}: "
               "slideshow Reel on Instagram + Facebook and an update on Google Business, one per day")
    return AgentResult(outputs=outputs, commit=CommitPlan(summary=summary))


# ── slideshow video ───────────────────────────────────────────────────────────

SLIDE_SECONDS = 3.5        # how long each slide holds
FADE_SECONDS = 0.5         # crossfade between slides
VIDEO_W, VIDEO_H = 1080, 1920
VIDEO_NAME = "story{n}_slideshow.mp4"


async def build_slideshow(slide_paths: List[str], out_path: str,
                          size: Tuple[int, int] = (VIDEO_W, VIDEO_H)) -> float:
    """Stitch the slide PNGs into one 9:16 MP4 with crossfades and a silent track.

    GHL's API cuts a SCHEDULED post down to its first image (verified 2026-11-15),
    so a 5-slide carousel can't be scheduled. One video carries every slide and
    posts as an Instagram Reel / Facebook video. The 4:5 slides sit centred on the
    slide background colour. Returns the duration in seconds.
    """
    from services.media.ffmpeg_local import run_ffmpeg
    if not slide_paths:
        raise StepError("No slides to build the video from.")
    w, h = size
    n = len(slide_paths)
    args: List[str] = []
    for p in slide_paths:
        args += ["-loop", "1", "-t", f"{SLIDE_SECONDS}", "-i", p]
    total = n * SLIDE_SECONDS - (n - 1) * FADE_SECONDS
    args += ["-f", "lavfi", "-t", f"{total}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    parts = [f"[{i}:v]scale={w}:-2:force_original_aspect_ratio=decrease,"
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x14161A,setsar=1,fps=30,format=yuv420p[v{i}]"
             for i in range(n)]
    last = "v0"
    for i in range(1, n):
        off = i * (SLIDE_SECONDS - FADE_SECONDS)
        tag = f"x{i}"
        parts.append(f"[{last}][v{i}]xfade=transition=fade:duration={FADE_SECONDS}:offset={off}[{tag}]")
        last = tag
    args += ["-filter_complex", ";".join(parts), "-map", f"[{last}]", "-map", f"{n}:a",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", out_path]
    await run_ffmpeg(args, timeout=300)
    return total


# ── commit ────────────────────────────────────────────────────────────────────

def _ghl():
    from services.ghl_adapter import ghl_adapter
    return ghl_adapter


def _session_factory():
    from db.engine import async_session
    return async_session


def _stories_from_job(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_id = {_get(o, "id"): o for o in job.get("outputs") or []}
    stories = []
    s = 1
    while f"s{s}_story" in by_id:
        meta = dict(_get(by_id[f"s{s}_story"], "value") or {})
        for k in FORMATS:
            meta[k] = str(_get(by_id.get(f"s{s}_{k}"), "value") or "").strip()
        stories.append(meta)
        s += 1
    return stories


async def _mirror_posts(location_id: str, rows: List[Tuple[datetime, str, Dict[str, Tuple[str, List[str]]]]]) -> List[str]:
    """One calendar Post per story so /calendar shows what GHL has scheduled."""
    from db.models import Post, PostVariant
    loc = uuid.UUID(str(location_id))
    async with _session_factory()() as session:
        made = []
        for at, base, variants in rows:
            post = Post(location_id=loc, base_caption=base, scheduled_at=at,
                        status="scheduled", source="brick_proposed")  # crew-approved; ck_posts_source has no local_story
            session.add(post)
            made.append((post, variants))
        await session.flush()
        for post, variants in made:
            for plat, (caption, media) in variants.items():
                session.add(PostVariant(post_id=post.id, platform=plat, caption=caption,
                                        media_urls=media or None, platform_specific={}))
        await session.flush()
        ids = [str(p.id) for p, _ in made]
        await session.commit()
    return ids


async def commit(ctx, job: Dict[str, Any], edits: Dict[str, Any]) -> Dict[str, Any]:
    stored = job.get("commit_result")
    if stored:
        return stored
    job_id = str(job.get("id") or ctx.job_id)
    lock = _commit_locks.setdefault(job_id, asyncio.Lock())
    async with lock:
        out_dir = Path(ctx.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        done, started = out_dir / COMMIT_DONE, out_dir / COMMIT_STARTED
        if done.exists():
            return json.loads(done.read_text())
        if started.exists():
            raise StepError("A previous approve was interrupted partway — check the GHL Social "
                            "Planner before running this again so nothing posts twice.")

        stories = [s for s in _stories_from_job(job) if s.get("caption") or s.get("gmb")]
        if not stories:
            raise StepError("Nothing to schedule — every draft is empty.")

        ghl = _ghl()
        loc = ctx.location_id
        accounts = await ghl.list_accounts(loc)
        acct: Dict[str, str] = {}
        for a in accounts:
            plat = str(a.get("platform") or "").lower()
            if plat == "google":
                plat = "gmb"
            if plat and not a.get("expired") and plat not in acct:
                acct[plat] = a.get("id") or a.get("_id")
        live = [p for p in SCHEDULE_PLATFORMS if p in acct]
        skipped_platforms = [p for p in SCHEDULE_PLATFORMS if p not in acct]
        if not live:
            raise StepError("No Instagram, Facebook or Google Business account is connected in GHL, "
                            "so there's nowhere to post. Connect one in the Social Planner and approve again.")

        started.write_text("{}")
        scheduled, failed, rows = [], [], []
        first = date.today() + timedelta(days=1)
        for i, s in enumerate(stories):
            day = first + timedelta(days=i)
            at = datetime(day.year, day.month, day.day, POST_HOUR_UTC, 0, 0, tzinfo=timezone.utc)
            when = at.isoformat().replace("+00:00", "Z")
            # One slideshow video for Instagram + Facebook (GHL keeps only the first image
            # of a scheduled post, so a carousel can't survive scheduling); the cover
            # slide for Google Business, which takes a single image anyway.
            media: List[str] = []
            cover: List[str] = []
            video = s.get("video")
            if video and any(p in live for p in ("instagram", "facebook")) and (out_dir / video).exists():
                try:
                    media = [await ghl.upload_media(loc, str(out_dir / video),
                                                    filename=f"{job_id[:8]}_{video}", mime="video/mp4")]
                except Exception as exc:
                    failed.append({"story": s.get("n"), "platform": "media", "error": str(exc)[:200]})
            first_slide = (s.get("images") or [None])[0]
            if "gmb" in live and first_slide and (out_dir / first_slide).exists():
                try:
                    cover = [await ghl.upload_media(loc, str(out_dir / first_slide),
                                                    filename=f"{job_id[:8]}_{first_slide}", mime="image/png")]
                except Exception as exc:   # GBP still posts as text
                    failed.append({"story": s.get("n"), "platform": "gmb-image", "error": str(exc)[:200]})
            variants: Dict[str, Tuple[str, List[str]]] = {}
            for plat in live:
                if plat == "gmb":
                    caption, m = s.get("gmb") or "", cover
                else:
                    caption, m = s.get("caption") or "", media
                    if plat == "instagram" and not m:
                        failed.append({"story": s.get("n"), "platform": plat,
                                       "error": "Instagram needs the slideshow video and it didn't upload."})
                        continue
                if not caption:
                    continue
                try:
                    gid = await ghl.schedule(location_id=loc, platform=plat, caption=caption[:2200],
                                             account_id=acct[plat], scheduled_at=when, media_urls=m or None)
                    scheduled.append({"story": s.get("n"), "platform": plat, "ghl_post_id": gid,
                                      "scheduled_at": when})
                    variants[plat] = (caption, m)
                except Exception as exc:
                    failed.append({"story": s.get("n"), "platform": plat, "error": str(exc)[:200]})
            if variants:
                rows.append((at, s.get("caption") or s.get("gmb") or "", variants))

        if not scheduled:
            # Nothing reached GHL, so nothing can double-post: clear the marker and fail
            # loudly instead of recording a "done" job with zero posts (which is what
            # happened on the first live approve, 2026-11-15).
            started.unlink(missing_ok=True)
            first_err = failed[0]["error"] if failed else "no post was accepted"
            raise StepError(f"GHL didn't take any of the posts — nothing was scheduled. First error: {first_err}")

        post_ids: List[str] = []
        if rows:
            try:
                post_ids = await _mirror_posts(loc, rows)
            except Exception as exc:   # GHL already has them; the mirror is a convenience
                failed.append({"story": None, "platform": "calendar", "error": str(exc)[:200]})

        result: Dict[str, Any] = {
            "scheduled": scheduled, "failed": failed, "skipped_platforms": skipped_platforms,
            "post_ids": post_ids, "posts_scheduled": len(scheduled), "calendar_url": "/calendar",
            "scripts_to_film": [{"story": s.get("n"), "title": s.get("title"), "script": s.get("script")}
                                for s in stories],
            "blogs": [{"story": s.get("n"), "title": s.get("title"), "blog": s.get("blog")} for s in stories],
            "summary": (f"{len(scheduled)} posts scheduled in GHL across {len(stories)} stories"
                        + (f"; {len(failed)} failed" if failed else "")
                        + (f"; not connected: {', '.join(skipped_platforms)}" if skipped_platforms else "")),
        }
        _atomic_write_json(done, result)
        return result


# ── weekly cron entry point ──────────────────────────────────────────────────

async def weekly_run(markets: Optional[List[str]] = None) -> List[str]:
    """Start one Local Story Desk job per market and ping Telegram. Returns job ids."""
    from services.agents import jobs as _jobs
    started: List[Tuple[str, str]] = []
    lines: List[str] = []
    for market in markets or list(MARKETS):
        try:
            job = await _jobs.start("local_story", {"market": market, "stories": 3}, initiator="cron")
            started.append((market, job["id"]))
        except Exception as exc:
            lines.append(f"• {market}: didn't start ({getattr(exc, 'args', [exc])[0]})")
    tasks = [t for t in (_jobs.task_for(jid) for _, jid in started) if t is not None]
    if tasks:
        await asyncio.wait(tasks, timeout=WEEKLY_WAIT_S)
    ready = 0
    for market, jid in started:
        job = _jobs.get_job(jid)
        count = sum(1 for o in job.get("outputs") or [] if str(_get(o, "id", "")).endswith("_story"))
        status = job.get("status")
        if count:
            ready += count
            lines.append(f"• {market}: {count} stor{'y' if count == 1 else 'ies'} ready")
        else:
            lines.append(f"• {market}: nothing ready ({status})")
    await _notify(f"*Local Story Desk* — {ready} local stor{'y' if ready == 1 else 'ies'} drafted. "
                  "Nothing posts until you approve: http://127.0.0.1:8765/agents\n" + "\n".join(lines))
    return [jid for _, jid in started]


async def _notify(text: str) -> bool:
    try:
        from pipeline import telegram
        if not telegram.is_configured():
            return False
        return await telegram.send(text)
    except Exception:
        return False
