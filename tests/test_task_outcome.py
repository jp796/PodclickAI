"""
Wave 0c — what a failed background task leaves behind.

Wave 0b made failures loud in the logs. These tests cover the user-visible half:
the audit row that always gets written, the terminal status that gets written only
where the task owns it, and the legacy registry entry the older surfaces poll.

The one rule that matters most: this module runs *inside* a failure handler, so it
must never raise. A handler that throws replaces one silent failure with two.
"""

import ast
from pathlib import Path

import pytest

from services import task_outcome

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    task_outcome._reset_for_tests()
    # Default: audit is a no-op success unless a test overrides it.
    calls = []

    async def fake_audit(location_id, action, payload=None, actor_type="user",
                         actor_id=None, session=None):
        calls.append({
            "location_id": location_id, "action": action, "payload": payload,
            "actor_type": actor_type, "actor_id": actor_id,
        })
        return True

    import services.audit as audit_mod
    monkeypatch.setattr(audit_mod, "write_audit_log", fake_audit)
    yield calls
    task_outcome._reset_for_tests()


# ── the audit row is unconditional ────────────────────────────────────────────

async def test_failure_always_writes_an_audit_row(clean):
    await task_outcome.record_task_failure("ship_it", RuntimeError("boom"))

    assert len(clean) == 1
    row = clean[0]
    assert row["action"] == "task.failed"
    assert row["actor_type"] == "system"
    assert row["actor_id"] == "task_guard"
    assert row["payload"]["task"] == "ship_it"
    assert "RuntimeError: boom" in row["payload"]["error"]
    assert task_outcome.outcome_health()["recorded"] == 1


async def test_ids_are_included_in_the_payload_when_known(clean):
    await task_outcome.record_task_failure(
        "pipeline", ValueError("x"), project_id="p-1", job_id="j-1"
    )
    payload = clean[0]["payload"]
    assert payload["project_id"] == "p-1"
    assert payload["job_id"] == "j-1"


async def test_long_error_text_is_truncated(clean):
    await task_outcome.record_task_failure("t", RuntimeError("y" * 900))
    assert len(clean[0]["payload"]["error"]) <= 500


async def test_a_failing_audit_write_does_not_raise(monkeypatch, clean):
    """The recovery path cannot become the new failure."""
    async def exploding_audit(*_a, **_k):
        raise RuntimeError("audit down")

    import services.audit as audit_mod
    monkeypatch.setattr(audit_mod, "write_audit_log", exploding_audit)

    await task_outcome.record_task_failure("t", ValueError("original"))
    assert task_outcome.outcome_health()["handler_errors"] == 1


# ── the legacy registry ───────────────────────────────────────────────────────

async def test_registry_entry_is_marked_failed(clean):
    registry = {"j-1": {"status": "running", "step": "rendering"}}
    await task_outcome.record_task_failure(
        "clip_job", RuntimeError("ffmpeg died"), registry=registry, job_id="j-1"
    )

    assert registry["j-1"]["status"] == "failed"
    assert "ffmpeg died" in registry["j-1"]["error"]
    # untouched keys survive
    assert registry["j-1"]["step"] == "rendering"
    assert task_outcome.outcome_health()["job_marked"] == 1


async def test_missing_registry_entry_is_survivable(clean):
    """A job evicted before its task died must not crash the handler."""
    await task_outcome.record_task_failure(
        "clip_job", RuntimeError("x"), registry={}, job_id="gone"
    )
    assert task_outcome.outcome_health()["job_marked"] == 0
    assert task_outcome.outcome_health()["recorded"] == 1  # audit still written


async def test_registry_untouched_without_a_job_id(clean):
    registry = {"j-1": {"status": "running"}}
    await task_outcome.record_task_failure("t", RuntimeError("x"), registry=registry)
    assert registry["j-1"]["status"] == "running"


# ── the project's terminal status ─────────────────────────────────────────────

class _FakeProject:
    def __init__(self):
        self.status = "processing"
        self.transcription_status = "pending"


def _patch_session(monkeypatch, project):
    """Stand in for db.engine.async_session with a one-project store."""
    committed = {"n": 0}

    class Session:
        async def get(self, _model, _pk):
            return project

        async def commit(self):
            committed["n"] += 1

    class Ctx:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_):
            return False

    import db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "async_session", lambda: Ctx())
    return committed


VALID_UUID = "6f1d9c2e-0000-4000-8000-000000000001"


async def test_fail_project_sets_failed_status(monkeypatch, clean):
    """Ship It owns the project's lifecycle — `failed` is the honest value."""
    project = _FakeProject()
    committed = _patch_session(monkeypatch, project)

    await task_outcome.record_task_failure(
        "ship_it", RuntimeError("render died"),
        project_id=VALID_UUID, fail_project=True,
    )

    assert project.status == "failed"
    assert project.transcription_status == "pending"  # untouched
    assert committed["n"] == 1
    assert task_outcome.outcome_health()["project_marked"] == 1


