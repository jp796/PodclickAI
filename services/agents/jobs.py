"""The Crew job store (AGENTS_HUB_SPEC §2.2–2.3).

In-memory `AGENT_JOBS` plus an atomic JSON mirror under `data/agent_jobs/`
(tmp + os.replace on every transition) — the podcast_autopilot durability pattern,
no DB migration. Runs start through `task_guard.spawn` so no job can vanish
silently. Commits go through ONE path: a BrickAction(`agent_commit`) whose
dispatch calls `commit_job`, so approving from the Walk-through punch list and
approving from /agents hit the same code and the same brick_track_record.

Collaborators that need the database or the Brick service (`foundation_summary`,
`current_tier`, `_create_commit_action`, `_brick_approve`, `_brick_reject`,
`requirement_met`) are module-level functions so tests can replace them; all of
them import lazily, which keeps this module importable from brick_agent's
dispatch without a cycle.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.agents.contract import (
    EDITABLE_KINDS,
    TERMINAL_STATUSES,
    AgentContext,
    AgentSpec,
    JobCancelled,
    Output,
    StepError,
    foundation_tier,
    validate_input,
)
from services.agents.registry import REGISTRY, get_agent

logger = logging.getLogger(__name__)

# ── storage locations (tests repoint DATA_DIR at a tmp dir) ──────────────────

DATA_DIR: Path = Path(__file__).resolve().parent.parent.parent / "data"


def jobs_dir() -> Path:
    return DATA_DIR / "agent_jobs"


def outputs_dir(job_id: str) -> Path:
    return DATA_DIR / "agent_outputs" / job_id


def uploads_dir() -> Path:
    return DATA_DIR / "agent_uploads"


AGENT_JOBS: Dict[str, Dict[str, Any]] = {}
_loaded = False
_commit_locks: Dict[str, asyncio.Lock] = {}
_tasks: Dict[str, "asyncio.Task[Any]"] = {}
_sem_holder: Dict[str, Any] = {"loop": None, "sem": None}

MAX_CONCURRENT_JOBS = 4
INTERRUPTED_MSG = "Interrupted by a restart — run it again."
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


# ── errors (each carries its HTTP status and the §2.4 response shape) ─────────

class AgentError(Exception):
    status_code = 400

    def __init__(self, error: str, **extra: Any) -> None:
        super().__init__(error)
        self.message = error
        self.extra = extra

    def payload(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"error": self.message}
        d.update(self.extra)
        return d


class UnknownAgent(AgentError):
    status_code = 404


class InvalidInput(AgentError):
    status_code = 400


class NeedsSetup(AgentError):
    status_code = 409


class FoundationNotReady(AgentError):
    status_code = 422


class NotBuilt(AgentError):
    status_code = 423

    def __init__(self, detail: str = "This crew member isn't on site yet.") -> None:
        super().__init__("not_built", message=detail)


class JobNotFound(AgentError):
    status_code = 404


class JobConflict(AgentError):
    status_code = 409


# ── helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def agents_disabled() -> bool:
    """Kill switch (§4.5 PODCLICK_AGENTS_DISABLED) — settings first, env fallback."""
    val = ""
    try:
        from config import settings
        val = str(getattr(settings, "podclick_agents_disabled", "") or "")
    except Exception:
        val = ""
    if not val:
        val = os.getenv("PODCLICK_AGENTS_DISABLED", "")
    return val.strip().lower() in ("1", "true", "yes", "on")


def load_runner(spec: AgentSpec) -> Optional[ModuleType]:
    """
    Import the spec's runner. None means `not_built`: the file is missing, fails to
    import, lacks `run`, or (for a committing agent) lacks `commit`. Lanes merge in
    any order, so a missing runner is a normal state, not an error.
    """
    if agents_disabled():
        return None
    try:
        module = importlib.import_module(spec.runner)
    except ModuleNotFoundError as exc:
        if exc.name == spec.runner or (exc.name and spec.runner.startswith(exc.name)):
            logger.debug("[agents] runner %s not on site yet", spec.runner)
        else:
            logger.warning("[agents] runner %s failed to import: %s", spec.runner, exc)
        return None
    except Exception as exc:
        logger.warning("[agents] runner %s failed to import: %s", spec.runner, exc)
        return None
    if not callable(getattr(module, "run", None)):
        logger.warning("[agents] runner %s has no run()", spec.runner)
        return None
    if spec.commit_tier and not callable(getattr(module, "commit", None)):
        logger.warning("[agents] runner %s commits but has no commit()", spec.runner)
        return None
    return module


_REQ_SETTINGS: Dict[str, Tuple[str, str]] = {
    "youtube_api": ("youtube_data_api_key", "YOUTUBE_DATA_API_KEY"),
    "pexels": ("pexels_api_key", "PEXELS_API_KEY"),
    "openai": ("openai_api_key", "OPENAI_API_KEY"),
    "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
    "ghl": ("ghl_token", "GHL_TOKEN"),
    "elevenlabs": ("elevenlabs_api_key", "ELEVENLABS_API_KEY"),
    "higgsfield": ("higgsfield_api_key", "HIGGSFIELD_API_KEY"),
}
_REQ_CAPABILITY: Dict[str, str] = {"elevenlabs": "tts", "higgsfield": "avatar_video"}


def requirement_met(req: str) -> bool:
    """
    Cheap, no-network check. Keys come from config.settings first; os.getenv is a
    fallback only (constraint 6 — .env is not exported to the process). For media
    providers, when lane D's registry is on site, the capability must actually be
    offered (a Higgsfield adapter with no public API declares no capabilities).
    """
    field_name, env_name = _REQ_SETTINGS.get(req, ("", ""))
    if not field_name:
        return False
    value = ""
    try:
        from config import settings
        value = str(getattr(settings, field_name, "") or "")
    except Exception:
        value = ""
    if not value:
        value = os.getenv(env_name, "")
    if not value.strip():
        return False
    cap = _REQ_CAPABILITY.get(req)
    if cap:
        try:
            from services.media.registry import get_provider  # lane D
        except Exception:
            return True
        try:
            return get_provider(cap) is not None
        except Exception:
            return False
    return True


def missing_requirements(spec: AgentSpec) -> List[str]:
    return [r for r in spec.requires if not requirement_met(r)]


def agent_state(spec: AgentSpec) -> Tuple[str, List[str]]:
    """('ready'|'needs_setup'|'not_built', missing)."""
    if load_runner(spec) is None:
        return "not_built", []
    missing = missing_requirements(spec)
    if missing:
        return "needs_setup", missing
    return "ready", []


# ── collaborators (replaced in tests) ─────────────────────────────────────────

async def foundation_summary(location_id: str) -> Dict[str, Any]:
    try:
        from db.engine import async_session
        from services.foundation import get_foundation_status

        async with async_session() as session:
            status = await get_foundation_status(session, location_id)
        count = int(status.sample_count)
        return {"tier": foundation_tier(count), "sample_count": count,
                "has_blueprint": bool(status.has_blueprint)}
    except Exception as exc:
        logger.warning("[agents] foundation status unavailable: %s", exc)
        return {"tier": "unknown", "sample_count": None, "has_blueprint": None}


async def current_tier(location_id: str) -> Optional[str]:
    try:
        from db.engine import async_session
        from services.brick_agent import BrickAgent

        async with async_session() as session:
            permit = await BrickAgent()._get_or_create_permit(session, location_id)
            tier = permit.current_tier
            await session.commit()
            return tier
    except Exception as exc:
        logger.warning("[agents] permit lookup failed: %s", exc)
        return None


async def _create_commit_action(job: Dict[str, Any], spec: AgentSpec, summary: str) -> str:
    from db.engine import async_session
    from db.models import BrickAction

    async with async_session() as session:
        action = BrickAction(
            location_id=uuid.UUID(job["location_id"]),
            action_type="agent_commit",
            status="pending",
            payload={"job_id": job["id"], "agent_id": spec.id, "summary": summary},
            rationale=summary,
            actor_type="brick" if job.get("initiator") == "brick" else "user",
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        session.add(action)
        await session.flush()
        action_id = str(action.id)
        await session.commit()
        return action_id


async def _brick_approve(action_id: str) -> Dict[str, Any]:
    from config import get_current_location_id
    from services.brick_agent import BrickAgent

    # Single-tenant sentinel reviewer, same as POST /api/brick/actions/{id}/approve.
    return await BrickAgent().approve_action(action_id, get_current_location_id())


async def _brick_reject(action_id: str, reason: Optional[str]) -> Dict[str, Any]:
    from config import get_current_location_id
    from services.brick_agent import BrickAgent

    return await BrickAgent().reject_action(action_id, get_current_location_id(), reason)


def _tier_allows(current: str, required: str) -> bool:
    from services.brick_agent import _tier_allows as _allows

    return _allows(current, required)


async def _load_brand_context(location_id: str, task_type: Any, topic: Optional[str]) -> Any:
    from db.engine import async_session
    from schemas.foundation import BrandContextTaskType
    from services.foundation import get_brand_context

    tt = task_type if isinstance(task_type, BrandContextTaskType) else BrandContextTaskType(task_type)
    async with async_session() as session:
        return await get_brand_context(session, location_id, tt, topic=topic)


def _media_lookup(capability: str) -> Any:
    try:
        from services.media.registry import get_provider  # lane D
    except Exception:
        return None
    return get_provider(capability)


# ── persistence and recovery ──────────────────────────────────────────────────

def _persist(job: Dict[str, Any]) -> None:
    job["updated_at"] = _now_iso()
    try:
        d = jobs_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / ".{}.{}.tmp".format(job["id"], uuid.uuid4().hex)
        tmp.write_text(json.dumps(job, default=str, indent=1))
        os.replace(str(tmp), str(d / "{}.json".format(job["id"])))
    except Exception as exc:
        logger.error("[agents] could not persist job %s: %s", job.get("id"), exc)


def recover_jobs() -> int:
    """
    Load job files into memory. Anything caught mid-flight by a restart
    (queued/running/committing) becomes `failed` — runs are not resumable in
    wave 2, and pretending they are would leave a forever-spinning work order.
    Returns how many were marked interrupted.
    """
    global _loaded
    recovered = 0
    d = jobs_dir()
    if d.is_dir():
        for path in sorted(d.glob("*.json")):
            try:
                job = json.loads(path.read_text())
            except Exception as exc:
                logger.warning("[agents] unreadable job file %s: %s", path.name, exc)
                continue
            jid = job.get("id")
            if not jid or jid in AGENT_JOBS:
                continue
            if job.get("status") in ("queued", "running", "committing"):
                job["status"] = "failed"
                job["error"] = INTERRUPTED_MSG
                for step in job.get("steps") or []:
                    if step.get("status") == "running":
                        step["status"] = "failed"
                        step["error"] = INTERRUPTED_MSG
                _persist(job)
                recovered += 1
            AGENT_JOBS[jid] = job
    _loaded = True
    if recovered:
        logger.warning("[agents] %d work order(s) interrupted by a restart, marked failed", recovered)
    return recovered


def _ensure_loaded() -> None:
    if not _loaded:
        recover_jobs()


def _reset_for_tests() -> None:
    global _loaded
    AGENT_JOBS.clear()
    _commit_locks.clear()
    _tasks.clear()
    _sem_holder["loop"] = None
    _sem_holder["sem"] = None
    _loaded = False


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_event_loop()
    if _sem_holder["loop"] is not loop:
        _sem_holder["loop"] = loop
        _sem_holder["sem"] = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
    return _sem_holder["sem"]


# ── queries ───────────────────────────────────────────────────────────────────

def get_job(job_id: str) -> Dict[str, Any]:
    _ensure_loaded()
    job = AGENT_JOBS.get(job_id) if isinstance(job_id, str) else None
    if job is None:
        raise JobNotFound("No work order by that id.")
    return job


def summarize(job: Dict[str, Any]) -> Dict[str, Any]:
    s = {k: v for k, v in job.items() if k not in ("input", "outputs", "edits", "cancel_requested")}
    s["outputs"] = [{k: v for k, v in o.items() if k != "value"} for o in job.get("outputs") or []]
    s["output_count"] = len(job.get("outputs") or [])
    return s


def list_jobs(
    agent_id: Optional[Sequence[str]] = None,
    statuses: Optional[Sequence[str]] = None,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    _ensure_loaded()
    limit = max(1, min(int(limit or 25), 100))
    agents = set(agent_id) if agent_id else None
    wanted = set(statuses) if statuses else None
    rows = [
        j for j in AGENT_JOBS.values()
        if (agents is None or j.get("agent_id") in agents)
        and (wanted is None or j.get("status") in wanted)
    ]
    rows.sort(key=lambda j: j.get("updated_at") or "", reverse=True)
    return [summarize(j) for j in rows[:limit]]


def job_ref_ok(ref_id: str, agents: Sequence[str]) -> bool:
    job = AGENT_JOBS.get(ref_id)
    if job is None or job.get("status") != "done":
        return False
    return not agents or job.get("agent_id") in agents


def resolve_upload(upload_id: str) -> Optional[Path]:
    if not isinstance(upload_id, str) or not _UPLOAD_ID_RE.match(upload_id):
        return None
    d = uploads_dir()
    if not d.is_dir():
        return None
    for p in d.glob(upload_id + ".*"):
        if p.is_file():
            return p
    return None


def resolve_output_file(job_id: str, name: str) -> Path:
    """The only way an output file is located. Refuses anything outside the job dir."""
    get_job(job_id)
    if not isinstance(name, str) or not _FILE_NAME_RE.match(name) or name in (".", "..") \
            or name.startswith(".."):
        raise JobNotFound("No such file on this work order.")
    base = outputs_dir(job_id).resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_file():
        raise JobNotFound("No such file on this work order.")
    return path


# ── lifecycle ─────────────────────────────────────────────────────────────────

def _new_job(spec: AgentSpec, clean: Dict[str, Any], initiator: str, location_id: str,
             warnings: List[str], foundation: Dict[str, Any]) -> Dict[str, Any]:
    now = _now_iso()
    return {
        "id": str(uuid.uuid4()),
        "agent_id": spec.id,
        "status": "queued",
        "initiator": initiator,
        "location_id": location_id,
        "input": clean,
        "steps": [],
        "outputs": [],
        "warnings": list(warnings),
        "foundation": foundation,
        "needs_approval": False,
        "approval": None,
        "commit_result": None,
        "committed_at": None,
        "usage": [],
        "error": None,
        "created_at": now,
        "updated_at": now,
    }


def _build_context(job: Dict[str, Any], runner: ModuleType) -> AgentContext:
    out = outputs_dir(job["id"])
    try:
        out.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("[agents] could not create output dir for %s: %s", job["id"], exc)
    return AgentContext(
        job=job,
        output_dir=out,
        persist=lambda: _persist(job),
        allowed_routes=tuple(getattr(runner, "ROUTES", ()) or ()),
        brand_loader=_load_brand_context,
        media_lookup=_media_lookup,
    )


def _fail(job: Dict[str, Any], message: str) -> None:
    job["status"] = "failed"
    job["error"] = message
    job["needs_approval"] = False
    _persist(job)


async def start(
    agent_id: str,
    raw_input: Optional[Dict[str, Any]],
    initiator: str = "user",
    location_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate, gate, persist and spawn. Raises an AgentError subclass on refusal."""
    _ensure_loaded()
    spec = get_agent(agent_id)
    if spec is None:
        raise UnknownAgent("No crew member by that name.")
    runner = load_runner(spec)
    if runner is None:
        raise NotBuilt()

    clean, errors = validate_input(spec, raw_input, job_ref_ok=job_ref_ok,
                                   upload_ok=lambda u: resolve_upload(u) is not None)
    if errors:
        raise InvalidInput("A few fields need fixing before I can put anyone to work.",
                           fields=errors)

    missing = missing_requirements(spec)
    if missing:
        raise NeedsSetup("{} needs setup before going to work.".format(spec.name),
                         needs_setup=True, missing=missing)

    if location_id is None:
        from config import get_current_location_id
        location_id = get_current_location_id()

    warnings: List[str] = []
    foundation: Dict[str, Any] = {"used": False, "sample_count": None, "tier": None}
    if spec.foundation in ("required", "optional"):
        fs = await foundation_summary(location_id)
        foundation["sample_count"] = fs.get("sample_count")
        foundation["tier"] = fs.get("tier")
        if fs.get("tier") == "not_ready" and spec.foundation == "required":
            raise FoundationNotReady(
                "I can't write in your voice until I know your voice. Pour foundation first.",
                foundation_not_ready=True,
            )
        if fs.get("tier") == "thin":
            warnings.append("Thin Foundation ({} samples) — output will sound less like you."
                            .format(fs.get("sample_count")))

    job = _new_job(spec, clean, initiator, location_id, warnings, foundation)
    AGENT_JOBS[job["id"]] = job
    _persist(job)

    from services.task_guard import spawn
    from services.task_outcome import on_task_failure

    task = spawn(
        _run(job["id"]),
        name="agent:{}:{}".format(spec.id, job["id"]),
        on_failure=on_task_failure("agent_job", location_id=location_id,
                                   registry=AGENT_JOBS, job_id=job["id"]),
    )
    _tasks[job["id"]] = task
    return job


