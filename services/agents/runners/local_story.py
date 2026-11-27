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
Plus one of JP's OWN active MLS listings per market (from a REOS feed that runs
REOS's MLS licensing gate and public-display projection — see "own MLS listings"
below): same formats, the listing's photos on the slides, numbers only from the
listing record, and the brokerage + MLS attribution stamped on every format.
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
from services.agents.runners import story_reel as _reel

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


ARTICLE_CHARS = 3500        # article text handed to the drafter


def article_text(raw: bytes, limit: int = ARTICLE_CHARS) -> str:
    """The story's paragraphs from an article page — the specifics an RSS blurb lacks
    (names, addresses, dates, what's actually going in). Empty when it isn't an article."""
    page = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    page = re.sub(r"(?is)<(script|style|noscript|nav|header|footer|aside|form)\b.*?</\1>", " ", page)
    paras = []
    for m in re.finditer(r"(?is)<p\b[^>]*>(.*?)</p>", page):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
        if len(t) >= 60 and not re.search(r"(?i)cookie|subscribe|sign up|newsletter|all rights reserved|"
                                           r"advertis|click here|javascript", t):
            paras.append(t)
    out = ""
    for t in paras:
        if len(out) + len(t) > limit:
            break
        out += t + "\n"
    return out.strip()


async def fetch_article(url: str) -> str:
    """Best-effort article body; '' on any failure (the summary still carries the story)."""
    if not url or "news.google.com" in url:
        return ""
    try:
        return article_text(await _fetch(url))
    except Exception:
        return ""


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


USED_LOOKBACK_DAYS = 21


def used_story_keys(market: str, days: int = USED_LOOKBACK_DAYS,
                    exclude_job: Optional[str] = None) -> set:
    """URLs and normalised titles of stories this market already drafted recently.

    Daily runs read the same news feeds every morning; without this they'd redraft
    yesterday's story. Failed and cancelled jobs don't count — nothing came of them."""
    try:
        from services.agents import jobs as _jobs
        _jobs._ensure_loaded()
        rows = list(_jobs.AGENT_JOBS.values())
    except Exception:
        return set()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    keys: set = set()
    for j in rows:
        if j.get("agent_id") != "local_story" or j.get("id") == exclude_job:
            continue
        if (j.get("input") or {}).get("market") != market or j.get("status") in ("failed", "cancelled"):
            continue
        if str(j.get("created_at") or "") < cutoff:
            continue
        for o in j.get("outputs") or []:
            if not str(_get(o, "id", "")).endswith("_story"):
                continue
            v = _get(o, "value") or {}
            if isinstance(v, dict):
                if v.get("url"):
                    keys.add(str(v["url"]))
                if v.get("title"):
                    keys.add(_norm_title(str(v["title"])))
    return keys


