"""
Content Scheduler — place topics onto real dates on the calendar and shoot schedule.

AGENTS_HUB_SPEC §1.3.

Build: a deterministic slot plan (no LLM, no Foundation). Topics come from a
finished Trend Radar / Pillar Planner job or a pasted list. Buckets come from
services.vyral.distribute_buckets over the Blueprint's vyral_mix, so the Vyral
mix and anti-clumping match auto-plan.

Commit (only after a human approves, or Brick at commit_tier):
  1. Post(status='draft', source='auto_plan', bucket, scheduled_at, base_caption)
     rows in ONE transaction. Deviation from the spec text: base content lives on
     Post.base_caption — migration a1f3c8d2e094 removed 'base' from the
     post_variants platform check, so PostVariant(platform='base') would 500.
     No per-platform variants are created: /variants/generate skips platforms
     that already exist, so pre-filling them would block Foundation captions.
  2. Topics appended to data/scheduler.json in the existing {title, pillar,
     market, notes} shape (atomic tmp + os.replace), deduped by title.
  3. Idempotent: commit.json in the job's output dir holds the result; a second
     approve returns it. A commit.started marker without commit.json means a
     previous commit died partway — refuse rather than double-write.

The job id and post ids are recorded only in the job's outputs/result — never in
PostVariant.platform_specific (constraint 5: those keys leak into the GHL body).
"""
import asyncio
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.agents.contract import AgentResult, CommitPlan, Output, StepError

# Exact internal paths ctx.call_route may hit ("{param}" = one path segment).
ROUTES = (
    "/api/agents/jobs/{job_id}",
)

SCHEDULER_FILE = Path(__file__).resolve().parents[3] / "data" / "scheduler.json"
COMMIT_DONE = "commit.json"
COMMIT_STARTED = "commit.started"
MAX_TOPICS = 60
POST_HOUR_UTC = 15            # 10am Chicago (CDT), same slot auto-plan uses
POST_TIME_LABEL = "10:00 AM CT"

CADENCE_PER_WEEK = {"3/week": 3, "5/week": 5, "daily": 7}
DEFAULT_WEEKDAYS = {3: (0, 2, 4), 5: (0, 1, 2, 3, 4), 7: (0, 1, 2, 3, 4, 5, 6)}
DEFAULT_VYRAL_MIX = {"viral": 0.4, "brand": 0.3, "personal": 0.2, "conversion": 0.1}
DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
ALLOWED_PLATFORMS = ("linkedin", "facebook", "instagram", "tiktok", "youtube", "x")

_commit_locks: Dict[str, asyncio.Lock] = {}


# ── small shared helpers ─────────────────────────────────────────────────────

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _find_output(job: Dict[str, Any], oid: str) -> Optional[Any]:
    for o in job.get("outputs") or []:
        if _get(o, "id") == oid:
            return o
    return None


def _emit(ctx, outputs: List[Any], output: Any) -> None:
    ctx.add_output(output)
    outputs.append(output)


async def _load_ref_job(ctx, job_id: str, agents: Iterable[str]) -> Dict[str, Any]:
    status, job = await ctx.call_route(f"/api/agents/jobs/{job_id}", None, method="GET")
    if status != 200 or not isinstance(job, dict):
        raise StepError("Couldn't find that work order.")
    if job.get("agent_id") not in tuple(agents):
        raise StepError("That work order came from the wrong crew member.")
    if job.get("status") != "done":
        raise StepError("That work order isn't built yet — wait for it to finish.")
    return job


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(str(tmp), str(path))


