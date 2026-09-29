"""
Wave 0 — background tasks must not vanish.

The defect: 23 `asyncio.create_task` call sites, zero `add_done_callback` anywhere
in the repo, and only 2 of them setting a terminal `failed` status. The
user-visible symptom was a project stuck in `processing` forever with nothing in
the logs. These tests pin that a guarded task logs its exception, tells the caller
so a row can be marked failed, and is held strongly enough not to be collected
mid-flight.
"""

import asyncio

import pytest

from services import task_guard


@pytest.fixture(autouse=True)
def clean_state():
    task_guard._reset_for_tests()
    yield
    task_guard._reset_for_tests()


async def test_successful_task_is_counted_and_reference_released():
    async def work():
        return "done"

    task = task_guard.spawn(work(), name="ok")
    assert await task == "done"
    await asyncio.sleep(0)  # let the done callback run

    health = task_guard.task_health()
    assert health["succeeded"] == 1
    assert health["failed"] == 0
    assert health["in_flight"] == 0


async def test_task_is_strongly_referenced_while_running():
    """Without the strong ref, CPython may collect a running task mid-flight."""
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    task_guard.spawn(slow(), name="ship_it:abc")
    assert "ship_it:abc" in task_guard.pending_task_names()
    assert task_guard.task_health()["in_flight"] == 1

    gate.set()
    await asyncio.sleep(0.01)
    assert task_guard.pending_task_names() == []


async def test_failing_task_logs_with_traceback(caplog):
    async def boom():
        raise RuntimeError("render died")

    with caplog.at_level("ERROR"):
        task = task_guard.spawn(boom(), name="render:42")
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

    assert task_guard.task_health()["failed"] == 1
    # The name must be in the log or you cannot tell which job died.
    assert "render:42" in caplog.text
    assert "render died" in caplog.text


async def test_on_failure_receives_the_exception():
    """This is the hook that sets a project's terminal 'failed' status."""
    seen = []

    async def boom():
        raise ValueError("bad clip")

    task = task_guard.spawn(boom(), name="clip", on_failure=seen.append)
    with pytest.raises(ValueError):
        await task
    await asyncio.sleep(0)

    assert len(seen) == 1
    assert isinstance(seen[0], ValueError)
    assert str(seen[0]) == "bad clip"


async def test_on_failure_not_called_on_success():
    called = []

    async def work():
        return 1

    await task_guard.spawn(work(), name="fine", on_failure=called.append)
    await asyncio.sleep(0)
    assert called == []


async def test_cancellation_is_not_a_failure():
    """Shutdown must not look like breakage."""
    called = []
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    task = task_guard.spawn(slow(), name="cancel-me", on_failure=called.append)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    health = task_guard.task_health()
    assert health["cancelled"] == 1
    assert health["failed"] == 0
    assert called == []


async def test_async_on_failure_is_scheduled_as_its_own_guarded_task():
    """A status update that is itself async must not be dropped."""
    marked = []

    async def mark_failed(exc):
        await asyncio.sleep(0)
        marked.append(str(exc))

    async def boom():
        raise RuntimeError("upload failed")

    task = task_guard.spawn(boom(), name="upload", on_failure=mark_failed)
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0.02)

    assert marked == ["upload failed"]


async def test_failing_on_failure_handler_is_itself_logged(caplog):
    """The recovery path cannot be the new silent failure."""

    def bad_handler(_exc):
        raise KeyError("handler broke")

    async def boom():
        raise RuntimeError("original")

    with caplog.at_level("ERROR"):
        task = task_guard.spawn(boom(), name="nested", on_failure=bad_handler)
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

    assert task_guard.task_health()["callback_errors"] == 1
    assert "on_failure handler itself failed" in caplog.text


async def test_drain_returns_names_still_running_after_timeout():
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    task_guard.spawn(slow(), name="stuck:1")
    still = await task_guard.drain(timeout=0.01)
    assert still == ["stuck:1"]

    gate.set()
    await asyncio.sleep(0.01)


async def test_drain_with_nothing_running_is_empty():
    assert await task_guard.drain(timeout=0.01) == []


async def test_health_exposes_in_flight_names_for_a_status_endpoint():
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    task_guard.spawn(slow(), name="b")
    task_guard.spawn(slow(), name="a")
    health = task_guard.task_health()
    assert health["in_flight_names"] == ["a", "b"]
    assert health["spawned"] == 2

    gate.set()
    await asyncio.sleep(0.01)