async def _run(job_id: str) -> None:
    job = AGENT_JOBS[job_id]
    async with _semaphore():
        if job.get("status") == "cancelled":
            return
        spec = get_agent(job["agent_id"])
        runner = load_runner(spec) if spec else None
        if spec is None or runner is None:
            _fail(job, "This crew member isn't on site anymore.")
            return
        job["status"] = "running"
        _persist(job)
        ctx = _build_context(job, runner)
        try:
            result = await runner.run(ctx, dict(job.get("input") or {}))
        except JobCancelled:
            job["status"] = "cancelled"
            _persist(job)
            return
        except StepError as exc:
            _fail(job, str(exc)[:500])
            return
        except Exception as exc:
            logger.exception("[agents] %s job %s broke", job.get("agent_id"), job_id)
            _fail(job, "Hit a snag on site ({}). Check the server log, then run it again."
                  .format(type(exc).__name__))
            return

        if job.get("status") == "cancelled":
            return  # Stopped mid-run: the result is discarded, not committed.

        for out in (getattr(result, "outputs", None) or []):
            if isinstance(out, Output) and out.id is None:
                ctx.add_output(out)

        plan = getattr(result, "commit", None)
        if plan is None or not spec.commit_tier:
            if plan is not None:
                logger.warning("[agents] %s returned a commit plan but never commits", spec.id)
            job["status"] = "done"
            _persist(job)
            return

        summary = str(getattr(plan, "summary", "") or "Commit this work.")[:500]
        job["commit_plan"] = {"summary": summary}

        tier = await current_tier(job["location_id"])
        if job.get("initiator") == "brick" and tier and _tier_allows(tier, spec.commit_tier):
            job["status"] = "committing"
            _persist(job)
            try:
                await commit_job(job_id)
            except Exception:
                pass  # commit_job already marked the job failed with a message
            return

        try:
            action_id = await _create_commit_action(job, spec, summary)
        except Exception as exc:
            logger.error("[agents] punch list write failed for %s: %s", job_id, exc)
            _fail(job, "Built it, but couldn't put it on your punch list — the database "
                       "didn't answer. Run it again.")
            return
        job["status"] = "needs_approval"
        job["needs_approval"] = True
        job["approval"] = {
            "action_id": action_id,
            "summary": summary,
            "commit_tier": spec.commit_tier,
            "current_tier": tier,
        }
        _persist(job)


