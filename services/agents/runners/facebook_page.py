"""
Facebook Page (`facebook_page`) — Facebook-native drafts from an idea, a Foundation
topic, a finished episode or a listing.

Build: Foundation gate first (get_brand_context, task facebook_post — contract #3),
then one draft per format: Page post (text + link), photo/carousel caption set,
Reel hook + caption, and — listing mode only — a listing-style post. Every draft
runs the deterministic guards (no invented numbers, no Fair Housing red flags);
one redraft is allowed, then the step fails rather than ship a bad post.
The build phase never publishes and never touches GHL or Meta.

Commit (on approval): one DRAFT Post per approved text, platform 'facebook',
source='auto_plan', platform_specific left empty (constraint 5). Nothing here
publishes: drafts reach Facebook only through the calendar's SocialService path
(contract #5), never direct Meta. Idempotent via commit.json, so a second approve
creates nothing new.
"""
import asyncio
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.agents.contract import AgentResult, CommitPlan, Output, StepError, foundation_tier
from services.agents.runners import avatar_video as _av

# Exact internal paths ctx.call_route may hit ("{param}" = one path segment).
ROUTES = ("/api/projects/{project_id}",)

PROVIDERS = ("foundation", "muse")
MODES = ("idea", "topic", "episode", "listing")
PLATFORM = "facebook"
DISCLOSURE = "Drafted with AI assistance."
COMMIT_DONE = "commit.json"
COMMIT_STARTED = "commit.started"
POST_HOUR_UTC = 15
FORMAT_IDS = ("page_post", "photo_caption", "reel", "listing_post")
LABELS = {"page_post": "Page post", "photo_caption": "Photo / carousel captions",
          "reel": "Reel hook + caption", "listing_post": "Listing post"}

_commit_locks: Dict[str, asyncio.Lock] = {}


# ── helpers ───────────────────────────────────────────────────────────────────

