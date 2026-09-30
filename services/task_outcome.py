"""
What a failed background task leaves behind.

Wave 0b made every background task loud — `spawn()` logs the exception with its
traceback under a task name. That fixed "the work vanished with no trace in the
logs." It did not fix the user-visible half: a project that sits in `processing`
forever, or a legacy job stuck on `running`, because only 1 of the 25 spawned
coroutines wrote a terminal status of its own.

This module is the `on_failure` side. It deliberately lives here rather than in
`services/task_guard.py`: the guard knows about asyncio, this knows about
PodClick's domain state, and mixing them would make the guard untestable without
a database.

Three things can happen when a task fails, and a call site opts into exactly the
ones that are true for it:

1. **Always** — an `audit_log` row (`action="task.failed"`, `actor_type="system"`).
   This is the durable, readable record that did not exist before Wave 0. It
   applies even to tasks that own no status field, so a failure is never invisible.

2. **Sometimes** — a terminal status on the owning `Project`. Only for tasks that
   own the project's lifecycle: Ship It moves a project through `processing`, so
   `failed` is the honest value and `/project/{id}` already renders a retry
   banner for it. Post-closing steps (distribution, guest assets, closing posts)
   must NOT touch it — the project really is `scheduled`, and overwriting that
   with `failed` would be a worse lie than the silence it replaces.

3. **Sometimes** — a terminal status in one of the in-process job registries
   (`jobs`, `clip_jobs`, `yt_spy_jobs`, `_VSL_JOBS`, `_broll_jobs`). The legacy
   surfaces poll these. `frontend/index.html` already tests for `"failed"` in six
   places, so that is the value written; pre-existing `"error"` /
   `"upload_error"` writes elsewhere are left alone rather than renamed under a
   working UI.

Like the audit writer, nothing here raises. A failure handler that throws would
replace one silent failure with two.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Statuses that mean "this is over, and it did not work."
PROJECT_FAILED = "failed"
TRANSCRIPTION_FAILED = "failed"
JOB_FAILED = "failed"

_stats: Dict[str, int] = {
    "recorded": 0,
    "project_marked": 0,
    "transcription_marked": 0,
    "job_marked": 0,
    "handler_errors": 0,
}


def outcome_health() -> Dict[str, Any]:
    """Counters for a health endpoint — how many failures were durably recorded."""
    return dict(_stats)


async def record_task_failure(
    task_name: str,
    exc: BaseException,
    location_id: Optional[str] = None,
    project_id: Optional[str] = None,
    fail_project: bool = False,
    fail_transcription: bool = False,
    registry: Optional[Dict[str, Dict[str, Any]]] = None,
    job_id: Optional[str] = None,
) -> None:
    """
    Record that a background task died. Never raises.

    `task_name` should match the name given to `spawn()` so the audit row and the
    log line agree. `fail_project` / `fail_transcription` are opt-in because most
    tasks do not own the project's lifecycle — see the module docstring.
    """
    detail = f"{type(exc).__name__}: {exc}"[:500]

    # 1. The durable record. Always.
    try:
        from services.audit import write_audit_log

        payload: Dict[str, Any] = {"task": task_name, "error": detail}
        if project_id:
            payload["project_id"] = str(project_id)
        if job_id:
            payload["job_id"] = str(job_id)
        await write_audit_log(
            location_id, "task.failed", payload, actor_type="system", actor_id="task_guard"
        )
        _stats["recorded"] += 1
    except Exception as audit_err:
        _stats["handler_errors"] += 1
        logger.error("[outcome] %s — could not audit the failure: %s", task_name, audit_err)

    # 2. The owning project's terminal status, when this task owns it.
    if project_id and (fail_project or fail_transcription):
        try:
            import uuid as _uuid

            from db.engine import async_session
            from db.models import Project

            async with async_session() as session:
                proj = await session.get(Project, _uuid.UUID(str(project_id)))
                if proj is None:
                    logger.warning(
                        "[outcome] %s — project %s not found, status not set",
                        task_name, project_id,
                    )
                else:
                    if fail_project:
                        proj.status = PROJECT_FAILED
                        _stats["project_marked"] += 1
                    if fail_transcription:
                        proj.transcription_status = TRANSCRIPTION_FAILED
                        _stats["transcription_marked"] += 1
                    await session.commit()
                    logger.error(
                        "[outcome] %s failed — project %s marked %s",
                        task_name, project_id, PROJECT_FAILED if fail_project else TRANSCRIPTION_FAILED,
                    )
        except Exception as db_err:
            _stats["handler_errors"] += 1
            logger.error(
                "[outcome] %s — could not mark project %s failed: %s",
                task_name, project_id, db_err,
            )

    # 3. The in-process registry the legacy surfaces poll.
    if registry is not None and job_id:
        try:
            entry = registry.get(job_id)
            if entry is None:
                logger.warning(
                    "[outcome] %s — job %s absent from its registry, status not set",
                    task_name, job_id,
                )
            else:
                entry["status"] = JOB_FAILED
                entry["error"] = detail
                _stats["job_marked"] += 1
                logger.error("[outcome] %s failed — job %s marked %s", task_name, job_id, JOB_FAILED)
        except Exception as reg_err:
            _stats["handler_errors"] += 1
            logger.error("[outcome] %s — could not mark job %s failed: %s", task_name, job_id, reg_err)


def on_task_failure(task_name: str, **kwargs: Any) -> Callable[[BaseException], Any]:
    """
    Build an `on_failure` callable for `spawn()`.

    Returns a coroutine, which `spawn` schedules as its own guarded task — so the
    recovery path is itself watched and cannot fail silently.

        spawn(
            _run_ship_it_async(...),
            name=f"ship_it:{project_id}",
            on_failure=on_task_failure("ship_it", project_id=project_id, fail_project=True),
        )
    """
    def handler(exc: BaseException) -> Any:
        return record_task_failure(task_name, exc, **kwargs)

    return handler


def _reset_for_tests() -> None:
    """Test-only: zero the counters."""
    for key in _stats:
        _stats[key] = 0
