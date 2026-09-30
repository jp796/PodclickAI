"""Release recovery tests: temporary queue only, never real provider requests."""
import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from pipeline import scheduler


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(scheduler, "queue", {})
    monkeypatch.setattr(scheduler, "_inflight", set())
    monkeypatch.setattr(scheduler, "_flip_to_public", AsyncMock(return_value={"success": True}))


def release(when=1):
    return scheduler.add_to_queue("project-1", "Test episode", 1, "provider-1", when)


def test_repeated_scheduling_reuses_provider_episode():
    first = release()
    again = release(123456)
    assert first["entry_id"] == again["entry_id"]
    assert len(scheduler.queue) == 1
    assert again["scheduled_at"] == 123456
    assert len(json.loads(scheduler.QUEUE_FILE.read_text())) == 1


def test_restart_preserves_shared_queue_reference_and_recovers_interruption():
    entry = release()
    entry.update(status="publishing", attempts=1)
    scheduler.save_queue()
    original = scheduler.queue
    scheduler.load_queue()
    assert scheduler.queue is original
    assert scheduler.queue[entry["entry_id"]]["status"] == "scheduled"


def test_unreadable_queue_is_not_overwritten():
    scheduler.QUEUE_FILE.write_text("[corrupted")
    with pytest.raises(RuntimeError, match="original file preserved"):
        scheduler.load_queue()
    assert scheduler.QUEUE_FILE.read_text() == "[corrupted"


@pytest.mark.asyncio
async def test_future_release_does_not_publish():
    entry = release(datetime.now().timestamp() + 3600)
    result = await scheduler._publish_entry(entry["entry_id"], notify=False)
    assert result["status"] == "not_due"
    scheduler._flip_to_public.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_dispatch_claims_once(monkeypatch):
    entry = release()
    started, finish = asyncio.Event(), asyncio.Event()

    async def provider(_episode_id):
        started.set()
        await finish.wait()
        return {"success": True}

    provider_mock = AsyncMock(side_effect=provider)
    monkeypatch.setattr(scheduler, "_flip_to_public", provider_mock)
    pending = asyncio.create_task(scheduler._publish_entry(entry["entry_id"], notify=False))
    await started.wait()
    duplicate = await scheduler._publish_entry(entry["entry_id"], force=True, notify=False)
    assert duplicate["status"] == "busy"
    assert scheduler.remove_from_queue(entry["entry_id"]) is False
    assert scheduler.reschedule(entry["entry_id"], 50) is None
    finish.set()
    await pending
    provider_mock.assert_awaited_once()
    assert entry["status"] == "published"


@pytest.mark.asyncio
async def test_completed_release_cannot_publish_twice():
    entry = release()
    await scheduler._publish_entry(entry["entry_id"], notify=False)
    await scheduler._publish_entry(entry["entry_id"], force=True, notify=False)
    release(55)
    scheduler._flip_to_public.assert_awaited_once()
    assert entry["status"] == "published"


@pytest.mark.asyncio
async def test_transient_failure_retries_with_backoff_then_stops(monkeypatch):
    entry = release()
    provider = AsyncMock(return_value={"success": False, "retryable": True, "error": "Temporary outage"})
    monkeypatch.setattr(scheduler, "_flip_to_public", provider)
    for attempt in range(1, 4):
        entry["next_attempt_at"] = None
        result = await scheduler._publish_entry(entry["entry_id"], notify=False)
        assert entry["attempts"] == attempt
        if attempt < 3:
            assert result["status"] == "scheduled"
            assert entry["next_attempt_at"] > datetime.now().timestamp()
            await scheduler._publish_entry(entry["entry_id"], notify=False)
            assert provider.await_count == attempt
        else:
            assert result["status"] == "failed"
    await scheduler._publish_entry(entry["entry_id"], notify=False)
    assert provider.await_count == 3


@pytest.mark.asyncio
async def test_permanent_error_waits_for_user_retry(monkeypatch):
    entry = release()
    monkeypatch.setattr(scheduler, "_flip_to_public", AsyncMock(return_value={"success": False, "retryable": False, "error": "Reconnect account"}))
    await scheduler._publish_entry(entry["entry_id"], notify=False)
    assert entry["status"] == "failed"
    assert entry["next_attempt_at"] is None
    assert scheduler.reschedule(entry["entry_id"], 99)["attempts"] == 0


def test_invalid_schedule_is_rejected():
    with pytest.raises(ValueError):
        release(float("nan"))
    entry = release()
    assert scheduler.reschedule(entry["entry_id"], float("inf")) is None


def test_old_failed_entries_do_not_silently_restart():
    entry = release()
    entry["status"] = "failed"
    scheduler.save_queue()
    scheduler.load_queue()
    assert scheduler.queue[entry["entry_id"]]["status"] == "failed"
