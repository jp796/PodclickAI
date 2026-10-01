"""
Draftsman — platform-ready social posts in your voice from an idea, an episode, or a template.

AGENTS_HUB_SPEC §1.4. Wraps POST /api/social/forge, which already runs
assert_foundation_ready + get_brand_context (contract #3 holds in the route).

Commit ("Add to Calendar as draft"): one Post(status='draft', source='post_forge')
plus one PostVariant per platform carrying the EDITED text, in one transaction.
platform_specific stays empty (constraint 5). Then each approved text is poured
into the Foundation through POST /api/foundation/ingest (SOW §10.1):
  - unchanged or lightly touched  -> source='social_approved'
  - rewritten (edit distance > 0.30) -> source='social_edited' with edit_distance
Idempotent via commit.json in the job's output dir, so a second approve neither
duplicates the post nor re-ingests the samples.
"""
import asyncio
import difflib
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.agents.contract import AgentResult, CommitPlan, Output, StepError

# Exact internal paths ctx.call_route may hit ("{param}" = one path segment).
ROUTES = (
    "/api/social/forge",
    "/api/foundation/ingest",
    "/api/projects/{project_id}",
)

PLATFORMS = (("linkedin", "LinkedIn"), ("facebook", "Facebook"), ("instagram", "Instagram"),
             ("x", "X"), ("tiktok", "TikTok"))
MODES = ("idea", "episode", "template")
TEMPLATES = ("Just Listed", "Market Update", "Client Win", "Hot Take", "Tip of the Week")
EDITED_THRESHOLD = 0.30
COMMIT_DONE = "commit.json"
COMMIT_STARTED = "commit.started"
POST_HOUR_UTC = 15

_commit_locks: Dict[str, asyncio.Lock] = {}


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


def _emit(ctx, outputs: List[Any], output: Any) -> None:
    ctx.add_output(output)
    outputs.append(output)


def edit_distance(original: str, final: str) -> float:
    """0.0 = identical, 1.0 = nothing in common (normalized, difflib ratio)."""
    if original == final:
        return 0.0
    return round(1.0 - difflib.SequenceMatcher(None, original or "", final or "").ratio(), 3)


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(str(tmp), str(path))


async def run(ctx, inp: Dict[str, Any]) -> AgentResult:
    mode = str(inp.get("mode") or "idea")
    if mode not in MODES:
        raise StepError("Mode has to be idea, episode, or template.")
    body: Dict[str, Any] = {"mode": mode, "topic": str(inp.get("topic") or "").strip(),
                            "market": str(inp.get("market") or "").strip()}

    if mode == "episode":
        pid = str(inp.get("project_id") or "").strip()
        if not pid:
            raise StepError("Pick the episode to write from.")
        async with ctx.step("episode", "Reading the episode"):
            status, project = await ctx.call_route(f"/api/projects/{pid}", None, method="GET")
            if status != 200 or not isinstance(project, dict):
                raise StepError(_err(project, "Can't find that episode on the Job Site."))
            body["title"] = project.get("title") or ""
            notes = (project.get("show_notes") or "").strip()
            body["hook_line"] = notes.splitlines()[0][:300] if notes else ""
            body["topic"] = body["topic"] or body["title"]
    elif mode == "template":
        template = str(inp.get("template") or "")
        if template not in TEMPLATES:
            raise StepError("Pick one of the templates.")
        body["template"] = template
    if not body["topic"] and mode == "idea":
        raise StepError("Give me a topic to write about.")

    outputs: List[Any] = []
    async with ctx.step("draft", "Drafting the posts"):
        status, data = await ctx.call_route("/api/social/forge", body)
        if status != 200 or not isinstance(data, dict):
            raise StepError(_err(data, "Post Forge didn't answer."))
        drafted = {k: str(data.get(k) or "").strip() for k, _ in PLATFORMS}
        if not any(drafted.values()):
            raise StepError("The draft came back empty — run it again.")

    if data.get("_foundation_thin"):
        ctx.warn(f"Thin Foundation ({data.get('_sample_count', 0)} samples) — output will sound less like you.")

    for key, label in PLATFORMS:
        if drafted[key]:
            _emit(ctx, outputs, Output(id=key, kind="text", label=label, value=drafted[key],
                                       meta={"editable": True, "original": drafted[key], "platform": key}))
    return AgentResult(outputs=outputs,
                       commit=CommitPlan(summary="Add these posts to the calendar as one draft"))


def _session_factory():
    from db.engine import async_session
    return async_session


async def _create_post(location_id: str, texts: Dict[str, str]) -> Dict[str, Any]:
    from db.models import Post, PostVariant
    tomorrow = date.today() + timedelta(days=1)
    at = datetime(tomorrow.year, tomorrow.month, tomorrow.day, POST_HOUR_UTC, 0, 0, tzinfo=timezone.utc)
    base = next((texts[k] for k, _ in PLATFORMS if texts.get(k)), "")
    async with _session_factory()() as session:
        post = Post(location_id=uuid.UUID(str(location_id)), base_caption=base, scheduled_at=at,
                    status="draft", source="post_forge")
        session.add(post)
        await session.flush()
        for key, text in texts.items():
            session.add(PostVariant(post_id=post.id, platform=key, caption=text, platform_specific={}))
        await session.flush()
        post_id = str(post.id)
        await session.commit()
    return {"post_id": post_id, "scheduled_at": at.isoformat()}


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

        finals: Dict[str, str] = {}
        originals: Dict[str, str] = {}
        valid = {k for k, _ in PLATFORMS}
        for o in job.get("outputs") or []:
            oid = _get(o, "id")
            if oid in valid and _get(o, "kind") == "text":
                text = str(_get(o, "value") or "").strip()
                if text:
                    finals[oid] = text
                    originals[oid] = str((_get(o, "meta") or {}).get("original") or text)
        if not finals:
            raise StepError("Nothing to put on the calendar — every post is empty.")

        started.write_text("{}")
        try:
            created = await _create_post(ctx.location_id, finals)
        except Exception:
            started.unlink()
            raise
        result: Dict[str, Any] = dict(created, platforms=sorted(finals), calendar_url="/calendar",
                                      summary="1 draft on the calendar", foundation={})
        _atomic_write_json(done, result)

        poured: Dict[str, str] = {}
        for key, text in finals.items():
            dist = edit_distance(originals[key], text)
            sample = {"text": text, "platform": key, "topic": str((job.get("input") or {}).get("topic") or "")}
            if dist > EDITED_THRESHOLD:
                sample.update(source="social_edited", edit_distance=dist)
            else:
                sample["source"] = "social_approved"
            try:
                status, data = await ctx.call_route("/api/foundation/ingest", sample)
                poured[key] = sample["source"] if status == 200 else f"not poured: {_err(data, status)}"
            except Exception as exc:  # the post exists; the Foundation pour is best-effort
                poured[key] = f"not poured: {exc}"
        if any(v.startswith("not poured") for v in poured.values()):
            ctx.warn("Draft's on the calendar, but some posts didn't make it into the Foundation.")
        result["foundation"] = poured
        _atomic_write_json(done, result)
        return result
