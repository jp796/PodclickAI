"""
Episode Release Scheduler

Manages the publish queue:
  - Queue entries stored in data/queue.json
  - Background asyncio loop checks every 60s
  - When scheduled_at <= now: PATCH Buzzsprout episode from private → public
  - Sends Telegram alerts on publish success / failure

Queue entry shape:
  {
    "entry_id":             str (uuid),
    "job_id":               str,
    "title":                str,
    "episode_number":       int,
    "buzzsprout_episode_id": str,
    "scheduled_at":         float (unix timestamp),
    "scheduled_at_iso":     str  (ISO-8601, for display),
    "status":               "scheduled" | "publishing" | "published" | "failed",
    "created_at":           str  (ISO-8601),
    "published_at":         str | None,
    "error":                str | None,
    "youtube_url":          str | None,
    "source":               "manual" | "automation",
  }
"""

import asyncio
import json
import logging
import math
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Resolve relative to this file so imports work from any working dir
_BASE   = Path(__file__).parent.parent
_DATA   = _BASE / "data"
QUEUE_FILE = _DATA / "queue.json"

BUZZSPROUT_BASE = "https://www.buzzsprout.com/api"

# ── In-memory queue store ─────────────────────────────────────────────────────
queue: dict[str, dict] = {}   # entry_id → entry
_inflight = set()
MAX_ATTEMPTS = 3
RETRY_DELAYS = (60, 300)
_logger = logging.getLogger("podclick.scheduler")


def load_queue() -> None:
    """Load queue from disk into memory on startup."""
    if QUEUE_FILE.exists():
        try:
            entries = json.loads(QUEUE_FILE.read_text())
            loaded = {e["entry_id"]: e for e in entries}
        except Exception as exc:
            # Never replace an unreadable durable queue with an empty one.
            raise RuntimeError("Release queue could not be read; original file preserved") from exc
    else:
        loaded = {}
    queue.clear()
    queue.update(loaded)
    recovered = False
    for entry in queue.values():
        if entry.get("status") == "publishing":
            # This operation is an idempotent PATCH, never another upload.
            entry["status"] = "scheduled" if entry.get("attempts", 0) < MAX_ATTEMPTS else "failed"
            entry["error"] = "Release interrupted by a restart; checking again." if entry["status"] == "scheduled" else "Release interrupted after the final attempt. Review before retrying."
            recovered = True
    if recovered:
        save_queue()