async def test_fail_transcription_touches_only_that_field(monkeypatch, clean):
    project = _FakeProject()
    _patch_session(monkeypatch, project)

    await task_outcome.record_task_failure(
        "transcribe", RuntimeError("whisper 413"),
        project_id=VALID_UUID, fail_transcription=True,
    )

    assert project.transcription_status == "failed"
    assert project.status == "processing"  # the project itself is not condemned
    assert task_outcome.outcome_health()["transcription_marked"] == 1


async def test_project_status_untouched_when_not_opted_in(monkeypatch, clean):
    """
    A post-closing step failing must not rewrite the project's state. This is the
    difference between recording a failure and lying about one.
    """
    project = _FakeProject()
    _patch_session(monkeypatch, project)

    await task_outcome.record_task_failure(
        "distribute", RuntimeError("youtube 500"), project_id=VALID_UUID
    )

    assert project.status == "processing"
    assert project.transcription_status == "pending"
    assert task_outcome.outcome_health()["project_marked"] == 0
    assert task_outcome.outcome_health()["recorded"] == 1  # but it IS recorded


async def test_a_malformed_project_id_is_logged_not_raised(monkeypatch, clean, caplog):
    with caplog.at_level("ERROR"):
        await task_outcome.record_task_failure(
            "ship_it", RuntimeError("x"), project_id="not-a-uuid", fail_project=True
        )
    assert task_outcome.outcome_health()["handler_errors"] == 1
    assert "ship_it" in caplog.text


async def test_a_missing_project_is_survivable(monkeypatch, clean):
    committed = _patch_session(monkeypatch, None)  # get() returns None

    await task_outcome.record_task_failure(
        "ship_it", RuntimeError("x"), project_id=VALID_UUID, fail_project=True
    )

    assert committed["n"] == 0
    assert task_outcome.outcome_health()["project_marked"] == 0
    assert task_outcome.outcome_health()["recorded"] == 1


# ── the handler factory ───────────────────────────────────────────────────────

async def test_on_task_failure_returns_an_awaitable_handler(clean):
    registry = {"j": {"status": "running"}}
    handler = task_outcome.on_task_failure(
        "pipeline", registry=registry, job_id="j"
    )
    await handler(RuntimeError("late"))
    assert registry["j"]["status"] == "failed"
    assert clean[0]["payload"]["task"] == "pipeline"


# ── the wiring, asserted by AST ───────────────────────────────────────────────

APP_SOURCES = [REPO / "main.py", REPO / "routers" / "foundation.py"]


def _spawn_calls(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "spawn":
            yield node


def test_every_spawn_has_an_on_failure():
    """
    Wave 0b gave every task a name; 0c gives every task an outcome. Without this,
    a failed task is loud in the logs and still invisible in the product.
    """
    missing = []
    for path in APP_SOURCES:
        for node in _spawn_calls(path):
            if "on_failure" not in {k.arg for k in node.keywords}:
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, "spawn() without on_failure:\n  " + "\n  ".join(missing)


def test_on_failure_is_always_the_shared_factory():
    """One place decides what a failure means — not 25 inline lambdas."""
    wrong = []
    for path in APP_SOURCES:
        for node in _spawn_calls(path):
            handler = next((k.value for k in node.keywords if k.arg == "on_failure"), None)
            ok = (
                isinstance(handler, ast.Call)
                and isinstance(handler.func, ast.Name)
                and handler.func.id == "on_task_failure"
            )
            if not ok:
                wrong.append(f"{path.name}:{node.lineno}")
    assert not wrong, "on_failure not built by on_task_failure():\n  " + "\n  ".join(wrong)


def test_only_lifecycle_owning_tasks_mark_a_project_failed():
    """
    Overwriting a `scheduled` project with `failed` because a post-closing step
    died would be a worse lie than the silence it replaces. Ship It owns the
    project's lifecycle; distribution, guest assets and closing posts do not.
    """
    allowed = {"ship_it"}
    offenders = []
    for path in APP_SOURCES:
        for node in _spawn_calls(path):
            handler = next((k.value for k in node.keywords if k.arg == "on_failure"), None)
            if not isinstance(handler, ast.Call):
                continue
            kw = {k.arg: k.value for k in handler.keywords}
            flag = kw.get("fail_project")
            if isinstance(flag, ast.Constant) and flag.value is True:
                task = handler.args[0].value if handler.args else "?"
                if task not in allowed:
                    offenders.append(f"{path.name}:{node.lineno} task={task}")
    assert not offenders, "fail_project=True on a task that does not own the lifecycle:\n  " + "\n  ".join(offenders)