def apply_edits(job: Dict[str, Any], edits: Optional[Dict[str, Any]]) -> None:
    if not edits:
        return
    if not isinstance(edits, dict):
        raise InvalidInput("Edits need to be keyed by output id.", fields={})
    by_id = {o.get("id"): o for o in job.get("outputs") or []}
    bad: Dict[str, str] = {}
    for oid in edits:
        out = by_id.get(oid)
        if out is None:
            bad[str(oid)] = "No output by that id."
        elif out.get("kind") not in EDITABLE_KINDS:
            bad[str(oid)] = "Only text and cards can be edited."
    if bad:
        raise InvalidInput("Some of those edits can't be applied.", fields=bad)
    stored = job.setdefault("edits", {})
    for oid, value in edits.items():
        by_id[oid]["value"] = value
        by_id[oid]["edited"] = True
        stored[oid] = value
    _persist(job)


async def commit_job(job_id: str) -> Any:
    """
    Run the agent's commit exactly once. The `agent_commit` dispatch branch in
    brick_agent calls this, so every approval path lands here. A second call after
    a successful commit returns the stored result instead of committing again.
    """
    job = get_job(job_id)
    lock = _commit_locks.setdefault(job_id, asyncio.Lock())
    async with lock:
        if job.get("committed_at"):
            return job.get("commit_result")
        if job.get("status") not in ("needs_approval", "committing"):
            raise JobConflict("Nothing to commit — this work order is {}.".format(job.get("status")))
        spec = get_agent(job["agent_id"])
        runner = load_runner(spec) if spec else None
        if spec is None or runner is None or not callable(getattr(runner, "commit", None)):
            _fail(job, "This crew member isn't on site anymore — nothing was committed.")
            raise NotBuilt()

        job["status"] = "committing"
        _persist(job)
        ctx = _build_context(job, runner)
        try:
            result = await runner.commit(ctx, copy.deepcopy(job), dict(job.get("edits") or {}))
        except StepError as exc:
            _fail(job, str(exc)[:500])
            raise
        except Exception as exc:
            logger.exception("[agents] commit for %s broke", job_id)
            _fail(job, "The commit hit a snag ({}). Nothing past that point was saved — "
                       "check the server log.".format(type(exc).__name__))
            raise

        job["commit_result"] = result
        job["committed_at"] = _now_iso()
        job["status"] = "done"
        job["needs_approval"] = False
        _persist(job)
        return result