def drop_used(candidates: List[Dict[str, Any]], used: set) -> List[Dict[str, Any]]:
    return [c for c in candidates
            if c.get("url") not in used and _norm_title(c.get("title") or "") not in used]


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
    system = ("You pick local news stories for a real estate investor-agent's social content. The "
              "goal is the scroll-stop: a local sees it and thinks 'wait, THAT'S going in there?' "
              "and shares it with a friend. Rank highest: a named brand or restaurant coming to a "
              "specific spot people drive past, a big employer or a big dollar figure, a road or "
              "landmark that changes, a surprising or first-ever thing, something locals will argue "
              "about. Rank lowest: routine committee votes, permits and license approvals with no "
              "new place in them, press-release fluff, anything vague about 'growth'. "
              "Skip national news, ads, crime, and anything about how safe an area is. "
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


HOOK_RULES = """How to grab attention (this matters more than anything else):
- Lead with the most surprising, specific thing: the brand name, the exact spot or street, the dollar figure, the date it opens. Never lead with the process ("committee approves", "council votes", "liquor license", "plans submitted").
- Open a curiosity gap the next line pays off: "Something big is going in where the old ___ was." / "Locals have been asking what's being built on ___. Here's the answer."
- Talk like a neighbor texting a friend the news, not a news anchor. Short words. Contractions. One idea per line.
- Every line must add a NEW fact or a reason to care. Never restate the headline, never pad with "this shows growth", "more options for residents", "the local economy is moving", "stay tuned".
- Use the article's real specifics (who, what, exactly where, when, how big, what was there before). If the story only has a few facts, use fewer, sharper lines rather than filler.
- End on a question locals will actually answer in the comments ("Would you go?", "What should go in next to it?", "Did you know this was happening?").
Bad hook: "Cheyenne's getting two new businesses with liquor licenses."
Good hook: "Wing Shack is coming to Cheyenne. Here's where."
"""

DRAFT_BRIEF = HOOK_RULES + f"""
Return JSON only, with these keys:
"caption": Instagram/Facebook carousel caption. First line is the hook (under 12 words, works alone before "...more"). Then 1-3 short lines of the best specifics, then a question. Max 3 hashtags.
"slides": a list of exactly {SLIDE_COUNT} objects {{"headline": "<max 7 words>", "body": "<max 22 words>"}}. Slide 1 is a scroll-stopping hook headline with a curiosity gap (body teases, doesn't give it all away). Slides 2-4 each reveal ONE new specific (where exactly, what it is / what was there before, when / how big). No two slides repeat a fact. Slide 5 asks the comment question and says to follow for what's coming to town.
"gmb": a Google Business Profile update, 2-3 sentences, plain text, no hashtags, leading with the specific news.
"script": a 20-35 second talk-to-camera reel script (55-85 words): the first line starts "Hook: " and is a pattern-interrupt under 12 words that names the town and the specific thing; then 3-5 short spoken lines, each a new specific or why it matters to locals; end with the comment question. Spoken words only: no stage directions, emojis or hashtags.
"blog": a 300-450 word blog post in markdown with a "# " title, written for locals, answering what, where, when and what it means for the area.
"""


def _all_text(d: Dict[str, Any]) -> str:
    slides = " ".join(f"{s.get('headline', '')} {s.get('body', '')}" for s in d.get("slides") or [])
    return " ".join(str(d.get(k) or "") for k in FORMATS) + " " + slides


_NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_NUM_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
                   "ninety": 90})
for _w in ("zero", "one"):          # "no one", "the one" — too common to treat as figures
    _NUM_WORDS.pop(_w)


def spelled_numbers(text: str) -> List[str]:
    """Spelled-out numbers as digits ("five beds" -> "5"), so the digit guard sees them.
    Reels are spoken, and the script writes numbers the way they're said."""
    return [str(_NUM_WORDS[w]) for w in re.findall(r"[a-z]+", (text or "").lower()) if w in _NUM_WORDS]


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
    sources = list(sources) + [" ".join(spelled_numbers(" ".join(sources)))]
    bad = _av.fabricated_numbers(text + " " + " ".join(spelled_numbers(text)), sources)
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
    body = story.get("article")
    if body is None:
        body = story["article"] = await fetch_article(story.get("url") or "")
    facts = f"Market: {market}\nHeadline: {story['title']}\nSummary: {story.get('summary') or '(headline only)'}\nSource: {story.get('source') or ''}"
    if body:
        facts += f"\n\nArticle text (the only other facts you may use):\n{body}"
    sources = [market, story["title"], story.get("summary") or "", story.get("source") or "", body]
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


# ── JP's own MLS listings (from REOS) ────────────────────────────────────────
#
# REOS owns the MLS license gate, so PodClick never talks to an MLS. A REOS script
# (scripts/own-listings-feed.ts) runs AS the listing agent through
# getMlsProviderForActor → requireMlsAccess, keeps only that agent's own ACTIVE
# listings, projects them with toPublicListing and writes a JSON feed. Each row
# carries the listing-firm + MLS source line that every public post must show.
# Here we refresh that feed (best effort), fall back to the last good copy if it
# is under a week old, and turn one listing per market into a story.

REOS_DIR = os.path.expanduser(os.environ.get("LOCAL_STORY_REOS_DIR", "~/real-estate-os"))
LISTINGS_FEED = os.path.expanduser(os.environ.get("LOCAL_STORY_LISTINGS_FEED", "~/.reos/own-listings.json"))
LISTINGS_SCRIPT = "scripts/own-listings-feed.ts"
LISTINGS_FEED_MAX_AGE = timedelta(days=8)
LISTINGS_REFRESH_TIMEOUT_S = 180
MARKET_CODES = {"Springfield, MO": "MO", "Cheyenne, WY": "WY"}


