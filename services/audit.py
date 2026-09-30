"""
The audit trail — one writer, no silence.

Before this module, `audit_log` was written by twelve copy-pasted raw-SQL blocks
in main.py, eleven of them wrapped in `except Exception: pass`. No migration or
model ever created the table, and nothing ever read it. So the "immutable audit
trail" that services/brick_agent.py's permit ladder rests on was, at best,
unverifiable and, at worst, twelve writes per day into nothing.

The non-blocking intent was right: a failed audit write must never fail the user's
request. The defect was the silence. `write_audit_log` keeps the intent — it never
raises — and removes the silence: every failure is logged with the action name, a
missing table is reported distinctly from a transient error, and the counters in
`audit_health()` make a persistently broken trail visible rather than invisible.

Usage from a route handler:

    from services.audit import write_audit_log
    await write_audit_log(location_id, "show_notes", {"topic": topic})

Pass an existing `session` to enlist in a caller's transaction; omit it and the
write gets its own short-lived session and commits on its own.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import async_session
from db.models import AuditLog

logger = logging.getLogger(__name__)

# PostgreSQL: undefined_table. A missing table is a deployment problem that no
# amount of retrying fixes, so it earns its own log line and counter.
PG_UNDEFINED_TABLE = "42P01"

VALID_ACTOR_TYPES = ("user", "brick", "system")

_health: Dict[str, int] = {
    "written": 0,
    "failed": 0,
    "table_missing": 0,
}


def audit_health() -> Dict[str, Any]:
    """
    Counters for this process, plus a plain-language verdict.

    `table_missing` above zero means the audit trail is not being recorded at all
    and no migration has been applied — surface that, do not average it away.
    """
    total = _health["written"] + _health["failed"]
    if _health["table_missing"]:
        verdict = "broken — audit_log table does not exist; run alembic upgrade head"
    elif _health["failed"] and not _health["written"]:
        verdict = "broken — every audit write in this process failed"
    elif _health["failed"]:
        verdict = f"degraded — {_health['failed']} of {total} audit writes failed"
    elif _health["written"]:
        verdict = "healthy"
    else:
        verdict = "idle — nothing audited yet in this process"
    return dict(_health, attempted=total, verdict=verdict)


def _is_missing_table(exc: BaseException) -> bool:
    """True when the failure is 'relation audit_log does not exist'."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(
        getattr(exc, "orig", None), "pgcode", None
    )
    if sqlstate == PG_UNDEFINED_TABLE:
        return True
    text = str(exc).lower()
    return "audit_log" in text and "does not exist" in text


async def write_audit_log(
    location_id: Optional[str],
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    actor_type: str = "user",
    actor_id: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> bool:
    """
    Record one audited action. Returns True when the row was written.

    Never raises — an audit failure must not fail the work being audited. Every
    failure is logged, so a broken trail is loud in the logs and visible in
    `audit_health()` instead of being swallowed at the call site.
    """
    if not action:
        logger.error("[audit] refusing to write a row with no action name")
        _health["failed"] += 1
        return False

    if actor_type not in VALID_ACTOR_TYPES:
        # Don't reject the row over a label — record it and say so.
        logger.warning(
            "[audit] unknown actor_type %r for action %r — storing as-is", actor_type, action
        )

    # Parse before building the row: a malformed identifier is a caller bug, and it
    # must be logged like every other failure rather than raised into the request.
    parsed_location = None
    if location_id:
        try:
            parsed_location = uuid.UUID(str(location_id))
        except (ValueError, AttributeError, TypeError) as exc:
            _health["failed"] += 1
            logger.error(
                "[audit] invalid location_id %r for action %r: %s", location_id, action, exc
            )
            return False

    row = AuditLog(
        id=uuid.uuid4(),
        location_id=parsed_location,
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        payload=payload or {},
    )

    try:
        if session is not None:
            # Enlist in the caller's transaction; they own the commit.
            session.add(row)
            await session.flush()
        else:
            async with async_session() as own_session:
                own_session.add(row)
                await own_session.commit()
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            _health["table_missing"] += 1
            _health["failed"] += 1
            logger.error(
                "[audit] audit_log table does not exist — action %r was NOT recorded. "
                "Run `alembic upgrade head` to create it.",
                action,
            )
            return False
        _health["failed"] += 1
        logger.error("[audit] schema error writing action %r: %s", action, exc)
        return False
    except SQLAlchemyError as exc:
        _health["failed"] += 1
        logger.error("[audit] database error writing action %r: %s", action, exc)
        return False
    except Exception as exc:
        # "Never raises" has to mean never. Twelve call sites in main.py now sit
        # outside any try/except of their own, so anything that escapes here —
        # a driver error that is not a SQLAlchemyError, a TypeError building the
        # row, a cancellation of the surrounding request — would turn an audit
        # hiccup into a 500 on work that already succeeded. Caught narrowly
        # before this for precise messages; caught broadly here for the contract.
        _health["failed"] += 1
        logger.error(
            "[audit] unexpected %s writing action %r: %s", type(exc).__name__, action, exc
        )
        return False
    _health["written"] += 1
    logger.debug("[audit] recorded %r (actor=%s)", action, actor_type)
    return True


def _reset_health_for_tests() -> None:
    """Test-only: zero the counters between cases."""
    for key in _health:
        _health[key] = 0