async def approve(job_id: str, edits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The /agents Approve button: persist edits, then approve the punch list item."""
    job = get_job(job_id)
    if job.get("committed_at"):
        return job  # idempotent: the stored commit_result rides along on the job
    if job.get("status") != "needs_approval":
        raise JobConflict("Nothing to approve — this work order is {}.".format(job.get("status")))
    apply_edits(job, edits)
    action_id = (job.get("approval") or {}).get("action_id")
    if not action_id:
        raise JobConflict("This work order lost its punch list item — run it again.")
    try:
        await _brick_approve(action_id)
    except ValueError:
        # Already handled elsewhere (the Walk-through punch list, a double click).
        if job.get("committed_at"):
            return job
        raise JobConflict("That punch list item was already handled.")
    return job


async def reject(job_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    job = get_job(job_id)
    if job.get("status") != "needs_approval":
        raise JobConflict("Nothing to send back — this work order is {}.".format(job.get("status")))
    action_id = (job.get("approval") or {}).get("action_id")
    if action_id:
        try:
            await _brick_reject(action_id, reason)
        except ValueError:
            raise JobConflict("That punch list item was already handled.")
    job["status"] = "rejected"
    job["needs_approval"] = False
    if reason:
        job["reject_reason"] = str(reason)[:500]
    _persist(job)
    return job


async def cancel(job_id: str) -> Dict[str, Any]:
    """
    Stop a work order. Runners notice at the next ctx.check_cancelled(); a provider
    job already submitted is not killed (resubmitting could double-charge) — its
    result is discarded.
    """
    job = get_job(job_id)
    status = job.get("status")
    if status in TERMINAL_STATUSES:
        raise JobConflict("Already {} — nothing to stop.".format(status))
    if status == "committing":
        raise JobConflict("It's committing right now — too late to stop.")
    if status == "needs_approval":
        action_id = (job.get("approval") or {}).get("action_id")
        if action_id:
            try:
                await _brick_reject(action_id, "Stopped from The Crew.")
            except Exception as exc:
                logger.info("[agents] cancel could not reject action %s: %s", action_id, exc)
        job["needs_approval"] = False
    job["cancel_requested"] = True
    job["status"] = "cancelled"
    _persist(job)
    return job


def task_for(job_id: str) -> Optional["asyncio.Task[Any]"]:
    """Test/diagnostic hook: the spawned task for a job, if still referenced."""
    return _tasks.get(job_id)


def serialize_agent(spec: AgentSpec) -> Dict[str, Any]:
    state, missing = agent_state(spec)
    return {
        "id": spec.id,
        "name": spec.name,
        "category": spec.category,
        "job": spec.job,
        "icon": spec.icon,
        "state": state,
        "missing": missing,
        "foundation": spec.foundation,
        "run_tier": spec.run_tier,
        "spend_tier": spec.spend_tier,
        "commit_tier": spec.commit_tier,
        "requires": list(spec.requires),
        "spends": list(spec.spends),
        "wraps": list(spec.wraps),
        "require_one_of": [list(g) for g in spec.require_one_of],
        "fields": [f.to_dict() for f in spec.fields],
    }


def all_specs() -> List[AgentSpec]:
    return list(REGISTRY.values())