def _err(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        msg = data.get("error") or data.get("detail")
        if msg:
            return str(msg)
    return fallback


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(str(tmp), str(path))


async def _complete(system: str, user: str, max_tokens: int = 900) -> str:
    """The default ('foundation') model call; patched in tests."""
    return await _av._complete(system, user, max_tokens)


async def _complete_muse(system: str, user: str, pii: bool) -> str:
    """Meta Muse text via services/media/muse.py. pii=True keeps lead data off the
    contributor tier (the adapter refuses it); patched in tests."""
    from services.media.muse import MuseProvider
    try:
        return await MuseProvider().complete(user, system=system, pii=pii)
    except Exception as exc:
        raise StepError(getattr(exc, "user_message", None) or f"Muse couldn't draft that: {exc}")


async def _generate(provider: str, pii: bool, system: str, user: str) -> str:
    if provider == "muse":
        return await _complete_muse(system, user, pii)
    return await _complete(system, user)


def _clean(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def _truthy(v: Any) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def _disclose(inp: Dict[str, Any]) -> bool:
    """Muse's acceptable-use terms: AI output is never passed off as human-written."""
    return _truthy(inp.get("ai_disclosure")) or str(inp.get("provider") or "") == "muse"


def with_disclosure(text: str) -> str:
    text = (text or "").rstrip()
    return text if DISCLOSURE in text else f"{text}\n\n{DISCLOSURE}"


def _listing_facts(inp: Dict[str, Any]) -> List[str]:
    facts = []
    for key, label in (("address", "Address"), ("price", "Price"), ("beds", "Beds"),
                       ("baths", "Baths"), ("sqft", "Square feet"), ("highlights", "Highlights")):
        v = str(inp.get(key) if inp.get(key) is not None else "").strip()
        if v:
            facts.append(f"{label}: {v}")
    return facts


FORMAT_BRIEFS = {
    "page_post": ("A Facebook Page post: 2-4 short paragraphs, conversational, one clear takeaway, "
                  "ends with a question or invitation. No hashtags spam (max 2)."),
    "photo_caption": ("A photo/carousel caption set. First line: the main caption (1-3 sentences). "
                      "Then three lines starting 'Slide 1:', 'Slide 2:', 'Slide 3:' with a short "
                      "caption for each photo."),
    "reel": ("A Facebook Reel. First line starts 'Hook: ' and is a scroll-stopping opener under 12 words. "
             "Then a blank line and the caption (1-3 sentences)."),
    "listing_post": ("A listing-style Facebook post about this home. Describe the PROPERTY only "
                     "(features, layout, condition, location facts the user gave). Never describe "
                     "who should or would want to live there."),
}

GUARD_RULES = [
    "Plain text only. Never mention being an AI.",
    _av.NO_FABRICATION_RULE,
    "Fair Housing: never reference family status, age, religion, race, ethnicity, disability, "
    "or the safety/crime level of an area, and never say who a home is 'perfect for'.",
    "Never make a claim (appreciation, guaranteed outcomes, awards, rankings) the user did not give you.",
]


async def _draft(ctx, bc: Any, fmt: str, subject: str, source_text: List[str],
                 link: str, provider: str = "foundation", pii: bool = False) -> str:
    """One format, guarded. The Foundation voice block comes from get_brand_context."""
    system = ("You write Facebook Page content in my voice.\n" + _av._voice_block(bc) +
              "\n\nRules:\n- " + "\n- ".join(GUARD_RULES) +
              f"\n\nFormat: {FORMAT_BRIEFS[fmt]}")
    sources = list(source_text) + [link]
    problems: List[str] = []
    for _attempt in range(2):
        user = subject
        if problems:
            user += ("\n\nYour last draft broke the rules: " + "; ".join(problems) +
                     ". Rewrite it without those.")
        text = _clean(await _generate(provider, pii, system, user))
        problems = []
        if not text:
            problems.append("it was empty")
        bad = _av.fabricated_numbers(text, sources)
        if bad:
            problems.append("it used numbers I never gave you (" + ", ".join(bad) + ")")
        flags = _av.fair_housing_flags(text)
        if flags:
            problems.append("it used Fair Housing red-flag language (" + ", ".join(flags) + ")")
        if not problems:
            if fmt == "page_post" and link and link not in text:
                text = f"{text}\n\n{link}"
            return text
    raise StepError("Couldn't get a clean " + LABELS[fmt].lower() + " — the draft kept " +
                    problems[0] + ". Adjust the inputs and run it again.")


# ── build ─────────────────────────────────────────────────────────────────────

async def run(ctx, inp: Dict[str, Any]) -> AgentResult:
    provider = str(inp.get("provider") or "foundation")
    if provider not in PROVIDERS:
        raise StepError("Provider has to be foundation or muse.")
    pii = _truthy(inp.get("contains_lead_data"))
    mode = str(inp.get("mode") or "idea")
    if mode not in MODES:
        raise StepError("Mode has to be idea, topic, episode, or listing.")
    link = str(inp.get("link") or "").strip()
    topic = str(inp.get("topic") or "").strip()
    sources: List[str] = [topic]
    formats = ["page_post", "photo_caption", "reel"]
    subject = ""

    if mode == "episode":
        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            raise StepError("Pick the episode to write from.")
        async with ctx.step("episode", "Reading the episode"):
            status, project = await ctx.call_route(f"/api/projects/{pid}", None, method="GET")
            if status != 200 or not isinstance(project, dict):
                raise StepError(_err(project, "Can't find that episode on the Job Site."))
            title = str(project.get("title") or "")
            notes = str(project.get("show_notes") or "").strip()[:1500]
            sources += [title, notes]
            topic = topic or title
            subject = f"Promote this finished episode.\nTitle: {title}\nShow notes: {notes}"
    elif mode == "listing":
        facts = _listing_facts(inp)
        if not facts:
            raise StepError("Give me the listing facts (address, price, highlights) to write from.")
        sources += facts
        formats = ["listing_post", "photo_caption", "reel"]
        subject = "Write about this listing using ONLY these facts:\n" + "\n".join(facts)
        if topic:
            subject += f"\nAngle: {topic}"
    else:
        if not topic:
            raise StepError("Give me a topic to write about.")
        kind = "Foundation topic" if mode == "topic" else "idea"
        subject = f"Write about this {kind}: {topic}"
    if link:
        subject += f"\nLink to include for the Page post: {link}"

    async with ctx.step("foundation", "Checking the Foundation"):
        try:
            from schemas.foundation import BrandContextTaskType
            task_type: Any = BrandContextTaskType.facebook_post
        except Exception:  # pragma: no cover
            task_type = "facebook_post"
        try:
            bc = await ctx.brand_context(task_type, topic=topic or None)
        except StepError:
            raise
        except Exception as exc:
            raise StepError("Your Foundation isn't ready, so I won't draft in a voice that isn't yours. "
                            f"({exc})")
        count = getattr(getattr(bc, "metadata", None), "sample_count", None)
        if isinstance(count, int) and foundation_tier(count) == "thin":
            ctx.warn(f"Thin Foundation ({count} samples) — drafts will sound less like you.")

    disclose = _disclose(inp)
    outputs: List[Any] = []
    async with ctx.step("draft", "Drafting the Facebook posts"):
        for fmt in formats:
            ctx.check_cancelled()
            text = await _draft(ctx, bc, fmt, subject, sources, link, provider, pii)
            if disclose:
                text = with_disclosure(text)
            out = Output(id=fmt, kind="text", label=LABELS[fmt], value=text,
                         meta={"editable": True, "original": text, "platform": PLATFORM,
                               "format": fmt, "ai_disclosure": disclose})
            ctx.add_output(out)
            outputs.append(out)

    return AgentResult(outputs=outputs,
                       commit=CommitPlan(summary=f"Add {len(outputs)} Facebook drafts to the calendar"))


# ── commit ────────────────────────────────────────────────────────────────────

def _session_factory():
    from db.engine import async_session
    return async_session


async def _create_posts(location_id: str, texts: List[str]) -> List[str]:
    from db.models import Post, PostVariant
    first = date.today() + timedelta(days=1)
    loc = uuid.UUID(str(location_id))
    async with _session_factory()() as session:
        posts = []
        for i, text in enumerate(texts):
            day = first + timedelta(days=i)       # one a day so drafts never clump
            at = datetime(day.year, day.month, day.day, POST_HOUR_UTC, 0, 0, tzinfo=timezone.utc)
            post = Post(location_id=loc, base_caption=text, scheduled_at=at,
                        status="draft", source="auto_plan")
            session.add(post)
            posts.append((post, text))
        await session.flush()
        for post, text in posts:
            session.add(PostVariant(post_id=post.id, platform=PLATFORM, caption=text,
                                    platform_specific={}))
        await session.flush()
        ids = [str(p.id) for p, _ in posts]
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
            raise StepError("A previous commit was interrupted partway — check /calendar before "
                            "running this again so nothing lands twice.")

        disclose = _disclose(job.get("input") or {})
        texts: List[str] = []
        for o in job.get("outputs") or []:
            if _get(o, "id") in FORMAT_IDS and _get(o, "kind") == "text":
                text = str(_get(o, "value") or "").strip()
                if text:
                    texts.append(with_disclosure(text) if disclose else text)
        if not texts:
            raise StepError("Nothing to put on the calendar — every draft is empty.")

        started.write_text("{}")
        try:
            ids = await _create_posts(ctx.location_id, texts)
        except Exception:
            started.unlink()          # the transaction never committed — a retry is safe
            raise
        result: Dict[str, Any] = {"post_ids": ids, "posts_created": len(ids), "platform": PLATFORM,
                                  "calendar_url": "/calendar",
                                  "summary": f"{len(ids)} Facebook drafts on the calendar"}
        _atomic_write_json(done, result)
        return result