def _listings_email() -> str:
    return os.environ.get("LOCAL_STORY_LISTINGS_EMAIL", "").strip().lower()


def _bun() -> Optional[str]:
    import shutil
    found = shutil.which("bun")
    if found:
        return found
    fallback = os.path.expanduser("~/.bun/bin/bun")
    return fallback if os.path.exists(fallback) else None


async def refresh_listings_feed() -> Optional[str]:
    """Rebuild the feed via the REOS script. Returns an error string, or None on success."""
    email = _listings_email()
    if not email:
        return "LOCAL_STORY_LISTINGS_EMAIL isn't set"
    if not os.path.exists(os.path.join(REOS_DIR, LISTINGS_SCRIPT)):
        return f"{LISTINGS_SCRIPT} isn't in {REOS_DIR}"
    bun = _bun()
    if not bun:
        return "bun isn't installed"
    Path(LISTINGS_FEED).parent.mkdir(parents=True, exist_ok=True)
    args = [bun]
    for env_file in (".env", ".env.local"):   # .env.local (if present) points at the live DB
        if os.path.exists(os.path.join(REOS_DIR, env_file)):
            args.append(f"--env-file={env_file}")
    args += [LISTINGS_SCRIPT, "--email", email, "--out", LISTINGS_FEED]
    # A bare environment: bun's --env-file never overrides a variable that's already set,
    # so PodClick's own DATABASE_URL (and friends) would point the REOS script at the
    # wrong database. REOS's env files supply everything it needs.
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "USER", "LANG", "TMPDIR", "TZ")}
    proc = await asyncio.create_subprocess_exec(*args, cwd=REOS_DIR, env=env, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=LISTINGS_REFRESH_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return "the REOS listings script timed out"
    if proc.returncode != 0:
        return _error_line((err or out or b"").decode("utf-8", "replace")) or f"exit {proc.returncode}"
    return None


def _error_line(text: str) -> str:
    """The one line of a bun/Prisma failure worth showing on the punch list (no stack frames)."""
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not l.strip().startswith(("at ", "Bun v", "^", "|")) and not l.strip()[:1].isdigit()]
    pick = next((l for l in lines if "rror" in l or "denied" in l or "No user" in l), lines[0] if lines else "")
    return pick[:200]


