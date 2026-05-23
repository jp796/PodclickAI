"""
Buzzsprout Auto-Upload
Uses subprocess curl for the file upload — Python's LibreSSL TLS fingerprint
is blocked by Cloudflare/Buzzsprout, but curl (SecureTransport) works fine.

Supports:
  - upload_episode()      — upload MP3, optionally as private draft
  - flip_to_public()      — PATCH existing episode from private → public
  - upload_episode_sync() — sync wrapper for upload_episode
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

BUZZSPROUT_BASE = "https://www.buzzsprout.com/api"


async def upload_episode(
    mp3_path: str,
    title: str,
    description: str,
    episode_number: int,
    private: bool = False,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Upload MP3 to Buzzsprout.
    private=True  → upload as draft (for scheduling; flip later with flip_to_public)
    private=False → publish immediately (default)
    Returns: {success, url, episode_id, error}
    """
    def log(msg):
        print(msg)
        if progress_cb:
            progress_cb(msg)

    api_key    = os.getenv("BUZZSPROUT_API_KEY", "")
    podcast_id = os.getenv("BUZZSPROUT_PODCAST_ID", "")

    if not api_key or not podcast_id:
        return {"success": False, "error": "Buzzsprout credentials not set in .env", "url": None}

    mp3_file = Path(mp3_path)
    if not mp3_file.exists():
        return {"success": False, "error": f"MP3 not found: {mp3_path}", "url": None}

    file_size_mb = mp3_file.stat().st_size / 1_048_576
    ep_url       = f"{BUZZSPROUT_BASE}/{podcast_id}/episodes.json"
    private_str  = "true" if private else "false"

    log(f"Uploading {mp3_file.name} ({file_size_mb:.1f} MB) to Buzzsprout…")
    if private:
        log("Uploading as private draft (will publish at scheduled time)…")
    else:
        log("Sending to Buzzsprout… (may take a minute for large files)")

    cmd = [
        "curl", "-s", "-w", "\n__HTTP_STATUS__%{http_code}",
        "-H", f"Authorization: Token token={api_key}",
        "-F", f"title={title}",
        "-F", f"description={description or ''}",
        "-F", f"episode_number={episode_number}",
        "-F", f"private={private_str}",
        "-F", "explicit=false",
        "-F", f"audio_file=@{mp3_path};type=audio/mpeg",
        ep_url,
    ]

    loop = asyncio.get_event_loop()

    def do_curl():
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return result.stdout, result.returncode

    try:
        stdout, _ = await loop.run_in_executor(None, do_curl)

        body, _, status_str = stdout.rpartition("\n__HTTP_STATUS__")
        status_code = int(status_str.strip()) if status_str.strip().isdigit() else 0

        if status_code in (200, 201):
            ep      = json.loads(body.strip())
            ep_id   = ep.get("id", "")
            url_out = ep.get("audio_url") or f"https://www.buzzsprout.com/{podcast_id}/{ep_id}"
            if private:
                log(f"✓ Episode {ep_id} saved as private draft — will publish at scheduled time.")
            else:
                log(f"✓ Episode {ep_id} uploaded! Distributing to Spotify now…")
            return {"success": True, "url": url_out, "episode_id": str(ep_id), "error": None}
        else:
            err = f"Buzzsprout error {status_code}: {body.strip()[:200]}"
            log(f"✗ {err}")
            return {"success": False, "error": err, "url": None}

    except subprocess.TimeoutExpired:
        err = "Upload timed out — check your connection and try again."
        log(f"✗ {err}")
        return {"success": False, "error": err, "url": None}
    except Exception as exc:
        err = f"Upload failed: {exc}"
        log(f"✗ {err}")
        return {"success": False, "error": err, "url": None}


async def flip_to_public(buzzsprout_episode_id: str) -> dict:
    """
    PATCH an existing Buzzsprout episode to set private=false (publish it).
    Used by the scheduler to release a previously uploaded private draft.
    Returns: {success, error}
    """
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


def upload_episode_sync(mp3_path, title, description, episode_number,
                        private=False, progress_cb=None):
    return asyncio.run(upload_episode(
        mp3_path, title, description, episode_number,
        private=private, progress_cb=progress_cb,
    ))
