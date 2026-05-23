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
import os
import subprocess
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


def load_queue() -> None:
    """Load queue from disk into memory on startup."""
    global queue
    if QUEUE_FILE.exists():
        try:
            queue = {e["entry_id"]: e for e in json.loads(QUEUE_FILE.read_text())}
        except Exception:
            queue = {}
    else:
        queue = {}


def save_queue() -> None:
    """Persist in-memory queue to disk."""
    _DATA.mkdir(exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(list(queue.values()), indent=2))


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
    entry_id = str(uuid.uuid4())[:8]
    entry = {
        "entry_id":              entry_id,
        "job_id":                job_id,
        "title":                 title,
        "episode_number":        episode_number,
        "buzzsprout_episode_id": buzzsprout_episode_id,
        "scheduled_at":          scheduled_at,
        "scheduled_at_iso":      datetime.fromtimestamp(scheduled_at).isoformat(),
        "status":                "scheduled",
        "created_at":            datetime.now().isoformat(),
        "published_at":          None,
        "error":                 None,
        "youtube_url":           youtube_url,
        "source":                source,
    }
    queue[entry_id] = entry
    save_queue()
    return entry


def remove_from_queue(entry_id: str) -> bool:
    """Remove an entry. Returns True if found."""
    if entry_id in queue:
        del queue[entry_id]
        save_queue()
        return True
    return False


def reschedule(entry_id: str, new_ts: float) -> Optional[dict]:
    """Update the scheduled time of an entry."""
    entry = queue.get(entry_id)
    if not entry or entry["status"] not in ("scheduled",):
        return None
    entry["scheduled_at"]     = new_ts
    entry["scheduled_at_iso"] = datetime.fromtimestamp(new_ts).isoformat()
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
        return {"success": False, "error": "Buzzsprout credentials not configured"}

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
        return {"success": False, "error": f"Buzzsprout {status_code}: {body.strip()[:120]}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ── Scheduler loop ────────────────────────────────────────────────────────────

async def _publish_entry(entry_id: str) -> None:
    """Flip one entry from private → public and update its status."""
    entry = queue.get(entry_id)
    if not entry:
        return

    entry["status"] = "publishing"
    save_queue()

    result = await _flip_to_public(entry["buzzsprout_episode_id"])

    if result["success"]:
        entry["status"]       = "published"
        entry["published_at"] = datetime.now().isoformat()
        save_queue()
        # Telegram success alert
        try:
            from pipeline.telegram import send as tg_send
            await tg_send(
                f"🚀 *EP. {entry['episode_number']} published!*\n"
                f"_{entry['title']}_\n\n"
                f"Scheduled release fired at {entry['published_at'][:16]} ✅"
            )
        except Exception:
            pass
    else:
        entry["status"] = "failed"
        entry["error"]  = result.get("error", "Unknown error")
        save_queue()
        try:
            from pipeline.telegram import send as tg_send
            await tg_send(
                f"⚠️ *Scheduled publish failed — EP. {entry['episode_number']}*\n"
                f"`{entry['error']}`"
            )
        except Exception:
            pass


async def scheduler_loop() -> None:
    """Background task: check queue every 60 s, publish due entries."""
    load_queue()
    while True:
        await asyncio.sleep(60)
        now = datetime.now().timestamp()
        due = [
            eid for eid, e in queue.items()
            if e["status"] == "scheduled" and e["scheduled_at"] <= now
        ]
        for eid in due:
            asyncio.ensure_future(_publish_entry(eid))