def load_listings(market: str, now: Optional[datetime] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Active own listings for a market from the feed file, plus a warning when there are none to use."""
    code = MARKET_CODES.get(market)
    try:
        feed = json.loads(Path(LISTINGS_FEED).read_text())
    except FileNotFoundError:
        return [], "no listings feed yet"
    except (OSError, ValueError) as exc:
        return [], f"the listings feed is unreadable ({exc})"
    try:
        made = datetime.fromisoformat(str(feed.get("generatedAt", "")).replace("Z", "+00:00"))
    except ValueError:
        return [], "the listings feed has no date"
    if (now or datetime.now(timezone.utc)) - made > LISTINGS_FEED_MAX_AGE:
        return [], "the listings feed is more than a week old"
    want = _listings_email()
    if want and str(feed.get("agentEmail") or "").strip().lower() != want:
        return [], "the listings feed belongs to a different agent"
    entry = (feed.get("markets") or {}).get(code) or {}
    if not entry.get("ok", False):
        return [], f"REOS couldn't read the {code} MLS ({entry.get('error') or 'not in the feed'})"
    rows = [r for r in entry.get("listings") or []
            if r.get("status") == "active" and r.get("market") == code and r.get("attribution")
            and r.get("mlsId") and r.get("address")]
    return rows, None


async def _listings_for(market: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Refresh (best effort) then load. Patched in tests."""
    err = await refresh_listings_feed()
    rows, warn = load_listings(market)
    if err:
        warn = (f"{warn}; refresh failed: {err}" if warn
                else f"couldn't refresh listings from REOS ({err}), used the last feed")
    return rows, warn


def pick_listing(listings: List[Dict[str, Any]], today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """One listing, rotating weekly so every active listing gets its turn."""
    if not listings:
        return None
    ordered = sorted(listings, key=lambda r: str(r.get("mlsId")))
    week = (today or date.today()).isocalendar()[1]
    return ordered[week % len(ordered)]


def _fmt_num(n: Any) -> str:
    if n is None:
        return ""
    f = float(n)
    return f"{int(f):,}" if f == int(f) else f"{f:g}"


def listing_facts(listing: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The structured fields the draft may use, in order. Only these carry numbers."""
    rows = [("Address", listing.get("address") or ""),
            ("Status", "Active, for sale"),
            ("List price", f"${_fmt_num(listing.get('listPrice'))}" if listing.get("listPrice") is not None else ""),
            ("Bedrooms", _fmt_num(listing.get("beds"))),
            ("Bathrooms", _fmt_num(listing.get("baths"))),
            ("Living area (sq ft)", _fmt_num(listing.get("sqft"))),
            ("Lot (acres)", _fmt_num(listing.get("lotAcres"))),
            ("Year built", str(listing.get("yearBuilt") or "")),
            ("Property type", listing.get("propertyType") or ""),
            ("Listing brokerage", (listing.get("listOffice") or {}).get("name") or "")]
    return [(k, v) for k, v in rows if v]


LISTING_BRIEF = DRAFT_BRIEF.replace(
    "Slide 5 asks the comment question and says to follow for what's coming to town.",
    "Slide 5 is a soft call to message me for a showing.").replace(
    "written for locals, answering what, where, when and what it means for the area.",
    "about this home and its area, written for local buyers.")
assert LISTING_BRIEF.count("message me for a showing") == 1 and "local buyers" in LISTING_BRIEF


LISTING_ATTEMPTS = 3


async def draft_listing(bc: Any, market: str, listing: Dict[str, Any],
                        problems_out: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """All formats for one of JP's own listings, guarded. Numbers may only come from the
    structured listing fields (not the free-text remarks). None if every attempt breaks the
    rules; the last attempt's problems go into `problems_out` so the punch list can say why."""
    facts_rows = listing_facts(listing)
    system = ("You write listing content in my voice for my real estate brand. This is MY listing — "
              "I'm the listing agent. Sound like a neighbor showing a friend a house, not an ad.\n"
              + _av._voice_block(bc) +
              "\n\nRules:\n- Plain language. Never mention being an AI.\n- " + _av.NO_FABRICATION_RULE +
              "\n- Every number (price, beds, baths, square feet, year, address number) must come from the "
              "Facts lines exactly as written. Never take a number from the remarks. Write the price in full, "
              "like $149,000, never $149K."
              "\n- Fair Housing: describe the property, never the people. Never say who a home is 'perfect "
              "for', never mention family status, age, religion, race, national origin, sex, disability, "
              "schools' quality, or how safe an area is."
              "\n- Don't invent features. Use only what the Facts and remarks say."
              "\n- No claims about the price versus the market or other homes (no 'under market', "
              "'best deal', 'priced to sell', 'won't last')."
              "\n- Don't add the brokerage or MLS disclaimer yourself; I add it after."
              "\n\n" + LISTING_BRIEF)
    sources = [market] + [v for _, v in facts_rows] + [str(listing.get("attribution") or "")]
    allowed = sorted(set().union(*(_av.numbers_in(s) for s in sources)), key=lambda x: float(x))
    facts = (f"Market: {market}\n" + "\n".join(f"{k}: {v}" for k, v in facts_rows)
             + f"\nRemarks (description only, no numbers from here): {listing.get('remarks') or '(none)'}"
             + "\n\nThe ONLY numbers you may write anywhere (prices with commas are fine): "
             + ", ".join(allowed) + ". Any other number (room sizes, distances, counts, rounded "
             "prices) gets the draft rejected — describe those things in words instead.")
    problems: List[str] = []
    for _attempt in range(LISTING_ATTEMPTS):
        user = facts
        if problems:
            user += "\n\nYour last draft broke the rules: " + "; ".join(problems) + ". Fix those."
        try:
            d = _json_block(await _complete(system, user, 2200))
        except (ValueError, TypeError):
            d = None
        problems = [p.replace("the story never gave", "the listing record doesn't have")
                    for p in _problems(d, sources)]
        if not problems:
            d["slides"] = [{"headline": str(s.get("headline") or "").strip(),
                            "body": str(s.get("body") or "").strip()} for s in d["slides"]]
            for k in FORMATS:
                d[k] = str(d[k]).strip()
            return with_attribution(d, listing)
    if problems_out is not None:
        problems_out.extend(problems)
    return None


def with_attribution(d: Dict[str, Any], listing: Dict[str, Any]) -> Dict[str, Any]:
    """Stamp the required MLS/brokerage attribution (and the link, if any) on every text format.
    Deterministic, so the model can't drop or reword it."""
    attr = str(listing.get("attribution") or "").strip()
    url = str(listing.get("listingUrl") or "").strip()
    link = f"\nDetails: {url}" if url else ""
    out = dict(d)
    out["caption"] = f"{d['caption']}\n\n{attr}{link}"
    out["gmb"] = f"{d['gmb']} {attr}{(' ' + url) if url else ''}"
    out["script"] = f"{d['script']}\n\n[On-screen text: {attr}]"
    out["blog"] = f"{d['blog']}\n\n---\n\n*{attr}*" + (f"\n\n[See the full listing]({url})" if url else "")
    return out


async def fetch_listing_photos(listing: Dict[str, Any], out_dir: Path, s: int, count: int) -> List[Optional[str]]:
    """Download up to `count` listing photos as JPEGs; a failed one is None (that slide goes text-only)."""
    from io import BytesIO
    from PIL import Image
    urls = [u for u in listing.get("photos") or [] if isinstance(u, str) and u.startswith("http")][:count]
    raws = await asyncio.gather(*(_fetch(u) for u in urls), return_exceptions=True)
    paths: List[Optional[str]] = []
    for j, raw in enumerate(raws, 1):
        if isinstance(raw, BaseException):
            paths.append(None)
            continue
        try:
            img = Image.open(BytesIO(raw)).convert("RGB")
            p = out_dir / f"story{s}_photo{j}.jpg"
            img.save(p, "JPEG", quality=90)
            paths.append(str(p))
        except Exception:
            paths.append(None)
    return paths


LISTING_PHOTO_H = 640


def render_listing_slide(path: str, headline: str, body: str, index: int, total: int, market: str,
                         photo: Optional[str], attribution: str) -> None:
    """A listing slide: the listing's own photo on top, copy below, the MLS attribution line,
    then the same headshot footer as every other slide."""
    from PIL import Image, ImageDraw, ImageOps
    bg, accent, fg, muted = (20, 22, 26), (214, 108, 52), (245, 243, 238), (176, 172, 164)
    img = Image.new("RGB", (SLIDE_W, SLIDE_H), bg)
    d = ImageDraw.Draw(img)
    pad = 80
    top = 0
    if photo:
        try:
            ph = ImageOps.fit(Image.open(photo).convert("RGB"), (SLIDE_W, LISTING_PHOTO_H), Image.LANCZOS)
            img.paste(ph, (0, 0))
            top = LISTING_PHOTO_H
        except Exception:
            top = 0
    d.rectangle([0, top, SLIDE_W, top + 12], fill=accent)
    label = f"{market.upper()} · MY LISTING"
    if top:
        lf = _font(30)
        lw = d.textlength(label, font=lf)
        d.rectangle([pad - 16, 40, pad + lw + 16, 96], fill=bg)
        d.text((pad, 50), label, font=lf, fill=accent)
        y = top + 50
        hf, hstep, bf, bstep = _font(64), 78, _font(40), 54
    else:
        d.text((pad, 110), label, font=_font(34), fill=accent)
        y = 230
        hf, hstep, bf, bstep = _font(84), 104, _font(46), 64
    for line in _wrap(d, headline, hf, SLIDE_W - 2 * pad)[:3]:
        d.text((pad, y), line, font=hf, fill=fg)
        y += hstep
    y += 24
    af = _font(24)
    attr_lines = _wrap(d, attribution, af, SLIDE_W - 2 * pad)[:2]
    attr_top = SLIDE_H - FOOTER_H - 20 - 32 * len(attr_lines)
    for line in _wrap(d, body, bf, SLIDE_W - 2 * pad):
        if y + bstep > attr_top - 10:
            break
        d.text((pad, y), line, font=bf, fill=muted)
        y += bstep
    for k, line in enumerate(attr_lines):
        d.text((pad, attr_top + 32 * k), line, font=af, fill=muted)
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
    want_listing = inp.get("listings", True) not in (False, "false", "0", 0, "")
    want_reels = inp.get("reels", True) not in (False, "false", "0", 0, "")

    listing: Optional[Dict[str, Any]] = None
    if want_listing:
        async with ctx.step("listings", "Checking your active MLS listings"):
            rows, warn = await _listings_for(market)
            listing = pick_listing(rows)
            if listing is None:
                ctx.warn(f"No listing post this week — {warn or 'you have no active listings in ' + market}.")
            elif warn:
                ctx.warn(f"Listing post: {warn}.")

    async with ctx.step("source", f"Reading this week's {city} news"):
        candidates = await gather_candidates(city, state, extra)
        fresh = drop_used(candidates, used_story_keys(market, exclude_job=ctx.job_id))
        if candidates and not fresh:
            ctx.warn(f"Every {city} story in the feeds was already drafted in the last "
                     f"{USED_LOOKBACK_DAYS} days.")
        candidates = fresh
        if not candidates and listing is None:
            raise StepError(f"No new {city} stories in the last {MAX_AGE_DAYS} days — the feeds were "
                            "empty or already used. Run it again later.")
        if not candidates:
            ctx.warn(f"No new {city} news — only your listing is in this batch.")

    async with ctx.step("pick", "Picking the stories locals will care about"):
        ctx.check_cancelled()
        picks = await pick_stories(market, candidates, n) if candidates else []

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
            outputs.extend(outs)

    if listing is not None:
        async with ctx.step("listing", f"Writing your listing post for {listing['address']}"):
            ctx.check_cancelled()
            why: List[str] = []
            d = await draft_listing(bc, market, listing, why)
            if d is None:
                ctx.warn(f"Dropped the listing post for {listing['address']} — the drafts kept breaking "
                         "the guardrails (" + "; ".join(why) + ").")
            else:
                outs = await _listing_outputs(ctx, out_dir, len(stories) + 1, market, listing, d, stories)
                outputs.extend(outs)

    if not stories:
        raise StepError("Every story I picked broke a guardrail, so nothing is ready to review. "
                        "Run it again.")

    reels = 0
    if want_reels:
        async with ctx.step("reels", "Filming each story with your AI twin"):
            ctx.check_cancelled()
            reels = await _add_reels(ctx, out_dir, stories, outputs)
    elif callable(getattr(ctx, "skip_step", None)):
        ctx.skip_step("reels", "Filming each story with your AI twin")
    for o in outputs:
        ctx.add_output(o)

    has_listing = any(s.get("kind") == "listing" for s in stories)
    modes = [s.get("reel_mode") for s in stories]
    if reels and all(m == "twin" for m in modes):
        video_kind = "AI-twin Reel"
    elif reels and all(m == "voiceover" for m in modes):
        video_kind = "voice-over Reel (your AI voice)"
    elif reels:
        video_kind = "Reel (AI twin where it rendered, voice-over or slideshow otherwise)"
    else:
        video_kind = "slideshow Reel"
    summary = (f"Schedule {len(stories)} {city} stor{'y' if len(stories) == 1 else 'ies'}"
               + (" (including your listing)" if has_listing else "") +
               f": {video_kind} on Instagram + Facebook and an update on Google Business, one per day")
    return AgentResult(outputs=outputs, commit=CommitPlan(summary=summary))


async def _listing_outputs(ctx, out_dir: Path, s: int, market: str, listing: Dict[str, Any],
                           d: Dict[str, Any], stories: List[Dict[str, Any]]) -> List[Any]:
    """Slides (with the listing's own photos), slideshow video and the text formats for
    one listing story. Same output ids as a news story, so approve schedules it the same way."""
    loop = asyncio.get_running_loop()
    photos = await fetch_listing_photos(listing, out_dir, s, SLIDE_COUNT)
    photos += [None] * (SLIDE_COUNT - len(photos))
    first_photo = next((p for p in photos if p), None)
    images = []
    for j, slide in enumerate(d["slides"], 1):
        name = f"story{s}_slide{j}.png"
        await loop.run_in_executor(None, render_listing_slide, str(out_dir / name), slide["headline"],
                                   slide["body"], j, SLIDE_COUNT, market, photos[j - 1] or first_photo,
                                   listing["attribution"])
        images.append({"file": name, "url": f"/api/agents/jobs/{ctx.job_id}/files/{name}",
                       "label": slide["headline"]})
    video = VIDEO_NAME.format(n=s)
    duration = await build_slideshow([str(out_dir / i["file"]) for i in images], str(out_dir / video))
    title = f"For sale: {listing['address']}"
    stories.append({"n": s, "kind": "listing", "title": title, "source": listing.get("mlsSource", ""),
                    "url": listing.get("listingUrl") or "", "why": "Your own active listing.",
                    "summary": "", "mls_id": listing.get("mlsId"), "attribution": listing["attribution"],
                    "facts": dict(listing_facts(listing)), "slides": d["slides"],
                    "images": [i["file"] for i in images], "video": video,
                    "photos_used": sum(1 for p in photos if p), "hero": first_photo})
    outs = [Output(id=f"s{s}_story", kind="json", label=f"Story {s} (your listing): {listing['address']}",
                   value=stories[-1]),
            Output(id=f"s{s}_video", kind="video", label=f"Story {s} listing slideshow (Reel)", file=video,
                   meta={"duration_s": round(duration, 1), "slides": len(images)}),
            Output(id=f"s{s}_slides", kind="images", label=f"Story {s} listing slides", value=images)]
    for k in FORMATS:
        outs.append(Output(id=f"s{s}_{k}", kind="text", label=f"Story {s} (listing) — {LABELS[k]}",
                           value=d[k], meta={"editable": True, "original": d[k], "story": s, "format": k}))
    return outs


# ── AI-twin reels (story_reel) ────────────────────────────────────────────────

REEL_DISCLOSURE = "🎥 Video made with my AI twin."
VO_REEL_DISCLOSURE = "🎙️ Voice-over made with my AI voice."


def _listing_chip(facts: Dict[str, str]) -> str:
    bits = [facts.get("List price", "")]
    if facts.get("Bedrooms"):
        bits.append(f"{facts['Bedrooms']} bd")
    if facts.get("Bathrooms"):
        bits.append(f"{facts['Bathrooms']} ba")
    return " · ".join(b for b in bits if b)


async def _story_panel(out_dir: Path, story: Dict[str, Any]) -> Path:
    """The top half of a story's reel: the article's headline screenshot (or a
    headline card), or for a listing its hero photo + MLS facts + attribution."""
    s = story["n"]
    panel = out_dir / f"story{s}_panel.png"
    loop = asyncio.get_running_loop()
    if story.get("kind") == "listing":
        hero = story.get("hero")
        await loop.run_in_executor(None, lambda: _reel.render_panel(
            str(panel), story.get("attribution") or "", shot=hero, headline=story.get("title", ""),
            source=story.get("source", ""), chip=_listing_chip(story.get("facts") or {})))
        return panel
    shot = out_dir / f"story{s}_shot.png"
    got = await _reel.capture_article(story.get("url") or "", story.get("title") or "", shot)
    src = story.get("source") or (urlparse(story.get("url") or "").hostname or "").removeprefix("www.") \
        or "local news"
    await loop.run_in_executor(None, lambda: _reel.render_panel(
        str(panel), f"Source: {src}. Commentary by {BRAND_NAME}.", shot=str(shot) if got else None,
        headline=story.get("title", ""), source=src))
    story["screenshot"] = bool(got)
    return panel


async def _add_reels(ctx, out_dir: Path, stories: List[Dict[str, Any]], outputs: List[Any]) -> int:
    """Swap each story's slideshow for an AI-twin reel where one renders. The
    slideshow stays as the video wherever a reel doesn't land. Returns reel count."""
    tts, twin, why, no_twin = _reel.providers(ctx)
    if why:
        ctx.warn(f"No reels this run — {why}. The stories keep the slideshow video.")
        return 0
    if no_twin:
        ctx.warn(f"No AI twin this run — {no_twin}. Reels go out as voice-over reels in your AI voice.")
    by_id = {o.id: o for o in outputs}
    jobs = []
    for st in stories:
        script = str(_get(by_id.get(f"s{st['n']}_script"), "value") or "")
        try:
            panel = await _story_panel(out_dir, st)
        except Exception as exc:
            ctx.warn(f"Story {st['n']}: no reel — couldn't build the story screenshot ({str(exc)[:120]}).")
            continue
        slides = [str(out_dir / f) for f in st.get("images") or []]
        jobs.append({"n": st["n"], "script": script, "panel": str(panel), "slides": slides})
    if not jobs:
        return 0
    made = await _reel.build_all(ctx, tts, twin, jobs)
    for st in stories:
        reel = made.get(st["n"])
        if not reel:
            continue
        mode = reel.get("mode") or "twin"
        st["slideshow"], st["video"], st["reel"], st["reel_mode"] = st.get("video"), reel["file"], True, mode
        vid = by_id.get(f"s{st['n']}_video")
        if vid is not None:
            vid.file = reel["file"]
            vid.label = (f"Story {st['n']} reel (your AI twin)" if mode == "twin"
                         else f"Story {st['n']} reel (voice-over, your AI voice)")
            vid.meta = {"duration_s": reel["duration_s"], "ai_generated": True, "captions": reel["captions"],
                        "screenshot": st.get("screenshot", st.get("kind") == "listing"),
                        "slideshow": st["slideshow"], "mode": mode}
        cap = by_id.get(f"s{st['n']}_caption")
        note = REEL_DISCLOSURE if mode == "twin" else VO_REEL_DISCLOSURE
        if cap is not None and note not in str(cap.value or ""):
            cap.value = f"{str(cap.value or '').rstrip()}\n\n{note}"
            cap.meta = {**(cap.meta or {}), "original": cap.value}
    return len(made)


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


# ── scheduled entry points ───────────────────────────────────────────────────

DAILY_STORIES = 1            # one fresh story per market per day
LISTING_WEEKDAY = 0          # the listing story rides along on Mondays only


async def _scheduled_run(base: Dict[str, Any], cadence: str,
                         markets: Optional[List[str]] = None) -> List[str]:
    """Start one Local Story Desk job per market and ping Telegram. Returns job ids."""
    from services.agents import jobs as _jobs
    started: List[Tuple[str, str]] = []
    lines: List[str] = []
    for market in markets or list(MARKETS):
        try:
            job = await _jobs.start("local_story", dict(base, market=market), initiator="cron")
            started.append((market, job["id"]))
        except Exception as exc:
            lines.append(f"• {market}: didn't start ({getattr(exc, 'args', [exc])[0]})")
    tasks = [t for t in (_jobs.task_for(jid) for _, jid in started) if t is not None]
    if tasks:
        await asyncio.wait(tasks, timeout=WEEKLY_WAIT_S)
    ready = 0
    heygen_out = False
    for market, jid in started:
        job = _jobs.get_job(jid)
        heygen_out = heygen_out or any("out of credits" in str(w).lower() for w in job.get("warnings") or [])
        count = sum(1 for o in job.get("outputs") or [] if str(_get(o, "id", "")).endswith("_story"))
        status = job.get("status")
        if count:
            ready += count
            lines.append(f"• {market}: {count} stor{'y' if count == 1 else 'ies'} ready")
        else:
            lines.append(f"• {market}: nothing ready ({status})")
    if heygen_out:
        lines.append("• HeyGen is out of credits — today's reels are voice-over (your AI voice). "
                     "Top up at https://app.heygen.com/settings/api to bring the twin back.")
    lines.append("• " + _reel.spend_line())
    await _notify(f"*Local Story Desk ({cadence})* — {ready} local stor{'y' if ready == 1 else 'ies'} drafted. "
                  "Nothing posts until you approve: http://127.0.0.1:8765/agents\n" + "\n".join(lines))
    return [jid for _, jid in started]


async def weekly_run(markets: Optional[List[str]] = None) -> List[str]:
    """The original Monday batch: 3 stories + a listing per market."""
    return await _scheduled_run({"stories": 3, "listings": True, "reels": True}, "weekly", markets)


async def daily_run(markets: Optional[List[str]] = None, today: Optional[date] = None) -> List[str]:
    """One fresh story per market every day; the listing joins on Mondays.

    Stories already drafted in the last USED_LOOKBACK_DAYS are skipped by run(), so
    a quiet news week doesn't repeat yesterday's post."""
    monday = (today or date.today()).weekday() == LISTING_WEEKDAY
    return await _scheduled_run({"stories": DAILY_STORIES, "listings": monday, "reels": True},
                                "daily", markets)


async def _notify(text: str) -> bool:
    try:
        from pipeline import telegram
        if not telegram.is_configured():
            return False
        return await telegram.send(text)
    except Exception:
        return False