def _read_scheduler() -> Dict[str, Any]:
    try:
        data = json.loads(Path(SCHEDULER_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


# ── deterministic planning (pure) ────────────────────────────────────────────

def normalize_days(days: Any) -> List[int]:
    out: List[int] = []
    for d in days or []:
        idx = DAY_INDEX.get(str(d).strip().lower()[:3])
        if idx is not None and idx not in out:
            out.append(idx)
    return sorted(out)


def plan_dates(start: date, cadence: str, shoot_days: List[int], n: int) -> List[date]:
    """Walk forward from start, taking allowed weekdays up to the cadence per ISO week."""
    per_week = CADENCE_PER_WEEK.get(cadence, 3)
    allowed = set(shoot_days) if shoot_days else set(DEFAULT_WEEKDAYS[per_week])
    out: List[date] = []
    counts: Dict[Any, int] = {}
    d = start
    for _ in range(366 * 3):
        if len(out) >= n:
            break
        wk = d.isocalendar()[:2]
        if d.weekday() in allowed and counts.get(wk, 0) < per_week:
            out.append(d)
            counts[wk] = counts.get(wk, 0) + 1
        d += timedelta(days=1)
    return out


def _round_robin_ideas(cards: List[Any]) -> List[Dict[str, str]]:
    queues = [[(str(_get(c, "pillar", "") or ""), i) for i in (_get(c, "ideas") or [])] for c in cards]
    topics: List[Dict[str, str]] = []
    while any(queues):
        for q in queues:
            if q:
                pillar, idea = q.pop(0)
                title = str(_get(idea, "title", idea) or "").strip()
                if title:
                    topics.append({"title": title, "pillar": pillar,
                                   "hook": str(_get(idea, "hook", "") or "")})
    return topics


async def _collect_topics(ctx, inp: Dict[str, Any]) -> Tuple[List[Dict[str, str]], str]:
    ref = str(inp.get("topics_job") or "").strip()
    market = ""
    if ref:
        job = await _load_ref_job(ctx, ref, ("trend_radar", "pillar_planner"))
        job_input = job.get("input") or {}
        market = str(job_input.get("city") or job_input.get("market") or "")
        if job.get("agent_id") == "trend_radar":
            out = _find_output(job, "topics")
            topics = [{"title": str(_get(t, "title", "") or "").strip(),
                       "pillar": str(_get(t, "pillar", "") or ""),
                       "hook": str(_get(t, "hook", "") or "")}
                      for t in (_get(out, "value") or [])] if out is not None else []
        else:
            out = _find_output(job, "pillar_plan")
            topics = _round_robin_ideas(_get(out, "value") or []) if out is not None else []
    else:
        lines = str(inp.get("topics") or "").splitlines()
        topics = [{"title": ln.strip(), "pillar": "", "hook": ""} for ln in lines if ln.strip()]
    topics = [t for t in topics if t["title"]][:MAX_TOPICS]
    if not topics:
        raise StepError("No topics to schedule — pick a Trend Radar or Pillar Planner work order, "
                           "or paste one topic per line.")
    return topics, market


async def _load_vyral_mix(location_id: str) -> Dict[str, Any]:
    from sqlalchemy import text as _sql_text
    from db.engine import async_session
    async with async_session() as session:
        row = (await session.execute(
            _sql_text("SELECT vyral_mix FROM blueprints WHERE location_id = :loc"),
            {"loc": location_id},
        )).mappings().first()
    mix = (row or {}).get("vyral_mix") if row is not None else None
    return mix or dict(DEFAULT_VYRAL_MIX)


def _parse_start(raw: Any) -> date:
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            raise StepError("That start date doesn't read as YYYY-MM-DD.")
    return date.today() + timedelta(days=1)


def _fmt(d: date) -> str:
    return d.strftime("%b ") + str(d.day)


# ── run ──────────────────────────────────────────────────────────────────────

async def run(ctx, inp: Dict[str, Any]) -> AgentResult:
    from services.vyral import distribute_buckets

    outputs: List[Any] = []
    async with ctx.step("topics", "Gathering topics"):
        topics, market = await _collect_topics(ctx, inp)

    async with ctx.step("plan", "Placing topics on the calendar"):
        sched = _read_scheduler()
        start = _parse_start(inp.get("start_date"))
        cadence = str(inp.get("cadence") or "3/week")
        if cadence not in CADENCE_PER_WEEK:
            raise StepError("Cadence has to be 3/week, 5/week, or daily.")
        shoot_days = normalize_days(inp.get("shoot_days") or sched.get("shoot_days"))
        platforms = [p for p in (inp.get("platforms") or []) if p in ALLOWED_PLATFORMS]
        dates = plan_dates(start, cadence, shoot_days, len(topics))
        if len(dates) < len(topics):
            raise StepError("Couldn't find enough open days with those shoot days.")
        buckets = distribute_buckets(await _load_vyral_mix(ctx.location_id), len(topics))
        market = market or str(sched.get("market") or "")
        rows = []
        for t, d, b in zip(topics, dates, buckets):
            at = datetime(d.year, d.month, d.day, POST_HOUR_UTC, 0, 0, tzinfo=timezone.utc)
            rows.append({"date": d.isoformat(), "time": POST_TIME_LABEL, "scheduled_at": at.isoformat(),
                         "topic": t["title"], "pillar": t["pillar"], "hook": t["hook"],
                         "bucket": b, "platforms": platforms, "market": market})

    _emit(ctx, outputs, Output(id="slot_plan", kind="table", label="Slot plan", schema="slot_plan", value=rows))
    summary = (f"Put {len(rows)} topics on the calendar from "
               f"{_fmt(dates[0])} – {_fmt(dates[-1])}" if len(rows) > 1
               else f"Put 1 topic on the calendar for {_fmt(dates[0])}")
    return AgentResult(outputs=outputs, commit=CommitPlan(summary=summary))


# ── commit ───────────────────────────────────────────────────────────────────

def _session_factory():
    from db.engine import async_session
    return async_session


async def _create_posts(location_id: str, rows: List[Dict[str, Any]]) -> List[str]:
    from db.models import Post
    loc = uuid.UUID(str(location_id))
    ids: List[str] = []
    async with _session_factory()() as session:
        posts = []
        for r in rows:
            caption = r["topic"] + (f"\n\n{r['hook']}" if r.get("hook") else "")
            p = Post(location_id=loc, bucket=r["bucket"], base_caption=caption,
                     scheduled_at=datetime.fromisoformat(r["scheduled_at"]),
                     status="draft", source="auto_plan")
            session.add(p)
            posts.append(p)
        await session.flush()
        ids = [str(p.id) for p in posts]
        await session.commit()
    return ids


def _append_topics(rows: List[Dict[str, Any]]) -> int:
    sched = _read_scheduler()
    queue = list(sched.get("topics") or [])
    have = {str(t.get("title", "")).strip().lower() for t in queue if isinstance(t, dict)}
    added = 0
    for r in rows:
        key = r["topic"].strip().lower()
        if key in have:
            continue
        queue.append({"title": r["topic"], "pillar": r.get("pillar", ""),
                      "market": r.get("market", "") or sched.get("market", ""),
                      "notes": r.get("hook", "")})
        have.add(key)
        added += 1
    if added:
        sched["topics"] = queue
        _atomic_write_json(Path(SCHEDULER_FILE), sched)
    return added


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
        plan = _find_output(job, "slot_plan")
        rows = list(_get(plan, "value") or []) if plan is not None else []
        if not rows:
            raise StepError("Nothing on the slot plan to commit.")

        started.write_text(json.dumps({"job_id": job_id, "at": datetime.now(timezone.utc).isoformat()}))
        try:
            post_ids = await _create_posts(ctx.location_id, rows)
        except Exception:
            started.unlink()          # the transaction never committed — a retry is safe
            raise

        result: Dict[str, Any] = {
            "post_ids": post_ids, "posts_created": len(post_ids), "topics_queued": 0,
            "calendar_url": "/calendar",
            "summary": f"{len(post_ids)} drafts on the calendar",
        }
        _atomic_write_json(done, result)   # posts exist from here on — record it before anything else
        try:
            result["topics_queued"] = _append_topics(rows)
        except Exception as exc:
            result["topics_error"] = f"Drafts are on the calendar, but the Studio topic queue didn't update: {exc}"
            ctx.warn(result["topics_error"])
        _atomic_write_json(done, result)
        return result
