"""
Wave 0 — the audit trail must never be silent.

The defect being tested against: twelve raw-SQL `audit_log` inserts in main.py,
eleven wrapped in `except Exception: pass`, writing into a table no migration ever
created. These tests pin the two properties that make that unrepeatable — the
writer never raises, and it never fails quietly.
"""

import uuid

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from services import audit


@pytest.fixture(autouse=True)
def clean_counters():
    audit._reset_health_for_tests()
    yield
    audit._reset_health_for_tests()


class FakeSession:
    """Minimal AsyncSession stand-in: records adds, optionally fails on flush."""

    def __init__(self, flush_error=None):
        self.added = []
        self._flush_error = flush_error
        self.flushed = False

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        if self._flush_error:
            raise self._flush_error
        self.flushed = True


def _undefined_table_error():
    """A ProgrammingError shaped like PostgreSQL's 42P01."""

    class Orig(Exception):
        sqlstate = "42P01"

    return ProgrammingError("INSERT INTO audit_log ...", {}, Orig("undefined table"))


# ── happy path ────────────────────────────────────────────────────────────────

async def test_write_enlists_in_caller_session_and_counts_it():
    session = FakeSession()
    ok = await audit.write_audit_log(
        str(uuid.uuid4()), "show_notes", {"topic": "flips"}, session=session
    )
    assert ok is True
    assert session.flushed is True
    assert len(session.added) == 1
    assert audit.audit_health()["written"] == 1
    assert audit.audit_health()["verdict"] == "healthy"


async def test_row_carries_action_actor_and_payload():
    session = FakeSession()
    loc = str(uuid.uuid4())
    await audit.write_audit_log(
        loc, "brick.approve", {"action_id": "a1"},
        actor_type="brick", actor_id="brick", session=session,
    )
    row = session.added[0]
    assert row.action == "brick.approve"
    assert row.actor_type == "brick"
    assert row.actor_id == "brick"
    assert row.payload == {"action_id": "a1"}
    assert str(row.location_id) == loc


async def test_missing_location_is_allowed():
    """Not every audited action belongs to a location."""
    session = FakeSession()
    assert await audit.write_audit_log(None, "system.boot", session=session) is True
    assert session.added[0].location_id is None


async def test_payload_defaults_to_empty_dict():
    session = FakeSession()
    await audit.write_audit_log(None, "no_payload", session=session)
    assert session.added[0].payload == {}


# ── the silence tests ─────────────────────────────────────────────────────────

async def test_missing_table_is_reported_distinctly_and_loudly(caplog):
    session = FakeSession(flush_error=_undefined_table_error())
    with caplog.at_level("ERROR"):
        ok = await audit.write_audit_log(None, "show_notes", session=session)

    assert ok is False
    health = audit.audit_health()
    assert health["table_missing"] == 1
    assert health["verdict"].startswith("broken — audit_log table does not exist")
    # The log must name the action AND the remedy, or a broken trail stays a mystery.
    assert "show_notes" in caplog.text
    assert "alembic upgrade head" in caplog.text


async def test_other_schema_error_is_logged_but_not_called_missing_table(caplog):
    class Orig(Exception):
        sqlstate = "42703"  # undefined_column

    session = FakeSession(flush_error=ProgrammingError("stmt", {}, Orig("nope")))
    with caplog.at_level("ERROR"):
        assert await audit.write_audit_log(None, "script_formula", session=session) is False

    assert audit.audit_health()["table_missing"] == 0
    assert audit.audit_health()["failed"] == 1
    assert "script_formula" in caplog.text


async def test_transient_database_error_is_logged(caplog):
    session = FakeSession(flush_error=OperationalError("stmt", {}, Exception("conn lost")))
    with caplog.at_level("ERROR"):
        assert await audit.write_audit_log(None, "hashtag_set", session=session) is False
    assert audit.audit_health()["failed"] == 1
    assert "hashtag_set" in caplog.text


async def test_write_never_raises_on_database_failure():
    """The whole point: an audit failure must not fail the work being audited."""
    session = FakeSession(flush_error=OperationalError("stmt", {}, Exception("boom")))
    result = await audit.write_audit_log(None, "cover_forge", session=session)
    assert result is False  # reached here at all == did not raise


async def test_empty_action_is_refused_and_logged(caplog):
    with caplog.at_level("ERROR"):
        assert await audit.write_audit_log(None, "", session=FakeSession()) is False
    assert audit.audit_health()["failed"] == 1
    assert "no action name" in caplog.text


async def test_bad_location_uuid_is_logged_not_raised(caplog):
    with caplog.at_level("ERROR"):
        assert await audit.write_audit_log("not-a-uuid", "repurpose_yt") is False
    assert audit.audit_health()["failed"] == 1
    assert "repurpose_yt" in caplog.text


async def test_unknown_actor_type_warns_but_still_records(caplog):
    session = FakeSession()
    with caplog.at_level("WARNING"):
        assert await audit.write_audit_log(
            None, "odd", actor_type="robot", session=session
        ) is True
    assert "robot" in caplog.text
    assert session.added[0].actor_type == "robot"


# ── own-session path ──────────────────────────────────────────────────────────

async def test_without_a_session_it_opens_and_commits_its_own(monkeypatch):
    committed = {"n": 0}

    class OwnSession(FakeSession):
        async def commit(self):
            committed["n"] += 1

    class Ctx:
        async def __aenter__(self):
            return OwnSession()

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(audit, "async_session", lambda: Ctx())
    assert await audit.write_audit_log(None, "project.created") is True
    assert committed["n"] == 1


# ── health reporting ──────────────────────────────────────────────────────────

def test_health_starts_idle():
    assert audit.audit_health()["verdict"].startswith("idle")


async def test_health_reports_degraded_when_some_writes_fail():
    await audit.write_audit_log(None, "ok_one", session=FakeSession())
    await audit.write_audit_log(
        None, "bad_one", session=FakeSession(flush_error=OperationalError("s", {}, Exception()))
    )
    health = audit.audit_health()
    assert health["attempted"] == 2
    assert health["verdict"].startswith("degraded — 1 of 2")


async def test_health_reports_broken_when_every_write_fails():
    await audit.write_audit_log(
        None, "bad", session=FakeSession(flush_error=OperationalError("s", {}, Exception()))
    )
    assert audit.audit_health()["verdict"].startswith("broken — every audit write")


def test_missing_table_detected_from_message_when_sqlstate_absent():
    """Some drivers surface no sqlstate; the message still has to be enough."""
    exc = ProgrammingError('relation "audit_log" does not exist', {}, Exception())
    assert audit._is_missing_table(exc) is True


def test_unrelated_error_is_not_mistaken_for_missing_table():
    exc = ProgrammingError('relation "projects" does not exist', {}, Exception())
    assert audit._is_missing_table(exc) is False