def save_queue() -> None:
    """Persist in-memory queue to disk."""
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=str(QUEUE_FILE.parent), prefix=".queue-", suffix=".tmp", delete=False) as handle:
        temp_path = Path(handle.name)
        try:
            json.dump(list(queue.values()), handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    try:
        os.replace(str(temp_path), str(QUEUE_FILE))
    finally:
        temp_path.unlink(missing_ok=True)


def add_to_queue(
    job_id: str,
    title: str,
    episode_number: int,
    buzzsprout_episode_id: str,
    scheduled_at: float,        # Unix timestamp
    youtube_url: Optional[str] = None,
    source: str = "manual",
) -> dict:
    """Add or update a queue entry. Returns the entry."""
    if not math.isfinite(scheduled_at) or scheduled_at <= 0:
        raise ValueError("A valid release time is required")
    if not buzzsprout_episode_id:
        raise ValueError("A Buzzsprout episode ID is required")
    for existing in queue.values():
        if existing.get("buzzsprout_episode_id") == str(buzzsprout_episode_id):
            if existing.get("status") in ("published", "publishing"):
                return existing
            existing.update(title=title, episode_number=episode_number, youtube_url=youtube_url, source=source)
            reschedule(existing["entry_id"], scheduled_at)
            return existing
    entry_id = str(uuid.uuid4())[:8]
    entry = {
        "entry_id":              entry_id,
        "job_id":                job_id,
        "title":                 title,
        "episode_number":        episode_number,
        "buzzsprout_episode_id": str(buzzsprout_episode_id),
        "scheduled_at":          scheduled_at,
        "scheduled_at_iso":      datetime.fromtimestamp(scheduled_at).isoformat(),
        "status":                "scheduled",
        "created_at":            datetime.now().isoformat(),
        "published_at":          None,
        "error":                 None,
        "youtube_url":           youtube_url,
        "source":                source,
        "attempts":              0,
        "next_attempt_at":       None,
    }
    queue[entry_id] = entry
    save_queue()
    return entry


def remove_from_queue(entry_id: str) -> bool:
    """Remove an entry. Returns True if found."""
    if entry_id in queue and queue[entry_id].get("status") != "publishing" and entry_id not in _inflight:
        del queue[entry_id]
        save_queue()
        return True
    return False


def reschedule(entry_id: str, new_ts: float) -> Optional[dict]:
    """Update the scheduled time of an entry."""
    entry = queue.get(entry_id)
    if not entry or entry["status"] not in ("scheduled", "failed") or entry_id in _inflight:
        return None
    if not math.isfinite(new_ts) or new_ts <= 0:
        return None
    entry["scheduled_at"]     = new_ts
    entry["scheduled_at_iso"] = datetime.fromtimestamp(new_ts).isoformat()
    entry.update(status="scheduled", attempts=0, next_attempt_at=None, error=None)
    save_queue()
    return entry


def list_queue() -> list[dict]:
    """Return all entries sorted by scheduled_at."""
    return sorted(queue.values(), key=lambda e: e["scheduled_at"])


# ── Buzzsprout PATCH — flip private → public ──────────────────────────────────

async def _flip_to_public(buzzsprout_episode_id: str) -> dict:
    """PATCH the Buzzsprout episode to set private=false (publish it)."""
    api_key    = os.getenv("BUZZSPROUT_API_KEY", "")
    podcast_id = os.getenv("BUZZSPROUT_PODCAST_ID", "")

    if not api_key or not podcast_id:
        return {"success": False, "error": "Buzzsprout credentials not configured", "retryable": False}

    ep_url = f"{BUZZSPROUT_BASE}/{podcast_id}/episodes/{buzzsprout_episode_id}.json"
    cmd = [
        "curl", "-s", "-w", "\n__HTTP_STATUS__%{http_code}",
        "-X", "PATCH",
        "-H", f"Authorization: Token token={api_key}",
        "-H", "Content-Type: application/json",
        "-d", '{"private": false}',
        ep_url,
    ]

    loop = asyncio.get_event_loop()

    def do_patch():
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout

    try:
        stdout = await loop.run_in_executor(None, do_patch)
        body, _, status_str = stdout.rpartition("\n__HTTP_STATUS__")
        status_code = int(status_str.strip()) if status_str.strip().isdigit() else 0
        if status_code in (200, 201):
            return {"success": True}
        return {"success": False, "error": f"Buzzsprout {status_code}: {body.strip()[:120]}", "retryable": status_code in (0, 408, 429) or status_code >= 500}
    except Exception as exc:
        return {"success": False, "error": str(exc), "retryable": True}


# ── Scheduler loop ────────────────────────────────────────────────────────────

async def _publish_entry(entry_id: str, force: bool = False, notify: bool = True) -> dict:
    """Claim one release before awaiting; retry only the idempotent public flip."""
    entry = queue.get(entry_id)
    if not entry:
        return {"success": False, "status": "missing", "error": "Entry not found"}
    if entry_id in _inflight or entry.get("status") == "publishing":
        return {"success": False, "status": "busy", "error": "Release already in progress"}
    if entry.get("status") == "published":
        return {"success": True, "status": "published"}
    now = datetime.now().timestamp()
    if not force and (entry.get("status") != "scheduled" or entry["scheduled_at"] > now or (entry.get("next_attempt_at") or 0) > now):
        return {"success": False, "status": "not_due", "error": "Release is not due"}
    _inflight.add(entry_id)
    try:
        if force and entry.get("status") == "failed":
            entry["attempts"] = 0
        entry.update(status="publishing", attempts=entry.get("attempts", 0) + 1, next_attempt_at=None)
        save_queue()
        try:
            result = await _flip_to_public(entry["buzzsprout_episode_id"])
        except Exception as exc:
            result = {"success": False, "error": str(exc), "retryable": True}
        if result.get("success"):
            entry.update(status="published", published_at=datetime.now(timezone.utc).isoformat(), error=None)
        else:
            retry = result.get("retryable", False) and entry["attempts"] < MAX_ATTEMPTS
            entry.update(status="scheduled" if retry else "failed", error=result.get("error", "Unknown error"))
            if retry:
                entry["next_attempt_at"] = datetime.now().timestamp() + RETRY_DELAYS[entry["attempts"] - 1]
        save_queue()
        if notify and entry["status"] in ("published", "failed"):
            try:
                from pipeline.telegram import send as tg_send
                message = (f"🚀 EP. {entry['episode_number']} published!\n{entry['title']}" if result.get("success") else f"⚠️ Release needs attention — EP. {entry['episode_number']}\n{entry['error']}")
                await tg_send(message)
            except Exception:
                _logger.warning("Could not send release notification")
        return dict(result, status=entry["status"], next_attempt_at=entry.get("next_attempt_at"))
    finally:
        _inflight.discard(entry_id)


async def run_due_releases() -> None:
    """One scheduler pass. The app intentionally runs a single scheduler worker."""
    now = datetime.now().timestamp()
    due = [eid for eid, entry in queue.items() if entry.get("status") == "scheduled" and entry["scheduled_at"] <= now and (entry.get("next_attempt_at") or 0) <= now]
    if due:
        await asyncio.gather(*(_publish_entry(eid) for eid in due))


async def scheduler_loop() -> None:
    """Background task: check queue every 60 s, publish due entries."""
    load_queue()
    while True:
        await asyncio.sleep(60)
        if os.getenv("PODCLICK_AUTOMATION_DISABLED", "").lower() in ("1", "true", "yes"):
            continue
        try:
            await run_due_releases()
        except Exception:
            _logger.exception("Release scheduler pass failed; will check again next minute")
