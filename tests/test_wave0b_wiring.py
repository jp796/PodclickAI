"""
Wave 0b — prove the wiring, and keep it wired.

Wave 0 built services/audit.py and services/task_guard.py; Wave 0b routed
main.py's twelve raw-SQL audit blocks and twenty-three fire-and-forget tasks
through them. The unit tests for those two modules pass whether or not anything
actually calls them, so these tests assert the call sites themselves — by AST,
not by grep — and fail if a bare `asyncio.create_task` or a hand-rolled
`INSERT INTO audit_log` ever comes back.
"""

import ast
import asyncio
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
APP_SOURCES = [REPO / "main.py"] + sorted((REPO / "routers").glob("*.py"))


def _tree(path):
    return ast.parse(path.read_text(), filename=str(path))


def _call_name(node):
    """Dotted name of a Call's func, e.g. 'asyncio.create_task'."""
    f = node.func
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def _calls(path):
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call):
            yield node, _call_name(node)


# ── the wiring is real ────────────────────────────────────────────────────────

def test_main_imports_both_primitives():
    """Text in the file proves nothing; the bound objects must be the real ones."""
    import main
    from services.audit import write_audit_log
    from services.task_guard import spawn

    assert main.write_audit_log is write_audit_log
    assert main.spawn is spawn


def test_main_has_twelve_audit_call_sites():
    sites = [n for n, name in _calls(REPO / "main.py") if name == "write_audit_log"]
    assert len(sites) == 12, f"expected 12 write_audit_log calls, found {len(sites)}"


def test_main_has_twentythree_guarded_task_sites():
    sites = [n for n, name in _calls(REPO / "main.py") if name == "spawn"]
    assert len(sites) == 23, f"expected 23 spawn calls, found {len(sites)}"


def test_every_audit_call_passes_an_action():
    """
    write_audit_log(location, action, payload) — action is positional arg 2.

    An f-string action is allowed. The original assertion here demanded an
    ast.Constant, and that had a consequence worth recording: the
    project.transition site previously built a dynamic
    `f"project.status.{new_status}"`, and satisfying this test is what flattened
    it to a literal. Forbidding dynamic actions was never a real requirement —
    the real one is that every call names a non-empty action — so the test now
    says that instead of shaping the code around itself.
    """
    for node, name in _calls(REPO / "main.py"):
        if name != "write_audit_log":
            continue
        assert len(node.args) >= 2, f"line {node.lineno}: missing action argument"
        action = node.args[1]
        if isinstance(action, ast.Constant):
            assert isinstance(action.value, str) and action.value, (
                f"line {node.lineno}: action must be a non-empty string"
            )
        else:
            assert isinstance(action, ast.JoinedStr), (
                f"line {node.lineno}: action must be a string literal or f-string, "
                f"got {type(action).__name__}"
            )


def test_every_spawn_passes_a_name():
    """
    An unnamed task is an unidentifiable one in the logs — the whole point of
    routing these through spawn() is knowing WHICH job died.
    """
    for path in APP_SOURCES:
        for node, name in _calls(path):
            if name != "spawn":
                continue
            kwargs = {k.arg for k in node.keywords}
            assert "name" in kwargs, f"{path.name}:{node.lineno}: spawn() without name="


# ── regression guards ─────────────────────────────────────────────────────────

BARE_TASK_CALLS = {"asyncio.create_task", "asyncio.ensure_future"}


def test_no_bare_fire_and_forget_in_app_code():
    """
    Every background task goes through spawn(). asyncio keeps only a weak
    reference to a task, so a discarded handle can be collected mid-flight, and
    an unretrieved exception surfaces only at interpreter shutdown.
    """
    offenders = []
    for path in APP_SOURCES:
        for node, name in _calls(path):
            if name in BARE_TASK_CALLS:
                offenders.append(f"{path.name}:{node.lineno} {name}")
    assert not offenders, "bare task creation reintroduced:\n  " + "\n  ".join(offenders)


def test_no_hand_rolled_audit_sql_in_app_code():
    """
    The twelve raw-SQL audit blocks each swallowed failures with
    `except Exception: pass`, into a table that did not exist. One writer only.
    """
    offenders = [p.name for p in APP_SOURCES if "INSERT INTO audit_log" in p.read_text()]
    assert not offenders, f"raw audit_log SQL reintroduced in: {offenders}"


def test_http_exception_is_imported_in_main():
    """
    main.py raised HTTPException at 70 sites while never importing it — every one
    would have been a NameError surfacing as a bare 500. Pin the import.
    """
    import main
    from fastapi import HTTPException

    assert main.HTTPException is HTTPException


# ── the guard actually guards ──────────────────────────────────────────────────

async def test_spawned_failure_is_logged_and_counted(caplog):
    from services import task_guard

    task_guard._reset_for_tests()
    try:
        async def boom():
            raise RuntimeError("wave0b probe")

        with caplog.at_level("ERROR"):
            task = task_guard.spawn(boom(), name="probe:wave0b")
            with pytest.raises(RuntimeError):
                await task
            await asyncio.sleep(0)

        assert task_guard.task_health()["failed"] == 1
        assert "probe:wave0b" in caplog.text
        assert "wave0b probe" in caplog.text
    finally:
        task_guard._reset_for_tests()


async def test_audit_write_failure_is_logged_not_raised(caplog):
    """
    The contract main.py now depends on at twelve call sites: an audit failure
    must never propagate into the request, and must never be silent.
    """
    from sqlalchemy.exc import OperationalError

    from services import audit

    audit._reset_health_for_tests()
    try:
        class FailingSession:
            def add(self, _row):
                pass

            async def flush(self):
                raise OperationalError("stmt", {}, Exception("probe"))

        with caplog.at_level("ERROR"):
            ok = await audit.write_audit_log(
                None, "wave0b_probe", {"k": "v"}, session=FailingSession()
            )

        assert ok is False
        assert "wave0b_probe" in caplog.text
        assert audit.audit_health()["failed"] == 1
    finally:
        audit._reset_health_for_tests()
