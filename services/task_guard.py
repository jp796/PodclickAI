"""
Guarded background tasks.

`asyncio.create_task` has two failure modes this codebase hits at 23 call sites:

1. **The return value is discarded.** asyncio keeps only a weak reference to a
   running task, so CPython is free to garbage-collect one mid-flight. A long
   Ship It render can simply stop, with no error and no log.

2. **Exceptions are never retrieved.** An unhandled exception in a fire-and-forget
   task surfaces only as "Task exception was never retrieved" at interpreter
   shutdown, if at all. The user-visible symptom is a project stuck in
   `processing` forever with nothing in the logs.

A grep for `add_done_callback` across this repo returned zero results, and only 2
of 23 background tasks set a terminal `failed` status. `spawn()` fixes both: it
holds a strong reference until completion and attaches a callback that logs any
exception and invokes an optional `on_failure` so the caller can mark its row
`failed`.

Usage:

    from services.task_guard import spawn

    spawn(_run_ship_it_async(project_id), name=f"ship_it:{project_id}",
          on_failure=lambda exc: _mark_project_failed(project_id, exc))

Cancellation is not a failure: a cancelled task logs at info and does not call
`on_failure`, so shutdown does not look like breakage.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Strong references to in-flight tasks. Without this set, asyncio's weak
# reference is the only one and the loop may collect a running task.
_live: Set[asyncio.Task] = set()

_stats: Dict[str, int] = {
    "spawned": 0,
    "succeeded": 0,
    "failed": 0,
    "cancelled": 0,
    "callback_errors": 0,
}


def task_health() -> Dict[str, Any]:
    """Counters plus the names of tasks still running, for a health endpoint."""
    return dict(_stats, in_flight=len(_live), in_flight_names=pending_task_names())


def pending_task_names() -> List[str]:
    """Names of tasks that have not finished yet."""
    return sorted(t.get_name() for t in _live if not t.done())


def spawn(
    coro: Awaitable[Any],
    name: str,
    on_failure: Optional[Callable[[BaseException], Any]] = None,
) -> asyncio.Task:
    """
    Start a background task that cannot vanish silently.

    `name` shows up in logs and in `pending_task_names()`, so make it identify the
    work — `"ship_it:<project_id>"`, not `"task"`.

    `on_failure` receives the exception. Keep it cheap and synchronous-ish: it runs
    inside the done callback. Use it to set a terminal `failed` status. If it is a
    coroutine function, the returned coroutine is scheduled as its own guarded
    task so a failed status update is itself not silent.
    """
    task = asyncio.ensure_future(coro)
    task.set_name(name)
    _live.add(task)
    _stats["spawned"] += 1

    def _done(finished: asyncio.Task) -> None:
        _live.discard(finished)
        try:
            if finished.cancelled():
                _stats["cancelled"] += 1
                logger.info("[task] %s cancelled", name)
                return
            exc = finished.exception()
        except Exception as probe_err:  # pragma: no cover — defensive
            _stats["callback_errors"] += 1
            logger.error("[task] %s — could not read task outcome: %s", name, probe_err)
            return

        if exc is None:
            _stats["succeeded"] += 1
            logger.debug("[task] %s finished", name)
            return

        _stats["failed"] += 1
        # exc_info carries the traceback — this is the line that was missing when a
        # project silently stuck in 'processing'.
        logger.error("[task] %s failed: %s", name, exc, exc_info=exc)

        if on_failure is None:
            return
        try:
            result = on_failure(exc)
            if asyncio.iscoroutine(result):
                # Guard the recovery path too, one level deep. on_failure=None
                # prevents an unbounded chain if the status update also fails.
                spawn(result, name=f"{name}:on_failure", on_failure=None)
        except Exception as handler_err:
            _stats["callback_errors"] += 1
            logger.error(
                "[task] %s — on_failure handler itself failed: %s", name, handler_err
            )

    task.add_done_callback(_done)
    return task


async def drain(timeout: Optional[float] = 30.0) -> List[str]:
    """
    Wait for in-flight tasks, for orderly shutdown. Returns the names that were
    still running when the timeout expired.
    """
    if not _live:
        return []
    pending = list(_live)
    done, still_running = await asyncio.wait(pending, timeout=timeout)
    return sorted(t.get_name() for t in still_running)


def _reset_for_tests() -> None:
    """Test-only: clear counters and any stale references."""
    _live.clear()
    for key in _stats:
        _stats[key] = 0
