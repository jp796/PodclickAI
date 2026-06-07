"""
Podcast Studio — main FastAPI application.

Endpoints:
  GET  /                         Serve the frontend
  POST /api/process              Start full processing pipeline
  GET  /api/jobs/{job_id}        Get job status
  POST /api/upload               Upload now (publish immediately)
  POST /api/schedule             Upload as private + add to release queue
  GET  /api/queue                List release queue
  DELETE /api/queue/{entry_id}   Remove from queue
  PATCH /api/queue/{entry_id}    Reschedule an entry
  POST /api/queue/{entry_id}/publish  Publish now (bypass schedule)
  GET  /api/episodes             List episode history
  WS   /ws/{job_id}              Real-time progress updates
"""

import asyncio
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional, List

import aiofiles
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI(title="Podcast Studio")

# ── Phase 1 routers ───────────────────────────────────────────────────────────
from routers.foundation import router as foundation_router  # noqa: E402
from routers.blueprint import router as blueprint_router  # noqa: E402

app.include_router(foundation_router, prefix="/api/foundation", tags=["Foundation"])
app.include_router(blueprint_router, prefix="/api/blueprint", tags=["Blueprint"])


@app.get("/health")
async def health_check():
    return {"status": "ok"}


async def _nightly_foundation_recompute() -> None:
    """
    Nightly cron job — recompute Foundation voice cohesion score.

    Runs at 03:00 America/Chicago (one hour before Brick's planning loop)
    so the planning context always has a fresh score.

    Skips recompute if a score was already computed in the last 23 hours
    (e.g., user triggered manual compute during intake today).
    Only operates on the default location (single-tenant beta setup).
    """
    import datetime as _dt
    from db.engine import async_session as _async_session
    from sqlalchemy import text as _text
    from config import settings as _settings
    from services.foundation import calculate_foundation_score as _compute

    location_id = _settings.titan_location_id
    if not location_id:
        print("[foundation.cron] No TITAN_LOCATION_ID configured — skipping recompute")
        return

    try:
        async with _async_session() as session:
            # Check when the score was last computed
            row = await session.execute(
                _text(
                    "SELECT computed_at FROM foundation_scores "
                    "WHERE location_id = :loc "
                    "ORDER BY computed_at DESC LIMIT 1"
                ),
                {"loc": location_id},
            )
            last = row.scalar()
            cutoff = _dt.datetime.utcnow() - _dt.timedelta(hours=23)
            if last and last > cutoff:
                print(
                    f"[foundation.cron] Score fresh (computed {last.isoformat()}) — skipping"
                )
                return

            score = await _compute(session=session, location_id=location_id)
            print(
                f"[foundation.cron] Recomputed score: {round(score * 100, 1)}% "
                f"for location {location_id}"
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[foundation.cron] Nightly recompute failed — {exc}")


@app.on_event("startup")
async def _startup():
    """Start the release scheduler + uploads sweep background loops."""
    from pipeline.scheduler import scheduler_loop
    asyncio.ensure_future(scheduler_loop())
    # Sweep stale data/uploads/<job_id>/ dirs (default >7 days old)
    # every 6 hours. Keeps disk usage bounded without disturbing
    # recent failed jobs that a user might still retry.
    asyncio.ensure_future(_uploads_sweep_loop())

    # ── Brick daily planning cron (Phase 3A) ──────────────────────────────────
    # Fires at 04:00 America/Chicago every day (user.timezone default).
    # Uses AsyncIOScheduler so async jobs run on the existing event loop.
    try:
        import pytz
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from services.brick_agent import run_planning_for_default_location

        _brick_scheduler = AsyncIOScheduler()
        _brick_scheduler.add_job(
            run_planning_for_default_location,
            CronTrigger(hour=4, minute=0, timezone=pytz.timezone("America/Chicago")),
            id="brick_daily_planning",
            replace_existing=True,
            misfire_grace_time=600,  # tolerate up to 10-min delay on cold start
        )

        # ── Foundation score nightly recompute (Step 1, Stage 2) ──────────────
        # Fires at 03:00 America/Chicago — before Brick's planning loop at 04:00
        # so the planning context always has a fresh score.
        # Only recomputes if the last score is >23h old (avoids redundant work
        # on days where the user triggered a manual compute via intake).
        _brick_scheduler.add_job(
            _nightly_foundation_recompute,
            CronTrigger(hour=3, minute=0, timezone=pytz.timezone("America/Chicago")),
            id="foundation_nightly_score",
            replace_existing=True,
            misfire_grace_time=600,
        )

        _brick_scheduler.start()
        print("[brick.cron] Daily planning cron registered — fires 04:00 America/Chicago")
        print("[foundation.cron] Nightly score recompute registered — fires 03:00 America/Chicago")
    except Exception as _brick_cron_err:
        print(f"[brick.cron] WARNING: Could not register cron — {_brick_cron_err}")

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
# ── Scout / virality constants ────────────────────────────────────────────────
# score = video_views / channel_subscriber_count (both from YouTube Data API).
# A score >= threshold means the video over-performed relative to the channel's
# install base — strong signal it punched above weight.
VIRALITY_POPULAR_THRESHOLD: float = 1.5

# Referer accepted by the YouTube Data API key (key has HTTP referer restriction).
# Must be present on every httpx call to googleapis.com/youtube/v3.
YT_API_REFERER = "http://localhost:8765/"

DATA_DIR     = BASE_DIR / "data"
JOBS_DIR     = DATA_DIR / "jobs"
EPISODES_FILE = DATA_DIR / "episodes.json"
FRONTEND_DIR  = BASE_DIR / "frontend"
STATIC_DIR    = FRONTEND_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

# Serve brand assets from /static/
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Serve shared design system CSS
@app.get("/podclick-design.css")
async def serve_design_css():
    from fastapi.responses import FileResponse as _FR2
    return _FR2(str(FRONTEND_DIR / "podclick-design.css"), media_type="text/css")

LIBRARY_DIR    = DATA_DIR / "library"
LIBRARY_FILE   = DATA_DIR / "library.json"
PROFILES_FILE  = DATA_DIR / "profiles.json"
SPONSORS_FILE  = DATA_DIR / "sponsors.json"
GUESTS_FILE    = DATA_DIR / "guests.json"

# Persistent uploads dir — replaces TMPDIR for the /api/run upload
# loop. macOS rotates /var/folders/.../T/ (the default tempfile root)
# and a sibling job's `finally:` cleanup used to unlink any clip with
# a matching path. Owning the lifecycle here means re-runs after a
# failure don't require re-uploading, and the clips outlive a server
# restart between upload and assembly. One subdir per job_id so a
# whole job is easy to inspect or sweep later.
UPLOADS_DIR    = DATA_DIR / "uploads"
AI_PERSONA_DIR = DATA_DIR / "ai_persona"
AI_PERSONA_FILE = DATA_DIR / "ai_persona.json"

DATA_DIR.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)
LIBRARY_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)
AI_PERSONA_DIR.mkdir(exist_ok=True)


# ── Uploads sweep ──────────────────────────────────────────────────────────────
# Periodically drop data/uploads/<job_id>/ subdirs older than the
# retention window. Bounds disk growth without disturbing recent
# failed jobs (so retry-without-reupload still works).
#
# Retention is intentionally generous (7 days) so a TC who comes
# back to a stuck job mid-week can still re-run it. Override at
# runtime by setting PODCAST_UPLOADS_RETENTION_DAYS.

import shutil

UPLOADS_RETENTION_DAYS = int(os.environ.get("PODCAST_UPLOADS_RETENTION_DAYS", "7"))
UPLOADS_SWEEP_INTERVAL_S = 6 * 60 * 60  # 6 h


def _sweep_old_uploads(max_age_days: int = UPLOADS_RETENTION_DAYS) -> int:
    """Delete data/uploads/<job_id>/ subdirs whose mtime is older than
    the cutoff. Returns the count removed. Uses dir mtime so a job
    that recently re-touched files (retry, manual edit) survives.

    Best-effort: any failure on a single dir is logged and skipped —
    one stuck path shouldn't take the whole sweep down.
    """
    if not UPLOADS_DIR.exists():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for entry in UPLOADS_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=False)
                removed += 1
        except Exception as e:
            print(f"[uploads-sweep] skip {entry.name}: {e}", flush=True)
    return removed


async def _uploads_sweep_loop():
    """Background task — runs once at startup then every 6 hours."""
    # Small startup delay so we don't race with other init work.
    await asyncio.sleep(5)
    while True:
        try:
            removed = _sweep_old_uploads()
            if removed:
                print(
                    f"[uploads-sweep] removed {removed} dir(s) older than "
                    f"{UPLOADS_RETENTION_DAYS}d",
                    flush=True,
                )
        except Exception as e:
            print(f"[uploads-sweep] loop error: {e}", flush=True)
        await asyncio.sleep(UPLOADS_SWEEP_INTERVAL_S)


# ── Guest helpers ─────────────────────────────────────────────────────────────
def _load_guests() -> list:
    if GUESTS_FILE.exists():
        return json.loads(GUESTS_FILE.read_text())
    return []

def _save_guests(items: list):
    GUESTS_FILE.write_text(json.dumps(items, indent=2))


# ── AI Persona helpers ───────────────────────────────────────────────────────
def _load_ai_persona() -> dict:
    if AI_PERSONA_FILE.exists():
        return json.loads(AI_PERSONA_FILE.read_text())
    return {"photos": []}


def _save_ai_persona(store: dict):
    AI_PERSONA_FILE.write_text(json.dumps(store, indent=2))


def _persona_public(photo: dict) -> dict:
    item = dict(photo)
    item["url"] = f"/api/yt/ai-persona/photos/{photo['id']}"
    return item


# ── Sponsor helpers ────────────────────────────────────────────────────────────
def _load_sponsors() -> list:
    if SPONSORS_FILE.exists():
        return json.loads(SPONSORS_FILE.read_text())
    return []

def _save_sponsors(items: list):
    SPONSORS_FILE.write_text(json.dumps(items, indent=2))


# ── Profile helpers ────────────────────────────────────────────────────────────
def _load_profiles_store() -> dict:
    """Returns {"active_id": str|None, "profiles": []}"""
    if PROFILES_FILE.exists():
        return json.loads(PROFILES_FILE.read_text())
    return {"active_id": None, "profiles": []}

def _save_profiles_store(store: dict):
    PROFILES_FILE.write_text(json.dumps(store, indent=2))

def _get_active_profile() -> Optional[dict]:
    """Return the currently active profile dict, or None."""
    store = _load_profiles_store()
    aid   = store.get("active_id")
    if not aid:
        return None
    return next((p for p in store["profiles"] if p["id"] == aid), None)


# ── Library helpers ────────────────────────────────────────────────────────────
def _load_library() -> list:
    if LIBRARY_FILE.exists():
        return json.loads(LIBRARY_FILE.read_text())
    return []

def _save_library(items: list):
    LIBRARY_FILE.write_text(json.dumps(items, indent=2))


# ── Episode counter ────────────────────────────────────────────────────────────
def _load_episodes() -> list:
    if EPISODES_FILE.exists():
        return json.loads(EPISODES_FILE.read_text())
    return []

def _save_episodes(episodes: list):
    EPISODES_FILE.write_text(json.dumps(episodes, indent=2))

def _next_episode_number() -> int:
    episodes = _load_episodes()
    if not episodes:
        return 1
    return max(e.get("episode_number", 0) for e in episodes) + 1


# ── In-memory job store ────────────────────────────────────────────────────────
jobs: dict[str, dict] = {}
job_ws_queues: dict[str, asyncio.Queue] = {}


def _persist_job(job_id: str):
    """Save a completed job to disk so it survives server restarts."""
    job = jobs.get(job_id)
    if not job:
        return
    job_file = JOBS_DIR / f"{job_id}.json"
    try:
        payload = {k: v for k, v in job.items() if k != "words"}
        job_file.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def _load_persisted_jobs():
    """Load previously completed jobs from disk into memory on startup."""
    for jf in JOBS_DIR.glob("*.json"):
        try:
            data = json.loads(jf.read_text())
            jid  = data.get("job_id", jf.stem)
            if jid not in jobs:
                data.setdefault("words", [])
                jobs[jid] = data
        except Exception:
            pass


_load_persisted_jobs()


def _get_job(job_id: str) -> Optional[dict]:
    return jobs.get(job_id)


async def _send_progress(job_id: str, message: str, step: Optional[str] = None):
    jobs[job_id]["log"].append(message)
    if step:
        jobs[job_id]["step"] = step
    if job_id in job_ws_queues:
        await job_ws_queues[job_id].put({
            "type":    "progress",
            "message": message,
            "step":    step or jobs[job_id].get("step", ""),
            "status":  jobs[job_id]["status"],
        })


async def _send_result(job_id: str, result: dict):
    jobs[job_id].update(result)
    if job_id in job_ws_queues:
        await job_ws_queues[job_id].put({
            "type": "result",
            "job":  {k: v for k, v in jobs[job_id].items() if k != "words"},
        })


# ── Processing pipeline ────────────────────────────────────────────────────────
async def run_pipeline(
    job_id: str,
    clips: list[dict],          # [{"path": str, "type": str, "filename": str, "is_image": bool}]
    model_size: str,
    podcast_name: str,
    studio_mode: str = "audio",         # "audio" | "video" | "both"
    subtitle_style: str = "reels",      # "reels" | "youtube" | "clean"
    episode_number_override: int = 0,   # 0 = auto-increment
):
    from pipeline.transcribe import transcribe
    from pipeline.audio      import process_audio
    from pipeline.assemble   import assemble_clips
    from pipeline.content    import generate_content

    job     = jobs[job_id]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    loop    = asyncio.get_event_loop()

    def mk_cb(step=""):
        """
        Create a thread-safe progress callback safe to call from executor threads.
        Uses run_coroutine_threadsafe — the correct way to schedule a coroutine
        from a non-async thread.
        """
        def cb(msg):
            asyncio.run_coroutine_threadsafe(
                _send_progress(job_id, msg, step or None),
                loop,
            )
        return cb

    try:
        job["status"] = "processing"
        multi_clip = len(clips) > 1

        # ── Identify main clip for transcription ──────────────────────────────
        main_clips = [c for c in clips if c["type"] == "main"]
        if not main_clips:
            raise ValueError("No 'main' podcast clip found.")
        main_clip_path = main_clips[0]["path"]

        # ── Step 1: Transcribe main clip (get word timestamps for filler cuts) ─
        await _send_progress(job_id, "Transcribing main podcast audio...", "transcribing")

        def do_transcribe():
            return transcribe(
                main_clip_path,
                model_size=model_size,
                progress_cb=mk_cb(),
            )

        transcript_result = await loop.run_in_executor(None, do_transcribe)
        job["transcript"] = transcript_result["text"]
        job["words"]      = transcript_result["words"]
        job["segments"]   = transcript_result.get("segments", [])
        job["language"]   = transcript_result["language"]
        await _send_progress(
            job_id,
            f"Transcription complete ({len(transcript_result['words'])} words).",
            "transcribed",
        )

        # ── Step 2: Audio processing ───────────────────────────────────────────
        await _send_progress(job_id, "Processing audio...", "processing_audio")

        if multi_clip:
            # Multi-clip: assemble + process together
            await _send_progress(job_id, "Assembling clips: intro → main → commercial → outro...", "assembling")
            def do_assemble():
                return assemble_clips(
                    clips=clips,
                    output_dir=str(job_dir),
                    word_timestamps=transcript_result["words"],
                    progress_cb=mk_cb("processing_audio"),
                )
            audio_result = await loop.run_in_executor(None, do_assemble)
            job["commercial_inserted_at"] = audio_result.get("commercial_inserted_at")
            job["has_intro"]      = audio_result.get("has_intro", False)
            job["has_commercial"] = audio_result.get("has_commercial", False)
            job["has_outro"]      = audio_result.get("has_outro", False)
        else:
            # Single clip: standard pipeline
            def do_audio():
                return process_audio(
                    main_clip_path,
                    str(job_dir),
                    word_timestamps=transcript_result["words"],
                    progress_cb=mk_cb("processing_audio"),
                )
            audio_result = await loop.run_in_executor(None, do_audio)

        job["mp3_path"]       = audio_result["output_mp3"]
        job["fillers_removed"] = audio_result["fillers_removed"]
        job["duration_saved"]  = audio_result["duration_saved"]
        job["final_duration"]  = audio_result["final_duration"]

        saved_s = audio_result["duration_saved"]
        fillers = audio_result["fillers_removed"]
        await _send_progress(
            job_id,
            f"Audio ready. {fillers} filler words removed ({saved_s:.1f}s saved).",
            "audio_done",
        )

        # ── Step 3: Generate content ───────────────────────────────────────────
        await _send_progress(job_id, "Generating title and description with GPT-4o...", "generating_content")

        active_profile = _get_active_profile()

        def do_content():
            return generate_content(
                transcript_result["text"],
                podcast_name=podcast_name,
                segments=transcript_result.get("segments", []),
                progress_cb=mk_cb(),
                profile=active_profile,
            )

        content = await loop.run_in_executor(None, do_content)
        job["title"]          = content["title"]
        job["description"]    = content["description"]
        job["links"]          = content.get("links", [])
        job["episode_number"] = episode_number_override if episode_number_override > 0 else _next_episode_number()

        await _send_progress(job_id, f'Content ready: "{content["title"]}"', "content_done")

        # ── Step 4: Video pipeline (video or both mode) ───────────────────────
        if studio_mode in ("video", "both"):
            await _send_progress(job_id, "Starting video processing…", "processing_video")

            from pipeline.video import (
                cut_video_segments, normalize_video_audio,
                assemble_video_clips, burn_subtitles, segments_to_srt,
                image_to_video_segment,
            )

            IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

            # 4a. Build video clip list — convert images to MP4 on the fly
            video_clips_ordered = []
            for clip in clips:
                ext = Path(clip["path"]).suffix.lower()
                if clip.get("is_image") or ext in IMAGE_EXTS:
                    img_out = str(job_dir / f"img_{clip['type']}.mp4")
                    await _send_progress(job_id, f"Converting image → video: {clip['filename']}", None)
                    def do_img(cp=clip, out=img_out):
                        return image_to_video_segment(cp["path"], out, duration=5.0)
                    ok = await loop.run_in_executor(None, do_img)
                    if ok:
                        video_clips_ordered.append({"path": img_out, "type": clip["type"]})
                    else:
                        await _send_progress(job_id, f"Warning: could not convert image {clip['filename']}", None)
                elif ext in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
                    video_clips_ordered.append({"path": clip["path"], "type": clip["type"]})
                # audio-only clips (mp3/wav) are skipped in video mode

            main_video_clips = [c for c in video_clips_ordered if c["type"] == "main"]

            if not main_video_clips:
                await _send_progress(job_id, "⚠️ No main video file found — skipping video pipeline.", None)
            else:
                main_video_path = main_video_clips[0]["path"]

                # 4b. Build filler-cut segments from Whisper word timestamps
                filler_segs = [
                    {"start": w["start"], "end": w["end"]}
                    for w in job.get("words", [])
                    if w.get("filler")
                ]

                # 4c. Cut filler words from main video
                cut_video_path = str(job_dir / "main_cut.mp4")
                await _send_progress(job_id, f"Cutting {len(filler_segs)} filler segments from video…", "cutting_video")
                def do_cut():
                    return cut_video_segments(main_video_path, filler_segs, cut_video_path)
                ok = await loop.run_in_executor(None, do_cut)
                if not ok:
                    await _send_progress(job_id, "Warning: video cut failed — using original.", None)
                    cut_video_path = main_video_path

                # Replace main clip with cut version
                video_clips_ordered = [c for c in video_clips_ordered if c["type"] != "main"]
                video_clips_ordered.append({"path": cut_video_path, "type": "main"})

                # Sort: intro → main → commercial → outro
                _order = {"intro": 0, "main": 1, "commercial": 2, "outro": 3}
                video_clips_ordered.sort(key=lambda c: _order.get(c["type"], 99))

                # 4d. Assemble clips (only if multiple exist)
                if len(video_clips_ordered) > 1:
                    assembled_path = str(job_dir / "assembled.mp4")
                    await _send_progress(job_id, "Assembling video clips…", "assembling_video")
                    def do_assemble_v():
                        return assemble_video_clips(video_clips_ordered, assembled_path)
                    ok = await loop.run_in_executor(None, do_assemble_v)
                    if not ok:
                        assembled_path = cut_video_path
                else:
                    assembled_path = cut_video_path

                # 4e. Normalize audio on assembled video
                normalized_video_path = str(job_dir / "normalized_video.mp4")
                await _send_progress(job_id, "Normalizing video audio…", "normalizing_video")
                def do_norm_v():
                    return normalize_video_audio(assembled_path, normalized_video_path)
                ok = await loop.run_in_executor(None, do_norm_v)
                if not ok:
                    normalized_video_path = assembled_path

                # 4f. Generate SRT from Whisper segments
                srt_path = str(job_dir / "subtitles.srt")
                def do_srt():
                    return segments_to_srt(job.get("segments", []), srt_path)
                await loop.run_in_executor(None, do_srt)

                # 4g. Burn subtitles
                subtitled_path = str(job_dir / "final_video.mp4")
                await _send_progress(job_id, f"Burning {subtitle_style} subtitles…", "burning_subtitles")
                def do_burn():
                    return burn_subtitles(normalized_video_path, srt_path, subtitled_path, style=subtitle_style)
                ok = await loop.run_in_executor(None, do_burn)
                if not ok:
                    subtitled_path = normalized_video_path

                job["srt_path"] = srt_path

                # 4h. B-roll composite (non-fatal)
                pexels_key  = os.getenv("PEXELS_API_KEY", "")
                openai_key  = os.getenv("OPENAI_API_KEY", "")
                broll_segs  = job.get("segments", [])
                if pexels_key and broll_segs:
                    from pipeline.broll import add_broll
                    broll_out = str(job_dir / "broll_video.mp4")
                    broll_cache = str(job_dir / "broll_cache")
                    await _send_progress(job_id, "Adding B-roll cutaways…", "broll")
                    def do_broll():
                        return add_broll(
                            main_video=subtitled_path,
                            segments=broll_segs,
                            output_path=broll_out,
                            openai_key=openai_key,
                            pexels_key=pexels_key,
                            cache_dir=broll_cache,
                            progress_cb=None,
                        )
                    broll_result = await loop.run_in_executor(None, do_broll)
                    if broll_result != subtitled_path:
                        subtitled_path = broll_result
                        job["broll_applied"] = True
                        await _send_progress(job_id, "B-roll composite complete ✓", "broll")

                job["mp4_path"] = subtitled_path
                await _send_progress(job_id, f"Video ready — {Path(subtitled_path).name}", "video_done")

        job["status"] = "ready"
        _persist_job(job_id)

        # ── Phase 5: Auto-create Project row on pipeline completion ───────────
        # Bridge: existing file-based pipeline → Project state machine.
        # Project status → 'review' means the Ship It wizard is available.
        try:
            await _auto_create_project(job_id, job)
        except Exception as _proj_err:
            # Non-fatal — pipeline completed fine, Project creation is best-effort
            import logging
            logging.getLogger("podclick.projects").warning(
                "auto_create_project failed for job %s: %s", job_id, _proj_err
            )

        await _send_result(job_id, {"status": "ready"})

    except Exception as e:
        job["status"] = "error"
        job["error"]  = str(e)
        await _send_progress(job_id, f"Error: {e}", "error")
        await _send_result(job_id, {"status": "error", "error": str(e)})
    finally:
        # Cleanup policy (changed Nov 2026): only delete the upload
        # files on SUCCESS. Keeping them on error lets the user retry
        # the same job (re-trigger from the UI) without re-uploading
        # the intro / commercial / outro — which is what bit us when
        # the tmp file disappeared between upload and assembly.
        # The whole job_upload_dir (data/uploads/<job_id>/) is the
        # unit of ownership; a future periodic sweep can purge dirs
        # older than N days. See UPLOADS_DIR comment near the top.
        if job.get("status") == "ready":
            for clip in clips:
                try: os.unlink(clip["path"])
                except: pass
            # Best-effort: drop the now-empty job upload dir too.
            try:
                upload_dir = UPLOADS_DIR / job_id
                if upload_dir.is_dir() and not any(upload_dir.iterdir()):
                    upload_dir.rmdir()
            except: pass


# ── Phase 5 — Project state machine helpers ───────────────────────────────────

_PROJECT_STATUS_TRANSITIONS = {
    "draft":          ["recording_done", "failed"],
    "recording_done": ["processing", "failed"],
    "processing":     ["review", "failed"],
    "review":         ["scheduled", "failed"],
    "scheduled":      ["closing", "failed"],
    "closing":        ["closed", "failed"],
    "closed":         [],
    "failed":         ["draft"],  # allow reset to retry
}


async def _auto_create_project(job_id: str, job: dict) -> None:
    """
    Called by run_pipeline on job completion.
    Creates a Project row in Postgres if one doesn't already exist for this job_id.
    Status is set to 'review' — the Ship It wizard becomes available.
    """
    from db.engine import async_session as _async_session
    from db.models import Project
    from sqlalchemy import select

    async with _async_session() as session:
        # Idempotent — skip if already created (e.g., retry scenario)
        existing = await session.execute(
            select(Project).where(Project.job_id == job_id)
        )
        if existing.scalar_one_or_none():
            return

        # Resolve default location_id (single-tenant for now)
        from services.foundation import _get_default_location_id
        location_id = await _get_default_location_id()
        if not location_id:
            return  # no location configured yet — skip gracefully

        project = Project(
            location_id=location_id,
            job_id=job_id,
            type="episode",
            title=job.get("title") or "Untitled Episode",
            description=job.get("description"),
            episode_number=job.get("episode_number"),
            mp3_url=job.get("mp3_path"),
            transcript=job.get("transcript"),
            status="review",
            wizard_step=1,
            audio_assembly={
                "has_intro": job.get("has_intro", False),
                "has_commercial": job.get("has_commercial", False),
                "has_outro": job.get("has_outro", False),
                "commercial_inserted_at": job.get("commercial_inserted_at"),
            },
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

        # Audit log
        try:
            import json as _json_audit, uuid as _uuid_audit
            import sqlalchemy as _sa_audit
            async with _async_session() as audit_session:
                await audit_session.execute(
                    _sa_audit.text("""
                        INSERT INTO audit_log (id, location_id, action, payload, created_at)
                        VALUES (:id, :loc_id, 'project.created', CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {"id": str(_uuid_audit.uuid4()), "loc_id": str(location_id),
                     "payload": _json_audit.dumps({"job_id": job_id, "project_id": str(project.id)})},
                )
                await audit_session.commit()
        except Exception:
            pass  # audit log is best-effort


def _project_to_dict(project) -> dict:
    """Serialize a Project ORM row to a JSON-safe dict."""
    return {
        "id": str(project.id),
        "location_id": str(project.location_id),
        "job_id": project.job_id,
        "type": project.type,
        "title": project.title,
        "description": project.description,
        "episode_number": project.episode_number,
        "status": project.status,
        "wizard_step": project.wizard_step,
        "recording_path": project.recording_path,
        "transcription_status": project.transcription_status,
        "mp3_url": project.mp3_url,
        "transcript": project.transcript,
        "show_notes": project.show_notes,
        "audio_assembly": project.audio_assembly or {},
        "sponsor_placement": project.sponsor_placement,
        "guest_ids": project.guest_ids or [],
        "closing_scheduled_at": project.closing_scheduled_at.isoformat() if project.closing_scheduled_at else None,
        "closed_at": project.closed_at.isoformat() if project.closed_at else None,
        "guest_email_sent_at": project.guest_email_sent_at.isoformat() if project.guest_email_sent_at else None,
        # Distribution links (Phase B)
        "buzzsprout_url": project.buzzsprout_url,
        "buzzsprout_episode_id": project.buzzsprout_episode_id,
        "youtube_url": project.youtube_url,
        "youtube_video_id": project.youtube_video_id,
        # Strip large word/segment arrays from API response — frontend never needs them.
        # Ship It reads them directly from the DB in the background task.
        "legacy_metadata": _strip_heavy_metadata(project.legacy_metadata),
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def _strip_heavy_metadata(meta) -> dict:
    """Return legacy_metadata without the large whisper arrays.

    whisper_words (7K+ entries) and whisper_segments (700+ entries) are
    only needed server-side by Ship It. Sending them to the browser adds
    ~430KB to every project API response for no benefit.
    """
    if not meta:
        return {}
    light = dict(meta)
    words = light.pop("whisper_words", None)
    segs  = light.pop("whisper_segments", None)
    # Keep a compact summary so the frontend knows the data is there
    if words is not None:
        light["has_whisper_data"] = True
        light["word_count"] = len(words)
    if segs is not None:
        light["segment_count"] = len(segs)
    return light


# ── Project CRUD routes ────────────────────────────────────────────────────────

@app.get("/api/projects")
async def list_projects(status: str = "", limit: int = 20):
    """
    List Projects in reverse-chronological order.
    Optional ?status=review filter.
    """
    from db.engine import async_session as _async_session
    from db.models import Project
    from sqlalchemy import select, desc

    from db.models import Clip
    from sqlalchemy import func, desc

    async with _async_session() as session:
        q = select(Project).order_by(desc(Project.created_at)).limit(limit)
        if status:
            q = q.where(Project.status == status)
        rows = (await session.execute(q)).scalars().all()

        # Batch-fetch clip counts — one query, not N
        if rows:
            project_ids = [r.id for r in rows]
            count_rows = (await session.execute(
                select(Clip.project_id, func.count(Clip.id).label("n"))
                .where(Clip.project_id.in_(project_ids))
                .group_by(Clip.project_id)
            )).all()
            clip_counts = {str(r.project_id): r.n for r in count_rows}
        else:
            clip_counts = {}

        def _to_list_dict(p):
            d = _project_to_dict(p)
            d["clip_count"] = clip_counts.get(str(p.id), 0)
            return d

        return JSONResponse([_to_list_dict(p) for p in rows])


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    """Get a single Project by UUID."""
    from db.engine import async_session as _async_session
    from db.models import Project
    from sqlalchemy import select
    import uuid as _uuid

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        return JSONResponse({"error": "Invalid project ID"}, status_code=400)

    async with _async_session() as session:
        row = (await session.execute(select(Project).where(Project.id == pid))).scalar_one_or_none()
        if not row:
            return JSONResponse({"error": "Project not found"}, status_code=404)
        return JSONResponse(_project_to_dict(row))


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: str, request: Request):
    """
    Update editable fields on a Project.
    Allowed fields: title, description, transcript, show_notes, wizard_step,
                    guest_ids, sponsor_placement.
    Does NOT allow direct status transitions — use dedicated status endpoints.
    """
    from db.engine import async_session as _async_session
    from db.models import Project
    from sqlalchemy import select
    import uuid as _uuid

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        return JSONResponse({"error": "Invalid project ID"}, status_code=400)

    body = await request.json()
    EDITABLE = {"title", "description", "transcript", "show_notes", "wizard_step",
                "guest_ids", "sponsor_placement", "episode_number"}

    async with _async_session() as session:
        row = (await session.execute(select(Project).where(Project.id == pid))).scalar_one_or_none()
        if not row:
            return JSONResponse({"error": "Project not found"}, status_code=404)

        for field in EDITABLE:
            if field in body:
                setattr(row, field, body[field])

        await session.commit()
        await session.refresh(row)
        return JSONResponse(_project_to_dict(row))


@app.post("/api/projects/from-recording")
async def create_project_from_recording(
    video: UploadFile = File(...),
    title: str = Form(default=""),
    recorded_at: str = Form(default=""),
):
    """
    Stage 2 Step 2 — Studio handoff endpoint.

    Accepts the raw WebM Blob from studio.html's "Save & Continue" action.
    Saves the file to data/recordings/{project_id}.webm and creates a Project
    record in the database.

    Returns: {project_id, project: {...}}
    The caller redirects to /project/{project_id}.

    Title rules:
      - If title is provided: use it as-is.
      - If blank: "New build — {YYYY-MM-DD HH:MM}" (construction vocabulary).
    """
    import datetime as _dt
    import uuid as _uuid
    from db.engine import async_session as _async_session
    from db.models import Project, Location
    from sqlalchemy import select as _select
    from config import settings as _settings

    location_id_str = _settings.titan_location_id
    if not location_id_str:
        return JSONResponse({"error": "No location configured"}, status_code=500)

    project_id = _uuid.uuid4()

    # ── Save recording to disk ────────────────────────────────────────────
    recordings_dir = DATA_DIR / "recordings"
    recordings_dir.mkdir(exist_ok=True)

    # Preserve extension from uploaded filename, default webm
    orig_filename = video.filename or "recording.webm"
    ext = orig_filename.rsplit(".", 1)[-1].lower() if "." in orig_filename else "webm"
    if ext not in {"webm", "mp4", "mov", "mkv", "ogg"}:
        ext = "webm"

    recording_filename = f"{project_id}.{ext}"
    recording_path = str(recordings_dir / recording_filename)

    try:
        contents = await video.read()
        with open(recording_path, "wb") as f:
            f.write(contents)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to save recording: {exc}"}, status_code=500)

    # ── Build title ───────────────────────────────────────────────────────
    effective_title = title.strip()
    if not effective_title:
        ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        effective_title = f"New build — {ts}"

    # ── Create Project record ─────────────────────────────────────────────
    try:
        async with _async_session() as session:
            # Resolve the Location row for this location_id string
            loc_row = (
                await session.execute(
                    _select(Location).where(Location.ghl_location_id == location_id_str)
                )
            ).scalar_one_or_none()

            if not loc_row:
                # Fallback: use the UUID directly if it's already a UUID
                try:
                    loc_uuid = _uuid.UUID(location_id_str)
                except ValueError:
                    return JSONResponse(
                        {"error": "Could not resolve location"}, status_code=500
                    )
            else:
                loc_uuid = loc_row.id

            project = Project(
                id=project_id,
                location_id=loc_uuid,
                title=effective_title,
                type="episode",
                status="recording_done",
                recording_path=recording_path,
                transcription_status="pending",
                wizard_step=1,
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)

            return JSONResponse(
                {
                    "project_id": str(project_id),
                    "project": _project_to_dict(project),
                },
                status_code=201,
            )
    except Exception as exc:
        # Clean up the saved file if DB insert fails
        try:
            Path(recording_path).unlink(missing_ok=True)
        except Exception:
            pass
        return JSONResponse({"error": f"Failed to create project: {exc}"}, status_code=500)


@app.post("/api/projects/from-upload")
async def create_project_from_upload(
    file: UploadFile = File(...),
    title: str = Form(default=""),
):
    """
    Phase C — Upload entry point.

    Accepts a pre-recorded MP4/MOV/WebM/MP3/M4A file from disk.
    Saves to data/recordings/{project_id}.{ext}, creates a Project record,
    kicks off Whisper transcription in the background.

    Returns: {project_id, project: {...}}
    Caller redirects to /project/{project_id}.
    """
    import datetime as _dt
    import uuid as _uuid
    from db.engine import async_session as _async_session
    from db.models import Project, Location
    from sqlalchemy import select as _select
    from config import settings as _settings

    _ALLOWED_EXTS = {"mp4", "mov", "webm", "mp3", "m4a"}

    location_id_str = _settings.titan_location_id
    if not location_id_str:
        return JSONResponse({"error": "No location configured"}, status_code=500)

    # ── Validate extension ────────────────────────────────────────────────
    orig_filename = file.filename or "upload.mp4"
    ext = orig_filename.rsplit(".", 1)[-1].lower() if "." in orig_filename else "mp4"
    if ext not in _ALLOWED_EXTS:
        return JSONResponse(
            {"error": f"Unsupported file type .{ext}. Accepted: {', '.join(sorted(_ALLOWED_EXTS))}"},
            status_code=400,
        )

    project_id = _uuid.uuid4()

    # ── Save file to disk ─────────────────────────────────────────────────
    recordings_dir = DATA_DIR / "recordings"
    recordings_dir.mkdir(exist_ok=True)

    recording_filename = f"{project_id}.{ext}"
    recording_path = str(recordings_dir / recording_filename)

    try:
        contents = await file.read()
        with open(recording_path, "wb") as f:
            f.write(contents)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to save file: {exc}"}, status_code=500)

    # ── Build title ───────────────────────────────────────────────────────
    effective_title = title.strip()
    if not effective_title:
        ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        effective_title = f"New build — {ts}"

    # ── Create Project record ─────────────────────────────────────────────
    try:
        async with _async_session() as session:
            loc_row = (
                await session.execute(
                    _select(Location).where(Location.ghl_location_id == location_id_str)
                )
            ).scalar_one_or_none()

            if not loc_row:
                try:
                    loc_uuid = _uuid.UUID(location_id_str)
                except ValueError:
                    try:
                        Path(recording_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse(
                        {"error": "Could not resolve location"}, status_code=500
                    )
            else:
                loc_uuid = loc_row.id

            project = Project(
                id=project_id,
                location_id=loc_uuid,
                title=effective_title,
                type="episode",
                status="recording_done",
                recording_path=recording_path,
                transcription_status="pending",
                wizard_step=1,
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)

            # Kick off transcription in the background
            asyncio.create_task(_run_transcription(str(project_id)))

            return JSONResponse(
                {
                    "project_id": str(project_id),
                    "project": _project_to_dict(project),
                },
                status_code=201,
            )
    except Exception as exc:
        try:
            Path(recording_path).unlink(missing_ok=True)
        except Exception:
            pass
        return JSONResponse({"error": f"Failed to create project: {exc}"}, status_code=500)


async def _run_transcription(project_id_str: str) -> None:
    """
    Background task — transcribe the project's recording via Whisper.

    Single-transcription architecture (OPT-1):
    1. FFmpeg compress to 16kHz mono ~48kbps  → {stem}.transcription.mp3
       (40-min episode ≈ 14MB — well under Whisper's 25MB cap; no quality loss
        because Whisper resamples to 16kHz mono internally)
    2. If still >24MB (marathon episode), chunk into 20-min segments + re-offset timestamps.
    3. Single Whisper call: response_format='verbose_json',
       timestamp_granularities=['word','segment'].
    4. Persist:
         project.transcript                              ← plain text (wizard display)
         project.legacy_metadata['extracted_audio_path'] ← path for Ship It filler removal
         project.legacy_metadata['whisper_words']        ← word timestamps for clip detection
         project.legacy_metadata['whisper_segments']     ← segment timestamps for chapters

    Ship It reads legacy_metadata — no second Whisper call needed (eliminates OPT-1 cost bleed).
    """
    import uuid as _uuid
    import openai as _openai
    import subprocess as _subprocess
    from db.engine import async_session as _async_session
    from db.models import Project
    from sqlalchemy import select as _select
    from config import settings as _settings

    try:
        pid = _uuid.UUID(project_id_str)
    except ValueError:
        print(f"[transcribe] Invalid project_id: {project_id_str}")
        return

    async with _async_session() as session:
        project = (
            await session.execute(_select(Project).where(Project.id == pid))
        ).scalar_one_or_none()

        if not project:
            print(f"[transcribe] Project {project_id_str} not found")
            return
        if not project.recording_path:
            print(f"[transcribe] Project {project_id_str} has no recording_path")
            project.transcription_status = "failed"
            await session.commit()
            return

        recording_file = Path(project.recording_path)
        if not recording_file.exists():
            print(f"[transcribe] Recording file not found: {project.recording_path}")
            project.transcription_status = "failed"
            await session.commit()
            return

        # Mark running
        project.transcription_status = "running"
        await session.commit()

    import asyncio as _asyncio
    import functools as _functools
    import math as _math

    loop = _asyncio.get_event_loop()

    # ── Step 1: FFmpeg compress to 16kHz mono ~48kbps ───────────────────────────
    compressed_path = str(recording_file.with_suffix("")) + ".transcription.mp3"

    def _compress(src: str, dst: str) -> None:
        cmd = [
            "ffmpeg", "-y",
            "-i", src,
            "-vn",          # drop video track
            "-ar", "16000", # 16kHz — Whisper's native rate (no quality loss)
            "-ac", "1",     # mono
            "-b:a", "48k",  # ~48kbps — 40min ≈ 14MB
            "-f", "mp3",
            dst,
        ]
        r = _subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"FFmpeg compression failed: {r.stderr[-400:]}")

    try:
        if Path(compressed_path).exists() and Path(compressed_path).stat().st_size > 0:
            print(f"[transcribe] Compressed audio already exists — skipping FFmpeg")
        else:
            print(f"[transcribe] Compressing {recording_file.name} → {Path(compressed_path).name}")
            await loop.run_in_executor(
                None, _functools.partial(_compress, str(recording_file), compressed_path)
            )
        compressed_size = Path(compressed_path).stat().st_size
        print(f"[transcribe] Compressed: {compressed_size / 1024 / 1024:.1f} MB")
    except Exception as exc:
        print(f"[transcribe] FFmpeg failed for {project_id_str}: {exc}")
        async with _async_session() as session:
            proj = (await session.execute(_select(Project).where(Project.id == pid))).scalar_one_or_none()
            if proj:
                proj.transcription_status = "failed"
                await session.commit()
        return

    # ── Step 2: Single Whisper call (or chunked for marathon episodes) ───────────
    WHISPER_LIMIT = 24 * 1024 * 1024  # 24MB safety margin (API cap is 25MB)

    def _whisper_single(path: str) -> dict:
        """verbose_json call — returns {text, words, segments}."""
        client = _openai.OpenAI(api_key=_settings.openai_api_key)
        with open(path, "rb") as af:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=af,
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
        words = [
            {"word": w.word.strip(), "start": w.start, "end": w.end}
            for w in (result.words or [])
            if hasattr(w, "start") and hasattr(w, "end")
        ]
        segments = [
            {"start": s.start, "end": s.end, "text": s.text}
            for s in (result.segments or [])
        ] if hasattr(result, "segments") and result.segments else []
        return {"text": result.text or "", "words": words, "segments": segments}

    def _whisper_chunked(path: str, size_bytes: int) -> dict:
        """20-minute chunks with timestamp re-offset for marathon episodes."""
        # Estimate duration from compressed size at 48kbps
        est_duration_s = size_bytes / (48000 / 8)
        chunk_s = 20 * 60
        n_chunks = _math.ceil(est_duration_s / chunk_s)
        print(f"[transcribe] Marathon episode — {n_chunks} chunks of {chunk_s//60}min")
        client = _openai.OpenAI(api_key=_settings.openai_api_key)
        base = str(Path(path).with_suffix(""))
        all_words, all_segs, all_text = [], [], []
        for i in range(n_chunks):
            offset = i * chunk_s
            chunk_path = f"{base}.chunk{i:02d}.mp3"
            cmd = ["ffmpeg", "-y", "-i", path, "-ss", str(offset),
                   "-t", str(chunk_s), "-c", "copy", chunk_path]
            proc = _subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0 or not Path(chunk_path).exists():
                print(f"[transcribe] Chunk {i} extraction failed — skipping")
                continue
            if Path(chunk_path).stat().st_size == 0:
                Path(chunk_path).unlink(missing_ok=True)
                continue
            try:
                with open(chunk_path, "rb") as cf:
                    r = client.audio.transcriptions.create(
                        model="whisper-1", file=cf,
                        response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                    )
                all_words.extend([
                    {"word": w.word.strip(), "start": w.start + offset, "end": w.end + offset}
                    for w in (r.words or []) if hasattr(w, "start") and hasattr(w, "end")
                ])
                if hasattr(r, "segments") and r.segments:
                    all_segs.extend([
                        {"start": s.start + offset, "end": s.end + offset, "text": s.text}
                        for s in r.segments
                    ])
                all_text.append(r.text or "")
                print(f"[transcribe] Chunk {i}: {len(r.words or [])} words at offset {offset}s")
            except Exception as ce:
                print(f"[transcribe] Chunk {i} Whisper failed: {ce}")
            finally:
                Path(chunk_path).unlink(missing_ok=True)
        return {"text": " ".join(all_text).strip(), "words": all_words, "segments": all_segs}

    try:
        if compressed_size > WHISPER_LIMIT:
            print(f"[transcribe] {compressed_size/1024/1024:.1f}MB > 24MB limit — chunking")
            whisper_result = await loop.run_in_executor(
                None, _functools.partial(_whisper_chunked, compressed_path, compressed_size)
            )
        else:
            print(f"[transcribe] Running single Whisper verbose_json call")
            whisper_result = await loop.run_in_executor(
                None, _functools.partial(_whisper_single, compressed_path)
            )
        transcript_text = whisper_result["text"].strip()
        print(
            f"[transcribe] Done — {len(transcript_text)} chars, "
            f"{len(whisper_result['words'])} words, "
            f"{len(whisper_result['segments'])} segments"
        )
    except Exception as exc:
        print(f"[transcribe] Whisper failed for {project_id_str}: {exc}")
        async with _async_session() as session:
            proj = (await session.execute(_select(Project).where(Project.id == pid))).scalar_one_or_none()
            if proj:
                proj.transcription_status = "failed"
                await session.commit()
        return

    # ── Step 3: Persist transcript + stored timestamps for Ship It ───────────────
    async with _async_session() as session:
        proj = (
            await session.execute(_select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if proj:
            proj.transcript = transcript_text
            proj.transcription_status = "done"
            # Store timestamps so Ship It skips the second Whisper call (OPT-1 closed)
            existing_meta = proj.legacy_metadata or {}
            existing_meta["extracted_audio_path"] = compressed_path
            existing_meta["whisper_words"] = whisper_result["words"]
            existing_meta["whisper_segments"] = whisper_result["segments"]
            proj.legacy_metadata = existing_meta
            await session.commit()
            print(
                f"[transcribe] Persisted — transcript {len(transcript_text)} chars, "
                f"{len(whisper_result['words'])} words stored, "
                f"audio: {Path(compressed_path).name}"
            )


@app.post("/api/projects/{project_id}/transcribe")
async def start_transcription(project_id: str):
    """
    Stage 2 Step 2 — Kick off Whisper transcription for a project's recording.

    Returns immediately (transcription runs in background).
    Poll GET /api/projects/{project_id} and check transcription_status:
      pending → running → done (transcript field populated) | failed

    Idempotent: re-calling while 'running' is a no-op.
    Re-calling after 'failed' retries.
    """
    import uuid as _uuid
    from db.engine import async_session as _async_session
    from db.models import Project
    from sqlalchemy import select as _select

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        return JSONResponse({"error": "Invalid project ID"}, status_code=400)

    async with _async_session() as session:
        project = (
            await session.execute(_select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if not project:
            return JSONResponse({"error": "Project not found"}, status_code=404)
        if project.transcription_status == "running":
            return JSONResponse({"ok": True, "status": "already_running"})
        if project.transcription_status == "done" and project.transcript:
            return JSONResponse({"ok": True, "status": "already_done"})
        if not project.recording_path:
            return JSONResponse(
                {"error": "No recording attached to this project"}, status_code=422
            )

    asyncio.create_task(_run_transcription(project_id))
    return JSONResponse({"ok": True, "status": "started"})


@app.post("/api/projects/{project_id}/transition")
async def transition_project_status(project_id: str, request: Request):
    """
    Advance a Project through the state machine.
    Body: {"status": "review"} — only valid transitions are accepted.
    Construction vocabulary: status 'closing' = episode being published.
    """
    from db.engine import async_session as _async_session
    from db.models import Project
    from sqlalchemy import select
    import uuid as _uuid
    from datetime import datetime, timezone

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        return JSONResponse({"error": "Invalid project ID"}, status_code=400)

    body = await request.json()
    new_status = body.get("status", "").strip()
    if not new_status:
        return JSONResponse({"error": "status required"}, status_code=400)

    async with _async_session() as session:
        row = (await session.execute(select(Project).where(Project.id == pid))).scalar_one_or_none()
        if not row:
            return JSONResponse({"error": "Project not found"}, status_code=404)

        allowed = _PROJECT_STATUS_TRANSITIONS.get(row.status, [])
        if new_status not in allowed:
            return JSONResponse(
                {"error": f"Cannot transition {row.status} → {new_status}. Allowed: {allowed}"},
                status_code=400,
            )

        row.status = new_status
        if new_status == "closed":
            row.closed_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(row)

        # Audit log
        try:
            import json as _json_audit, uuid as _uuid_audit
            import sqlalchemy as _sa_audit
            from services.foundation import _get_default_location_id
            loc_id = await _get_default_location_id()
            async with _async_session() as audit_session:
                await audit_session.execute(
                    _sa_audit.text("""
                        INSERT INTO audit_log (id, location_id, action, payload, created_at)
                        VALUES (:id, :loc_id, :action, CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {"id": str(_uuid_audit.uuid4()), "loc_id": str(loc_id),
                     "action": f"project.status.{new_status}",
                     "payload": _json_audit.dumps({"project_id": str(row.id), "new_status": new_status})},
                )
                await audit_session.commit()
        except Exception:
            pass

        return JSONResponse(_project_to_dict(row))


@app.get("/api/projects/{project_id}/clips")
async def list_project_clips(project_id: str):
    """List all Clip rows for a Project."""
    from db.engine import async_session as _async_session
    from db.models import Clip
    from sqlalchemy import select
    import uuid as _uuid

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        return JSONResponse({"error": "Invalid project ID"}, status_code=400)

    async with _async_session() as session:
        rows = (await session.execute(
            select(Clip).where(Clip.project_id == pid)
        )).scalars().all()
        return JSONResponse([{
            "id": str(c.id),
            "project_id": str(c.project_id),
            "source_start_seconds": c.source_start_seconds,
            "source_end_seconds": c.source_end_seconds,
            "hook_text": c.hook_text,
            "clip_caption": c.clip_caption,
            "virality_score": c.virality_score,
            # rendered_url is a filesystem path — expose a streamable /video URL instead
            "rendered_url": (
                f"/api/projects/{c.project_id}/clips/{c.id}/video"
                if c.rendered_url and Path(c.rendered_url).exists()
                else None
            ),
            "srt_url": c.srt_url,
            "status": c.status,
        } for c in rows])


@app.get("/api/projects/{project_id}/clips/{clip_id}/video")
async def serve_project_clip_video(project_id: str, clip_id: str):
    """
    Stream a rendered project clip MP4 for in-browser preview.

    The rendered_url stored on Clip rows is a local filesystem path.
    This endpoint converts it to a streamable HTTP response so the
    project.html Step 3 video player works without filesystem access.
    """
    from fastapi.responses import FileResponse as _FR
    from db.engine import async_session as _async_session
    from db.models import Clip
    from sqlalchemy import select
    import uuid as _uuid

    try:
        pid = _uuid.UUID(project_id)
        cid = _uuid.UUID(clip_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID")

    async with _async_session() as session:
        clip = (await session.execute(
            select(Clip).where(Clip.id == cid, Clip.project_id == pid)
        )).scalar_one_or_none()

    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    path = clip.rendered_url or ""
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Clip file not on disk")

    return _FR(path=path, media_type="video/mp4", filename=Path(path).name)


@app.get("/api/projects/{project_id}/clips/{clip_id}/srt")
async def serve_project_clip_srt(project_id: str, clip_id: str):
    """Return the SRT caption file for a rendered clip as plain text."""
    from fastapi.responses import PlainTextResponse
    from db.engine import async_session as _async_session
    from db.models import Clip
    from sqlalchemy import select
    import uuid as _uuid

    try:
        pid = _uuid.UUID(project_id)
        cid = _uuid.UUID(clip_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID")

    async with _async_session() as session:
        clip = (await session.execute(
            select(Clip).where(Clip.id == cid, Clip.project_id == pid)
        )).scalar_one_or_none()

    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    # Derive SRT path from rendered_url (stored as filesystem path)
    rendered = clip.rendered_url or ""
    if rendered.startswith("/api/"):
        raise HTTPException(status_code=404, detail="SRT not available")

    srt_path = Path(rendered).with_suffix(".srt")
    if not srt_path.exists():
        raise HTTPException(status_code=404, detail="SRT file not on disk")

    return PlainTextResponse(srt_path.read_text(encoding="utf-8"))


@app.post("/api/projects/{project_id}/clips/{clip_id}/rerender")
async def rerender_clip(project_id: str, clip_id: str, request: Request):
    """
    Re-render a single clip with a different crop mode.

    Body: { "crop_mode": "stack" | "left" | "right" | "center" }
      stack  — host top / guest bottom (default, best for interviews)
      left   — left half of frame only (host solo)
      right  — right half of frame only (guest solo)
      center — original center crop (wide single-speaker)

    Response: { "ok": true, "rendered_url": "/api/projects/.../clips/.../video" }
    """
    from db.engine import async_session as _async_session
    from db.models import Clip, Project
    from sqlalchemy import select
    from pipeline.project_pipeline import (
        generate_srt_for_clip, render_vertical_clip_from_video, render_vertical_clip
    )
    import uuid as _uuid
    import asyncio as _asyncio

    try:
        pid = _uuid.UUID(project_id)
        cid = _uuid.UUID(clip_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID")

    body = {}
    try: body = await request.json()
    except Exception: pass

    crop_mode = body.get("crop_mode", "stack")
    if crop_mode not in ("stack", "left", "right", "center"):
        raise HTTPException(status_code=400, detail="crop_mode must be stack|left|right|center")

    async with _async_session() as session:
        clip = (await session.execute(
            select(Clip).where(Clip.id == cid, Clip.project_id == pid)
        )).scalar_one_or_none()
        proj = (await session.execute(
            select(Project).where(Project.id == pid)
        )).scalar_one_or_none()

    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    recording_path = proj.recording_path or ""
    legacy = proj.legacy_metadata or {}
    stored_words = legacy.get("whisper_words", [])
    extracted_audio = legacy.get("extracted_audio_path", "")

    if not recording_path or not Path(recording_path).exists():
        raise HTTPException(status_code=400, detail="Source recording not on disk")

    start = clip.source_start_seconds or 0.0
    end   = clip.source_end_seconds or 0.0

    # Re-use existing SRT if available, regenerate otherwise
    srt_content = ""
    if clip.srt_url and Path(clip.srt_url).exists():
        srt_content = Path(clip.srt_url).read_text(encoding="utf-8")
    elif stored_words:
        srt_content = generate_srt_for_clip(stored_words, start, end)

    # Output to the same path (overwrites the existing render)
    mp4_path = clip.rendered_url or ""
    if not mp4_path or mp4_path.startswith("/api/"):
        raise HTTPException(status_code=400, detail="Cannot determine output path for re-render")

    loop = _asyncio.get_event_loop()

    def _do_rerender():
        try:
            render_vertical_clip_from_video(
                source_video=recording_path,
                start_sec=start,
                end_sec=end,
                srt_content=srt_content,
                output_path=mp4_path,
                crop_mode=crop_mode,
            )
        except RuntimeError as vid_err:
            # Fallback to audio-only if video render fails
            if extracted_audio and Path(extracted_audio).exists():
                render_vertical_clip(
                    source_mp3=extracted_audio,
                    start_sec=start,
                    end_sec=end,
                    srt_content=srt_content,
                    output_path=mp4_path,
                )
            else:
                raise vid_err
        return mp4_path

    try:
        await loop.run_in_executor(None, _do_rerender)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Re-render failed: {exc}")

    return JSONResponse({
        "ok": True,
        "crop_mode": crop_mode,
        "rendered_url": f"/api/projects/{project_id}/clips/{clip_id}/video",
    })


@app.post("/api/projects/{project_id}/clips/{clip_id}/post-to-youtube")
async def post_clip_to_youtube(project_id: str, clip_id: str, request: Request):
    """
    Upload a rendered clip as a private YouTube Short.

    Body (optional): { "title": "...", "description": "..." }
    Response: { "ok": true, "video_id": "...", "url": "https://www.youtube.com/watch?v=..." }
    """
    from fastapi.responses import JSONResponse as _JR
    from db.engine import async_session as _async_session
    from db.models import Clip, Project
    from sqlalchemy import select
    import uuid as _uuid

    try:
        pid = _uuid.UUID(project_id)
        cid = _uuid.UUID(clip_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID")

    body = {}
    try: body = await request.json()
    except Exception: pass

    async with _async_session() as session:
        clip = (await session.execute(
            select(Clip).where(Clip.id == cid, Clip.project_id == pid)
        )).scalar_one_or_none()
        proj = (await session.execute(
            select(Project).where(Project.id == pid)
        )).scalar_one_or_none()

    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    clip_path = clip.rendered_url or ""
    if not clip_path or not Path(clip_path).exists():
        raise HTTPException(status_code=400, detail="Clip file not on disk")

    title = body.get("title") or (clip.hook_text or "PodClick Short")[:100]
    description = body.get("description") or (clip.clip_caption or "")

    # Upload via existing pipeline/youtube helper (same as main episode)
    try:
        import asyncio as _asyncio
        from config import settings as _settings
        loop = _asyncio.get_event_loop()

        def _do_upload():
            from pipeline.youtube import upload_video as _yt_upload
            return _yt_upload(
                video_path=clip_path,
                title=title,
                description=description + "\n\n#Shorts",
                privacy_status="private",
                tags=["Shorts", "YouTubeShorts", "podcast"],
            )

        result = await loop.run_in_executor(None, _do_upload)
        vid = result.get("video_id") or result.get("id")
        return _JR({"ok": True, "video_id": vid, "url": result.get("url")})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/projects/{project_id}/clips/smart-schedule")
async def smart_schedule_clips(project_id: str):
    """
    AI-powered optimal posting schedule for this project's clips.

    1. Tries YouTube Analytics (dayOfWeek × hour heatmap) for real data
    2. Falls back to Claude recommendation for real estate podcast Shorts
       when analytics are thin (< 7 day channel, < 100 total views)

    Response: {
      source: "analytics" | "ai",
      start_at: "2026-06-10T14:00:00Z",
      cadence_days: 2,
      best_days: ["Tuesday", "Thursday"],
      best_hour_utc: 14,
      best_hour_local: "9:00 AM Central",
      hashtags: [...],
      reasoning: "...",
      clips_count: 5,
      schedule: [ {clip_n, publish_at, day_label} ]
    }
    """
    from db.engine import async_session as _async_session
    from db.models import Clip, Project
    from sqlalchemy import select
    from pipeline.youtube import get_subscriber_activity, is_authorized
    from datetime import datetime, timedelta, timezone
    from config import settings as _settings
    import uuid as _uuid
    import anthropic as _anthropic

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    # Count clips
    async with _async_session() as session:
        proj = (await session.execute(select(Project).where(Project.id == pid))).scalar_one_or_none()
        clip_count = len((await session.execute(
            select(Clip).where(Clip.project_id == pid, Clip.status != "removed")
        )).scalars().all())

    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    # ── 1. Try YouTube Analytics heatmap ──────────────────────────────────────
    analytics_source = False
    best_hour_utc = 14      # default 9am Central = 14:00 UTC
    best_days_idx = [1, 3]  # Tue, Thu
    cadence_days = 2
    reasoning = ""
    hashtags = []

    if is_authorized():
        loop = asyncio.get_event_loop()
        heatmap = await loop.run_in_executor(None, get_subscriber_activity)

        if heatmap:
            # Find peak day + hour from real data
            peak_views = 0
            for day, hours in heatmap.items():
                for hour, views in hours.items():
                    if views > peak_views:
                        peak_views = views
                        best_hour_utc = hour
                        best_days_idx = [day]
            analytics_source = True
            reasoning = f"Based on your YouTube Analytics: peak activity at hour {best_hour_utc}:00 UTC."

    # ── 2. AI optimization (always runs — enriches analytics or replaces it) ──
    try:
        client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)
        ep_title = proj.title or "Real estate podcast episode"
        analytics_ctx = (
            f"YouTube Analytics heatmap available — peak hour: {best_hour_utc}:00 UTC."
            if analytics_source
            else "No YouTube Analytics yet (new channel). Use niche research."
        )

        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=800,
            messages=[{"role": "user", "content": f"""
You are a YouTube Shorts growth expert specializing in real estate and podcast content.

Channel: Success Agent Podcast (real estate agents, investors, entrepreneurs)
Location: Springfield MO & Cheyenne WY markets
Episode: {ep_title}
Clips to schedule: {clip_count}
{analytics_ctx}

Optimize the posting schedule for maximum reach. Return ONLY valid JSON:
{{
  "best_hour_utc": <int 0-23>,
  "cadence_days": <int 1-7>,
  "best_days": ["Tuesday", "Thursday"],
  "reasoning": "<2-3 sentences explaining the choice>",
  "hashtags": ["<10-15 optimized hashtags without # symbol, mix of broad/niche/local>"]
}}

Rules:
- Real estate agents are most active Tue-Thu 7-10am and 6-9pm local (Central = UTC-5)
- Shorts algorithm rewards consistency over viral timing — cadence > perfect hour
- Hashtags: mix brand (#SuccessAgentPodcast), niche (#RealEstateInvesting #PodcastClips),
  broad (#Shorts #Podcast), and local (#SpringfieldMO #Wyoming)
- For 5 clips over 2 weeks, every 2-3 days works well
"""}]
        )

        import json as _json
        ai_data = _json.loads(resp.content[0].text.strip())
        best_hour_utc = ai_data.get("best_hour_utc", best_hour_utc)
        cadence_days  = ai_data.get("cadence_days", cadence_days)
        hashtags      = ai_data.get("hashtags", hashtags)
        reasoning     = ai_data.get("reasoning", reasoning)
        best_days     = ai_data.get("best_days", ["Tuesday", "Thursday"])
        if not analytics_source:
            analytics_source = False  # stays AI

    except Exception as ai_err:
        best_days = ["Tuesday", "Thursday"]
        hashtags  = ["SuccessAgentPodcast", "RealEstate", "Podcast", "Shorts", "RealEstateInvesting",
                     "RealEstateAgent", "PodcastClips", "YouTubeShorts", "SpringfieldMO", "Wyoming"]
        reasoning = "Using proven best-practice schedule for real estate podcast content."

    # ── 3. Build schedule ─────────────────────────────────────────────────────
    DAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    now = datetime.now(timezone.utc)
    # Find next occurrence of best day (prefer the first best_days entry)
    target_day_name = best_days[0] if best_days else "Tuesday"
    target_weekday  = DAY_NAMES.index(target_day_name) if target_day_name in DAY_NAMES else 1
    days_ahead = (target_weekday - now.weekday()) % 7 or 7
    start_dt = (now + timedelta(days=days_ahead)).replace(
        hour=best_hour_utc, minute=0, second=0, microsecond=0
    )

    schedule = []
    for i in range(clip_count):
        pub_dt = start_dt + timedelta(days=i * cadence_days)
        schedule.append({
            "clip_n":     i + 1,
            "publish_at": pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "day_label":  pub_dt.strftime("%A, %b %-d at %-I:%M %p UTC"),
        })

    # Convert UTC hour to Central for display
    central_hour = (best_hour_utc - 5) % 24
    ampm = "AM" if central_hour < 12 else "PM"
    display_hour = central_hour if central_hour <= 12 else central_hour - 12
    display_hour = display_hour or 12

    return JSONResponse({
        "source":          "analytics" if analytics_source else "ai",
        "start_at":        start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cadence_days":    cadence_days,
        "best_days":       best_days,
        "best_hour_utc":   best_hour_utc,
        "best_hour_local": f"{display_hour}:00 {ampm} Central",
        "hashtags":        hashtags,
        "reasoning":       reasoning,
        "clips_count":     clip_count,
        "schedule":        schedule,
    })


@app.post("/api/projects/{project_id}/clips/schedule-all")
async def schedule_all_clips(project_id: str, request: Request):
    """
    Upload all rendered clips as YouTube Shorts on a scheduled cadence.

    Body:
      start_at      — ISO 8601 datetime for first clip (e.g. "2026-06-10T09:00:00Z")
      cadence_days  — days between each clip (1, 2, 3, or 7)
      hour_utc      — hour of day in UTC to publish (default 14 = 9am Central)

    Response: { "scheduled": [ {clip_id, title, publish_at, video_id, url} ] }
    YouTube auto-publishes each clip at its scheduled time — no manual toggle needed.
    """
    from db.engine import async_session as _async_session
    from db.models import Clip, Project
    from sqlalchemy import select
    from pipeline.youtube import upload_video as _yt_upload, is_authorized
    from datetime import datetime, timedelta, timezone
    import uuid as _uuid
    import asyncio as _asyncio

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    if not is_authorized():
        raise HTTPException(status_code=400, detail="YouTube not connected")

    body = await request.json()
    cadence_days  = max(1, int(body.get("cadence_days", 2)))
    start_at_str  = body.get("start_at", "")
    ai_hashtags   = body.get("hashtags", [])

    # Parse start time, default to tomorrow at 14:00 UTC (9am Central)
    try:
        start_dt = datetime.fromisoformat(start_at_str.replace("Z", "+00:00")) if start_at_str else None
    except Exception:
        start_dt = None
    if not start_dt:
        start_dt = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
        start_dt += timedelta(days=1)

    # Load project + approved/rendered clips (de-duplicate by rendered_url)
    async with _async_session() as session:
        proj = (await session.execute(select(Project).where(Project.id == pid))).scalar_one_or_none()
        clip_rows = (await session.execute(
            select(Clip).where(Clip.project_id == pid, Clip.status != "removed")
        )).scalars().all()

    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    # De-duplicate by rendered_url
    seen_urls = set()
    clips = []
    for c in clip_rows:
        if c.rendered_url and c.rendered_url not in seen_urls and Path(c.rendered_url).exists():
            seen_urls.add(c.rendered_url)
            clips.append(c)

    if not clips:
        raise HTTPException(status_code=400, detail="No rendered clips found for this project")

    loop = _asyncio.get_event_loop()
    scheduled = []

    for idx, clip in enumerate(clips):
        publish_dt = start_dt + timedelta(days=idx * cadence_days)
        publish_at = publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        hook = (clip.hook_text or proj.title or "PodClick Short")[:100]
        caption = clip.clip_caption or ""
        desc = (
            f"{caption}\n\n"
            f"🎙 From the Success Agent Podcast\n\n"
            f"📞 Work with JP Fluellen:\n"
            f"Wyoming: +1-307-772-1184 | Missouri: +1-417-340-1927\n"
            f"👉 https://jpfluellen.com/contact\n\n"
            f"#Shorts #YouTubeShorts #SuccessAgentPodcast #RealEstate"
        )

        clip_path = clip.rendered_url

        def _upload(path=clip_path, title=hook, description=desc, pub=publish_at):
            base_tags = ["Shorts", "YouTubeShorts", "SuccessAgentPodcast", "RealEstate", "podcast"]
            all_tags  = list(ai_hashtags) + [t for t in base_tags if t not in ai_hashtags]
            return _yt_upload(
                video_path=path,
                title=title,
                description=description,
                privacy_status="private",
                publish_at=pub,
                tags=all_tags[:30],  # YouTube tag limit
                category_id="22",
            )

        try:
            result = await loop.run_in_executor(None, _upload)
            scheduled.append({
                "clip_id":    str(clip.id),
                "title":      hook,
                "publish_at": publish_at,
                "video_id":   result.get("video_id"),
                "url":        result.get("url"),
                "ok":         result.get("ok", False),
                "error":      result.get("error"),
            })
        except Exception as e:
            scheduled.append({"clip_id": str(clip.id), "ok": False, "error": str(e)})

    success_count = sum(1 for s in scheduled if s.get("ok"))
    return JSONResponse({
        "scheduled": scheduled,
        "total": len(scheduled),
        "success": success_count,
        "first_publish": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_publish": (start_dt + timedelta(days=(len(clips)-1) * cadence_days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


@app.patch("/api/projects/{project_id}/clips/{clip_id}")
async def update_clip(project_id: str, clip_id: str, request: Request):
    """Update clip caption or status (approve/remove from wizard step 3)."""
    from db.engine import async_session as _async_session
    from db.models import Clip
    from sqlalchemy import select
    import uuid as _uuid

    try:
        cid = _uuid.UUID(clip_id)
    except ValueError:
        return JSONResponse({"error": "Invalid clip ID"}, status_code=400)

    body = await request.json()

    async with _async_session() as session:
        row = (await session.execute(select(Clip).where(Clip.id == cid))).scalar_one_or_none()
        if not row:
            return JSONResponse({"error": "Clip not found"}, status_code=404)

        for field in ("clip_caption", "status", "hook_text"):
            if field in body:
                setattr(row, field, body[field])

        await session.commit()
        await session.refresh(row)
        return JSONResponse({"id": str(row.id), "status": row.status, "clip_caption": row.clip_caption})


@app.post("/api/projects/{project_id}/ship-it")
async def ship_it(project_id: str, request: Request):
    """
    The hero button — Ship It.

    Triggers the full processing chain for a Project:
      1. Transition status: recording_done/review → processing
      2. Run sync Ship It chain (sponsor round-robin, clip detection, render)
      3. Persist Clip rows to DB
      4. Generate Foundation-powered show notes
      5. Generate Foundation-powered clip captions
      6. Transition status → review (wizard ready)

    Returns immediately with job_id-like response; processing is async.
    Poll /api/projects/{id} for status='review' to know it's done.

    Construction vocabulary: Ship It = break ground on the distribution pipeline.
    """
    from db.engine import async_session as _async_session
    from db.models import Project, Clip
    from sqlalchemy import select
    from services.foundation import get_brand_context, assert_foundation_ready
    from schemas.foundation import BrandContextTaskType as _TaskType
    import uuid as _uuid

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        return JSONResponse({"error": "Invalid project ID"}, status_code=400)

    # Load project
    async with _async_session() as session:
        project = (await session.execute(select(Project).where(Project.id == pid))).scalar_one_or_none()
        if not project:
            return JSONResponse({"error": "Project not found"}, status_code=404)

        allowed_entry_states = ("recording_done", "review", "failed")
        if project.status not in allowed_entry_states:
            return JSONResponse(
                {"error": f"Project status '{project.status}' cannot Ship It. Must be one of: {allowed_entry_states}"},
                status_code=400,
            )

        # Transition to processing
        project.status = "processing"
        await session.commit()
        job_id = project.job_id
        transcript = project.transcript or ""
        mp3_url = project.mp3_url or ""
        location_id = project.location_id
        project_uuid = project.id
        recording_path = project.recording_path or ""
        # Read stored single-transcription data (OPT-1 fix) — Ship It reads these
        # instead of running a second Whisper call.
        _legacy = project.legacy_metadata or {}
        stored_audio_path = _legacy.get("extracted_audio_path", "")
        stored_words = _legacy.get("whisper_words", [])
        stored_segments = _legacy.get("whisper_segments", [])

    # Kick off async processing
    asyncio.create_task(_run_ship_it_async(
        project_uuid=project_uuid,
        project_id=project_id,
        job_id=job_id,
        transcript=transcript,
        mp3_url=mp3_url,
        location_id=location_id,
        recording_path=recording_path,
        stored_audio_path=stored_audio_path,
        stored_words=stored_words,
        stored_segments=stored_segments,
    ))

    return JSONResponse({
        "status": "processing",
        "project_id": project_id,
        "message": "Breaking ground — Ship It pipeline started. Poll /api/projects/{id} for status='review'.",
    })


# ── Step 2.5 helpers — audio extraction + word timestamps for studio recordings ──

def _ship_it_extract_audio(recording_path: str) -> str:
    """
    Extract audio track from a studio recording (webm/mp4/mov/ogg) to MP3.

    Saves alongside the source as <recording_path>.ship_audio.mp3 so retries
    don't re-extract (idem­potent: skip if output already exists and is non-empty).

    Returns the output MP3 path. Raises RuntimeError on FFmpeg failure.
    """
    import subprocess
    from pathlib import Path as _Path
    out_path = str(_Path(recording_path).with_suffix("")) + ".ship_audio.mp3"
    if _Path(out_path).exists() and _Path(out_path).stat().st_size > 0:
        return out_path  # already extracted from a previous attempt
    cmd = [
        "ffmpeg", "-y",
        "-i", recording_path,
        "-q:a", "2",          # VBR ~190 kbps — good quality for Whisper
        "-map", "a",           # audio track only
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg audio extraction failed for {recording_path}: "
            f"{result.stderr[-400:]}"
        )
    return out_path


def _ship_it_whisper_words(audio_path: str, api_key: str) -> dict:
    """
    Transcribe audio via OpenAI Whisper (verbose_json) and return word timestamps.

    Returns dict with keys:
      text     — full transcript string
      words    — list of {word, start, end} dicts (matches detect_clips_for_project contract)
      segments — list of Whisper segment objects
    """
    import openai as _openai_mod
    client = _openai_mod.OpenAI(api_key=api_key)
    with open(audio_path, "rb") as af:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=af,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )
    # Normalize words list to the shape detect_clips_for_project expects
    raw_words = result.words or []
    words = [
        {"word": w.word.strip(), "start": w.start, "end": w.end}
        for w in raw_words
        if hasattr(w, "start") and hasattr(w, "end")
    ]
    segments = []
    if hasattr(result, "segments") and result.segments:
        segments = [
            {"start": s.start, "end": s.end, "text": s.text}
            for s in result.segments
        ]
    return {
        "text": result.text or "",
        "words": words,
        "segments": segments,
    }


async def _run_ship_it_async(
    project_uuid,
    project_id: str,
    job_id: Optional[str],
    transcript: str,
    mp3_url: str,
    location_id,
    recording_path: str = "",
    stored_audio_path: str = "",
    stored_words: Optional[list] = None,
    stored_segments: Optional[list] = None,
) -> None:
    """
    Async Ship It orchestration — runs after the HTTP response returns.
    Each step updates the Project row; failures are logged and non-fatal.
    """
    from db.engine import async_session as _async_session
    from db.models import Project, Clip
    from sqlalchemy import select
    from services.foundation import get_brand_context, assert_foundation_ready
    from schemas.foundation import BrandContextTaskType as _TaskType
    import anthropic as _anthropic
    import uuid as _uuid
    from config import settings as _settings

    loop = asyncio.get_event_loop()

    try:
        await _run_ship_it_inner(
            project_uuid=project_uuid, project_id=project_id, job_id=job_id,
            transcript=transcript, mp3_url=mp3_url, location_id=location_id,
            recording_path=recording_path, stored_audio_path=stored_audio_path,
            stored_words=stored_words, stored_segments=stored_segments,
            loop=loop,
        )
    except Exception as _fatal:
        import logging
        logging.getLogger("podclick.projects").error(
            "Ship It fatal error for %s: %s", project_id, _fatal, exc_info=True
        )
        await _ship_it_fail_safe(project_uuid, str(_fatal))


async def _run_ship_it_inner(
    project_uuid, project_id, job_id, transcript, mp3_url, location_id,
    recording_path, stored_audio_path, stored_words, stored_segments, loop,
) -> None:
    """Inner Ship It body — wrapped by _run_ship_it_async for fail-safe error handling."""
    from db.engine import async_session as _async_session
    from db.models import Project, Clip
    from sqlalchemy import select
    from services.foundation import get_brand_context, assert_foundation_ready
    from schemas.foundation import BrandContextTaskType as _TaskType
    import anthropic as _anthropic
    import uuid as _uuid
    from config import settings as _settings

    # Load job data (words + segments) from in-memory jobs dict if available
    job_data = jobs.get(job_id, {}) if job_id else {}

    # ── Step 2.5: Audio + word timestamps for clip detection ─────────────────────
    # Fast path (OPT-1 fix): single-transcription stored timestamps from _run_transcription.
    # No second Whisper call needed — just read what was stored at upload time.
    # Fallback path: extract + re-transcribe (pre-fix projects or cleaned-up audio files).
    if not job_data.get("mp3_path"):
        _stored_w = stored_words or []
        _stored_s = stored_segments or []

        if stored_audio_path and _stored_w:
            from pathlib import Path as _path_check
            if _path_check(stored_audio_path).exists():
                print(
                    f"[ship_it.2.5] Using stored transcription data — "
                    f"{len(_stored_w)} words, no second Whisper call (OPT-1)"
                )
                job_data = {
                    "mp3_path": stored_audio_path,
                    "words": _stored_w,
                    "segments": _stored_s,
                }
            else:
                print(
                    f"[ship_it.2.5] Stored audio missing on disk ({stored_audio_path}) "
                    f"— falling back to re-extraction"
                )

        # Fallback: extract + Whisper for pre-fix projects or if stored audio is gone
        if not job_data.get("mp3_path") and recording_path:
            from pathlib import Path as _path_check
            if _path_check(recording_path).exists():
                try:
                    print(f"[ship_it.2.5] Fallback: extracting audio from {recording_path}…")
                    extracted_audio = await loop.run_in_executor(
                        None, _ship_it_extract_audio, recording_path
                    )
                    print(f"[ship_it.2.5] Audio extracted → {extracted_audio}")
                    print(f"[ship_it.2.5] Running Whisper (verbose_json) — fallback path…")
                    from config import settings as _cfg_s
                    whisper_result = await loop.run_in_executor(
                        None, _ship_it_whisper_words, extracted_audio, _cfg_s.openai_api_key
                    )
                    job_data = {
                        "mp3_path": extracted_audio,
                        "words": whisper_result["words"],
                        "segments": whisper_result["segments"],
                    }
                    print(
                        f"[ship_it.2.5] Fallback Whisper done — "
                        f"{len(whisper_result['words'])} word timestamps"
                    )
                except Exception as _ext_err:
                    import logging
                    logging.getLogger("podclick.projects").warning(
                        "[ship_it.2.5] Audio extraction/transcription failed — "
                        "clips will be skipped: %s", _ext_err
                    )
            else:
                print(f"[ship_it.2.5] recording_path set but file missing: {recording_path} — skipping")

    # ── a-c: Sponsor, clip detection, clip rendering (sync via executor) ───────
    try:
        from pipeline.project_pipeline import run_ship_it as _sync_ship_it

        def _sync():
            return _sync_ship_it(
                project_id=project_id,
                job_data=job_data,
                progress_cb=lambda msg: None,
                source_video=recording_path,
                crop_mode="stack",  # stack = host top / guest bottom for interviews
            )

        ship_result = await loop.run_in_executor(None, _sync)

        # Persist sponsor placement + assembled episode path to Project
        _sponsor_pl   = ship_result.get("sponsor_placement")
        _assembled    = ship_result.get("assembled_mp3")
        _audio_asm    = ship_result.get("audio_assembly", {})
        if _sponsor_pl or _assembled or _audio_asm:
            async with _async_session() as session:
                proj = (await session.execute(select(Project).where(Project.id == project_uuid))).scalar_one_or_none()
                if proj:
                    if _sponsor_pl:
                        proj.sponsor_placement = _sponsor_pl
                    if _assembled:
                        proj.mp3_url = _assembled
                    if _audio_asm:
                        from sqlalchemy.orm.attributes import flag_modified as _fm
                        proj.audio_assembly = dict(_audio_asm)
                        _fm(proj, "audio_assembly")
                    await session.commit()

        # Persist Clip rows to DB — delete first to prevent accumulation from re-runs
        rendered = ship_result.get("rendered_clips", [])
        clip_ids = []
        if rendered:
            async with _async_session() as session:
                from sqlalchemy import delete as _delete
                await session.execute(_delete(Clip).where(Clip.project_id == project_uuid))
                await session.flush()
                for idx, r in enumerate(rendered):
                    clip = Clip(
                        project_id=project_uuid,
                        location_id=location_id,
                        source_start_seconds=r.get("start", 0.0),
                        source_end_seconds=r.get("end", 0.0),
                        hook_text=r.get("hook_text"),
                        virality_score=r.get("score"),
                        rendered_url=r.get("rendered_url"),
                        srt_url=r.get("srt_url"),
                        status=r.get("status", "pending"),
                    )
                    session.add(clip)
                await session.flush()
                # Collect IDs before commit
                for clip in session.new:
                    if isinstance(clip, Clip):
                        clip_ids.append(clip.id)
                await session.commit()

    except Exception as _ship_err:
        import logging
        logging.getLogger("podclick.projects").error("Ship It sync chain failed: %s", _ship_err)
        rendered = []
        clip_ids = []

    # ── d: Foundation-powered show notes ──────────────────────────────────────
    show_notes_text = None
    if transcript:
        try:
            _anthropic_client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)
            async with _async_session() as _f_session:
                ctx = await get_brand_context(
                    session=_f_session,
                    location_id=str(location_id),
                    task_type=_TaskType.show_notes,
                    topic=transcript[:500],
                )
            # ctx is a BrandContext Pydantic model — use attribute access
            vp = ctx.voice_profile
            tone_list = vp.tone or []
            tone_str = ", ".join(tone_list) if tone_list else ""
            voice_preamble = (
                f"Write in the voice of {ctx.brand_profile.full_name or 'the host'}, "
                f"a {ctx.brand_profile.niche_primary or 'real estate professional'} in "
                f"{ctx.brand_profile.market_city or 'their market'}.\n"
                f"Tone: {tone_str}. {vp.cadence or ''}\n\n"
                f"Voice examples:\n"
                + "\n".join(f"- {s.text[:200]}" for s in ctx.voice_samples[:3])
            )

            response = _anthropic_client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1500,
                system=voice_preamble,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Write Buzzsprout-ready show notes for this podcast episode using "
                        f"this exact structure (use markdown formatting — headers, bullets, bold):\n\n"
                        f"## Episode Summary\n[2-3 sentence summary of what this episode covers and why it matters]\n\n"
                        f"## What You'll Learn\n- [Key takeaway]\n- [Key takeaway]\n- [Key takeaway]\n\n"
                        f"## Episode Highlights\n[00:00] Introduction\n[Approximate timestamps for 3-5 key moments based on transcript flow]\n\n"
                        f"## Resources Mentioned\n[Any tools, people, books, or links discussed — write 'None mentioned' if absent]\n\n"
                        f"## About the Host\n[1-2 sentence bio in first person, referencing their market and niche]\n\n"
                        f"## Subscribe & Review\n[Short CTA — subscribe wherever they listen, leave a review if it helped]\n\n"
                        f"Use construction vocabulary naturally. "
                        f"Do NOT use banned phrases: AI-powered, leverage, unlock, seamless, workflow, dashboard.\n\n"
                        f"TRANSCRIPT:\n{transcript[:3000]}"
                    )
                }],
            )
            show_notes_text = response.content[0].text

            # Persist show notes + YouTube chapter markers
            _segments = job_data.get("segments", [])
            _chapters = _chapters_from_segments(_segments) if _segments else []
            print(f"[ship_it.chapters] segments={len(_segments)} → chapters={len(_chapters)}")
            async with _async_session() as session:
                proj = (await session.execute(select(Project).where(Project.id == project_uuid))).scalar_one_or_none()
                if proj:
                    proj.show_notes = show_notes_text
                    if _chapters:
                        from sqlalchemy.orm.attributes import flag_modified as _flag_mod
                        # Build new dict so SQLAlchemy detects the JSONB mutation
                        new_meta = dict(proj.legacy_metadata or {})
                        new_meta["youtube_chapters"] = _chapters
                        proj.legacy_metadata = new_meta
                        _flag_mod(proj, "legacy_metadata")
                        print(f"[ship_it.chapters] {len(_chapters)} chapter markers stored for project {project_uuid}")
                    await session.commit()

            # Audit log for Foundation call
            try:
                import json as _j, sqlalchemy as _sa
                async with _async_session() as audit_s:
                    await audit_s.execute(
                        _sa.text("INSERT INTO audit_log (id, location_id, action, payload, created_at) "
                                 "VALUES (:id, :loc, 'project.show_notes', CAST(:p AS jsonb), now()) ON CONFLICT DO NOTHING"),
                        {"id": str(_uuid.uuid4()), "loc": str(location_id),
                         "p": _j.dumps({"project_id": str(project_uuid), "model": "claude-sonnet-4-5",
                                        "sample_count": ctx.metadata.sample_count})}
                    )
                    await audit_s.commit()
            except Exception:
                pass

        except Exception as _sn_err:
            import logging
            logging.getLogger("podclick.projects").warning("show_notes generation failed: %s", _sn_err)

    # ── e: Foundation-powered clip captions (one per rendered clip) ───────────
    if rendered and transcript:
        try:
            async with _async_session() as session:
                clip_rows = (await session.execute(
                    select(Clip).where(Clip.project_id == project_uuid)
                )).scalars().all()

            _anthropic_client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)

            for clip_row in clip_rows:
                try:
                    clip_topic = clip_row.hook_text or "podcast clip"
                    async with _async_session() as _fc_session:
                        ctx = await get_brand_context(
                            session=_fc_session,
                            location_id=str(location_id),
                            task_type=_TaskType.clip_caption,
                            topic=clip_topic,
                        )
                    vp = ctx.voice_profile
                    tone_list = vp.tone or []
                    tone_str = ", ".join(tone_list) if tone_list else ""
                    voice_preamble = (
                        f"Write in the voice of {ctx.brand_profile.full_name or 'the host'}, "
                        f"a {ctx.brand_profile.niche_primary or 'real estate professional'}.\n"
                        f"Tone: {tone_str}.\n\n"
                        f"Voice examples:\n"
                        + "\n".join(f"- {s.text[:150]}" for s in ctx.voice_samples[:3])
                    )

                    caption_resp = _anthropic_client.messages.create(
                        model="claude-sonnet-4-5",
                        max_tokens=300,
                        system=voice_preamble,
                        messages=[{
                            "role": "user",
                            "content": (
                                f"Write a short, punchy social media caption for this podcast clip. "
                                f"Max 3 sentences. Hook first. No hashtags (they'll be added separately). "
                                f"Sound like a real person sharing something valuable, not a brand post.\n\n"
                                f"CLIP HOOK: {clip_row.hook_text}\n"
                                f"CLIP TRANSCRIPT: {clip_row.virality_score}"
                            )
                        }],
                    )
                    caption_text = caption_resp.content[0].text

                    async with _async_session() as session:
                        cr = (await session.execute(select(Clip).where(Clip.id == clip_row.id))).scalar_one_or_none()
                        if cr:
                            cr.clip_caption = caption_text
                            await session.commit()

                except Exception as _cap_err:
                    import logging
                    logging.getLogger("podclick.projects").warning(
                        "clip_caption failed for clip %s: %s", clip_row.id, _cap_err
                    )

        except Exception as _caps_err:
            import logging
            logging.getLogger("podclick.projects").warning("clip captions batch failed: %s", _caps_err)

    # ── Transition to review — Ship It wizard is ready ─────────────────────────
    try:
        async with _async_session() as session:
            proj = (await session.execute(select(Project).where(Project.id == project_uuid))).scalar_one_or_none()
            if proj and proj.status == "processing":
                proj.status = "review"
                proj.wizard_step = 3
                await session.commit()
    except Exception as _trans_err:
        import logging
        logging.getLogger("podclick.projects").error("Status transition to 'review' failed: %s", _trans_err)


async def _ship_it_fail_safe(project_uuid, error_msg: str) -> None:
    """Reset stuck 'processing' project to 'failed' with an error message."""
    try:
        from db.engine import async_session as _ses
        from db.models import Project
        from sqlalchemy import select as _sel
        async with _ses() as session:
            proj = (await session.execute(_sel(Project).where(Project.id == project_uuid))).scalar_one_or_none()
            if proj and proj.status == "processing":
                proj.status = "failed"
                # Store error in audio_assembly for display in failed-banner
                existing = proj.audio_assembly or {}
                existing["ship_it_error"] = error_msg
                proj.audio_assembly = existing
                await session.commit()
    except Exception:
        pass


# ── _distribute_project — background upload to Buzzsprout + YouTube ───────────

def _chapters_from_segments(segments: list, max_chapters: int = 8) -> list:
    """
    Derive YouTube chapter markers from Whisper segment list.
    Returns list of {start_s: float, label: str} sorted by start_s.

    YouTube chapter rules:
      - First chapter MUST be 0:00.
      - Minimum 3 chapters to activate the feature.
      - Each label: max 100 chars.
    """
    if not segments:
        return []

    total = segments[-1].get("end", 0)
    if total < 60:
        return []  # too short to bother

    # Always open with 0:00 Introduction
    chapters = [{"start_s": 0.0, "label": "Introduction"}]

    # Target ~1 chapter per 3 minutes; cap at max_chapters
    target = min(max_chapters, max(3, int(total / 180)))
    step = max(1, len(segments) // target)

    for i in range(step, len(segments), step):
        if len(chapters) >= max_chapters:
            break
        seg = segments[i]
        start = seg.get("start", 0)
        if start < 30:          # avoid micro-chapters near the open
            continue
        text = seg.get("text", "").strip()
        # Use first 6 words as label, strip trailing punctuation
        words = text.split()[:6]
        label = " ".join(words).rstrip(".,!?—–").strip()
        if not label:
            continue
        chapters.append({"start_s": float(start), "label": label[:100]})

    # Drop duplicates (same start_s), re-sort
    seen: set = set()
    unique = []
    for c in sorted(chapters, key=lambda x: x["start_s"]):
        key = int(c["start_s"])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique if len(unique) >= 3 else []


def _format_chapters(chapters: list) -> str:
    """
    Format chapter list as YouTube-ready description block.
    e.g. "0:00 Introduction\n2:15 The real issue with listings…"
    """
    lines = []
    for c in chapters:
        s = int(c["start_s"])
        mm, ss = divmod(s, 60)
        lines.append(f"{mm}:{ss:02d} {c['label']}")
    return "\n".join(lines)


async def _distribute_project(project_id: str, closing_at_ts: float) -> None:
    """
    Background task: upload assembled episode to Buzzsprout (private draft) and
    YouTube (private). Adds Buzzsprout entry to the release queue so the scheduler
    flips it public at closing_scheduled_at.

    Called from schedule_closing() via asyncio.create_task().
    WIRE DON'T REWRITE — calls pipeline/upload.py and pipeline/youtube.py unchanged.
    """
    import logging as _log
    import uuid as _uuid
    _logger = _log.getLogger("podclick.distribute")

    from db.engine import async_session as _async_session
    from db.models import Project
    from sqlalchemy import select as _select

    # ── Load project ──────────────────────────────────────────────────────────
    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        _logger.error("_distribute_project: invalid project_id %s", project_id)
        return

    async with _async_session() as _s:
        proj = (await _s.execute(_select(Project).where(Project.id == pid))).scalar_one_or_none()
        if not proj:
            _logger.error("_distribute_project: project %s not found", project_id)
            return
        mp3_path       = proj.mp3_url or ""
        recording_path = proj.recording_path or ""
        title          = proj.title or "Untitled Episode"
        show_notes_md  = proj.show_notes or ""
        episode_number = proj.episode_number or 0
        _meta          = proj.legacy_metadata or {}

    # ── Vyral hashtags (from Hashtag Lab saved sets) ──────────────────────────
    import json as _json
    _ht_path = DATA_DIR / "social_hashtags.json"
    _yt_tags: list = []
    try:
        if _ht_path.exists():
            _ht_data = _json.loads(_ht_path.read_text())
            _core  = [t.lstrip("#") for t in _ht_data.get("core", [])[:10] if t]
            _niche = [t.lstrip("#") for t in _ht_data.get("niche", [])[:5]  if t]
            _yt_tags = _core + _niche
    except Exception as _ht_err:
        _logger.warning("_distribute_project: could not load hashtags (%s) — using defaults", _ht_err)
    if not _yt_tags:
        _yt_tags = ["podcast", "real estate", "successagent"]

    # ── YouTube chapter text (stored by Ship It in legacy_metadata) ───────────
    _chapter_text = ""
    _stored_chapters = _meta.get("youtube_chapters", [])
    if _stored_chapters:
        _chapter_text = _format_chapters(_stored_chapters)
        _logger.info("_distribute_project: %d chapter markers loaded", len(_stored_chapters))

    # ── Build YouTube long-form description ───────────────────────────────────
    # Structure: chapters → episode summary → links/CTAs → hashtags
    _desc_parts = []

    # 1. Chapters (timestamps)
    if _chapter_text:
        _desc_parts.append(_chapter_text)

    # 2. Episode summary from show notes (first 500 chars of plain text)
    if show_notes_md:
        # Strip markdown headers/bullets for a clean paragraph
        import re as _re
        _plain = _re.sub(r'#{1,6}\s+', '', show_notes_md)
        _plain = _re.sub(r'\*\*(.+?)\*\*', r'\1', _plain).strip()
        _desc_parts.append(_plain[:600] + ("…" if len(_plain) > 600 else ""))

    # 3. CTA block — JP's contact info and links
    _desc_parts.append(
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏠 WORK WITH JP FLUELLEN\n"
        "Backed by tech, trust, and 400+ homes sold since 2011.\n"
        "Serving Wyoming & Missouri.\n\n"
        "📞 Wyoming: +1-307-772-1184\n"
        "📞 Missouri: +1-417-340-1927\n"
        "📧 jp@jpfluellen.com\n\n"
        "👉 Free Home Valuation: https://jpfluellen.com/valuation\n"
        "👉 Connect with JP: https://jpfluellen.com/contact\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎙 ABOUT THE SUCCESS AGENT PODCAST\n"
        "Weekly conversations with business owners, team leaders, CEOs, and real estate professionals.\n"
        "Subscribe to learn how top performers build businesses that work for them.\n\n"
        "🔔 Subscribe so you never miss an episode!\n"
        "👍 Like this video if it added value.\n"
        "💬 Comment below — what was your biggest takeaway?"
    )

    # 4. Hashtags
    _hashtags = " ".join(f"#{t.replace(' ','')}" for t in (_yt_tags[:10] if _yt_tags else []))
    if _hashtags:
        _desc_parts.append(_hashtags)
    _desc_parts.append("#SuccessAgentPodcast #RealEstate #Podcast #RealEstateInvesting")

    _yt_description = "\n\n".join(_desc_parts)

    # ── Markdown → HTML for Buzzsprout description ────────────────────────────
    try:
        from markdown_it import MarkdownIt as _MarkdownIt
        _md = _MarkdownIt()
        description_html = _md.render(show_notes_md) if show_notes_md else ""
    except Exception as _md_err:
        _logger.warning("_distribute_project: markdown-to-HTML failed (%s), using plain text", _md_err)
        description_html = show_notes_md

    if not mp3_path or not Path(mp3_path).exists():
        _logger.warning("_distribute_project: no assembled MP3 for project %s — skipping Buzzsprout", project_id)
        mp3_ready = False
    else:
        mp3_ready = True

    # ── B2: Buzzsprout upload (private draft → scheduler flips public) ────────
    buzzsprout_url     = None
    buzzsprout_ep_id   = None
    if mp3_ready:
        try:
            from pipeline.upload import upload_episode
            bz_result = await upload_episode(
                mp3_path=mp3_path,
                title=title,
                description=description_html,
                episode_number=episode_number,
                private=True,
            )
            if bz_result.get("success"):
                buzzsprout_url   = bz_result.get("url")
                buzzsprout_ep_id = bz_result.get("episode_id")
                _logger.info("_distribute_project: Buzzsprout draft uploaded — ep_id=%s", buzzsprout_ep_id)

                from pipeline.scheduler import add_to_queue
                add_to_queue(
                    job_id=project_id,
                    title=title,
                    episode_number=episode_number,
                    buzzsprout_episode_id=buzzsprout_ep_id,
                    scheduled_at=closing_at_ts,
                    youtube_url=None,
                    source="automation",
                )
            else:
                _logger.warning("_distribute_project: Buzzsprout upload failed — %s", bz_result.get("error"))
        except Exception as _bz_err:
            _logger.error("_distribute_project: Buzzsprout upload exception: %s", _bz_err)

    # ── B3: YouTube main upload (private, with chapters + Vyral hashtags) ─────
    youtube_url    = None
    youtube_vid_id = None
    video_path = recording_path or mp3_path
    if video_path and Path(video_path).exists():
        try:
            from pipeline.youtube import upload_video, is_authorized
            if is_authorized():
                loop = asyncio.get_event_loop()
                yt_result = await loop.run_in_executor(
                    None,
                    lambda: upload_video(
                        video_path=video_path,
                        title=title,
                        description=_yt_description,
                        privacy_status="private",
                        tags=_yt_tags,
                    ),
                )
                if yt_result.get("ok"):
                    youtube_url    = yt_result.get("url")
                    youtube_vid_id = yt_result.get("video_id")
                    _logger.info(
                        "_distribute_project: YouTube main upload OK — video_id=%s tags=%d chapters=%d",
                        youtube_vid_id, len(_yt_tags), len(_stored_chapters),
                    )
                else:
                    _logger.warning("_distribute_project: YouTube upload failed — %s", yt_result.get("error"))
            else:
                _logger.info("_distribute_project: YouTube not authorized — skipping upload")
        except Exception as _yt_err:
            _logger.error("_distribute_project: YouTube upload exception: %s", _yt_err)
    else:
        _logger.info("_distribute_project: no video/audio file found — skipping YouTube upload")

    # ── B3b: YouTube Shorts — upload each rendered clip (private) ─────────────
    _shorts_uploaded = 0
    _yt_authorized = youtube_vid_id is not None  # only run if main upload succeeded
    if _yt_authorized:
        try:
            from db.models import Clip as _Clip
            from sqlalchemy import select as _clip_select

            async with _async_session() as _cs:
                _clips = (
                    await _cs.execute(
                        _clip_select(_Clip).where(_Clip.project_id == pid)
                    )
                ).scalars().all()

            from pipeline.youtube import upload_video as _yt_upload

            for _clip in _clips:
                _rendered = _clip.rendered_url or ""
                if not _rendered or not Path(_rendered).exists():
                    continue

                _hook = (_clip.hook_text or title)[:100]
                _short_title = f"{_hook} | {title}"[:100]
                _short_desc = (
                    f"{_hook}\n\n"
                    f"Full episode: {youtube_url or 'Coming soon'}\n\n"
                    + show_notes_md[:500]
                )

                try:
                    loop = asyncio.get_event_loop()
                    _sr = await loop.run_in_executor(
                        None,
                        lambda _rp=_rendered, _st=_short_title, _sd=_short_desc: _yt_upload(
                            video_path=_rp,
                            title=_st,
                            description=_sd,
                            privacy_status="private",
                            tags=_yt_tags + ["Shorts", "YouTubeShorts"],
                            category_id="22",
                        ),
                    )
                    if _sr.get("ok"):
                        _shorts_uploaded += 1
                        _logger.info(
                            "_distribute_project: Short uploaded — video_id=%s clip=%s",
                            _sr.get("video_id"), _clip.id,
                        )
                        # Store short URL in clip record via rendered_url annotation
                        # (Full youtube_url column on Clip is deferred — DEFERRED OPT-3)
                    else:
                        _logger.warning(
                            "_distribute_project: Short upload failed for clip %s — %s",
                            _clip.id, _sr.get("error"),
                        )
                except Exception as _sr_err:
                    _logger.warning("_distribute_project: Short upload exception clip %s: %s", _clip.id, _sr_err)

            if _shorts_uploaded:
                _logger.info("_distribute_project: %d Shorts uploaded for project %s", _shorts_uploaded, project_id)
        except Exception as _shorts_err:
            _logger.error("_distribute_project: Shorts loop failed: %s", _shorts_err)

    # ── B4: Persist distribution links to project ─────────────────────────────
    if buzzsprout_url or buzzsprout_ep_id or youtube_url or youtube_vid_id:
        try:
            async with _async_session() as _ps:
                _proj = (await _ps.execute(_select(Project).where(Project.id == pid))).scalar_one_or_none()
                if _proj:
                    if buzzsprout_url:
                        _proj.buzzsprout_url = buzzsprout_url
                    if buzzsprout_ep_id:
                        _proj.buzzsprout_episode_id = buzzsprout_ep_id
                    if youtube_url:
                        _proj.youtube_url = youtube_url
                    if youtube_vid_id:
                        _proj.youtube_video_id = youtube_vid_id
                    await _ps.commit()
                    _logger.info(
                        "_distribute_project: distribution links persisted — project=%s shorts=%d",
                        project_id, _shorts_uploaded,
                    )
        except Exception as _persist_err:
            _logger.error("_distribute_project: failed to persist distribution links: %s", _persist_err)


@app.post("/api/projects/{project_id}/schedule-closing")
async def schedule_closing(project_id: str, request: Request):
    """
    Step 4 of the Ship It wizard — set the Closing date.

    Body: {
      "closing_at": "2026-05-30T08:00:00Z",   // ISO timestamp
      "platforms": ["linkedin", "facebook"],   // optional, defaults to all connected
      "guest_ids": ["abc123"]                  // optional, override project guest_ids
    }

    On call:
      1. Validate Project is in 'review' status
      2. Create Post rows for main episode + approved clips
      3. Set project.closing_scheduled_at
      4. Transition Project → 'scheduled'
      5. Guest CRM: update linked guests to 'recorded' status

    The actual publish fires when closing_scheduled_at arrives (handled by
    the existing APScheduler cron in pipeline/scheduler.py).

    Construction vocabulary: this is "Set the closing date."
    """
    from db.engine import async_session as _async_session
    from db.models import Project, Clip
    from sqlalchemy import select
    import uuid as _uuid
    from datetime import datetime, timezone

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        return JSONResponse({"error": "Invalid project ID"}, status_code=400)

    body = await request.json()
    closing_at_str = body.get("closing_at")
    platforms = body.get("platforms") or ["linkedin", "facebook", "instagram"]
    guest_id_overrides = body.get("guest_ids")

    if not closing_at_str:
        return JSONResponse({"error": "closing_at required (ISO timestamp)"}, status_code=400)

    try:
        closing_at = datetime.fromisoformat(closing_at_str.replace("Z", "+00:00"))
    except ValueError:
        return JSONResponse({"error": "Invalid closing_at format — use ISO 8601"}, status_code=400)

    async with _async_session() as session:
        from sqlalchemy import func
        project = (await session.execute(select(Project).where(Project.id == pid))).scalar_one_or_none()
        if not project:
            return JSONResponse({"error": "Project not found"}, status_code=404)

        if project.status != "review":
            return JSONResponse(
                {"error": f"Project must be in 'review' to schedule closing. Current: {project.status}"},
                status_code=400,
            )

        # Update guest_ids if overridden
        if guest_id_overrides is not None:
            project.guest_ids = guest_id_overrides

        # Auto-assign episode_number if not already set
        # Queries MAX from projects table; falls back to 100 so new episodes start at 101+
        if not project.episode_number:
            max_ep = (await session.execute(
                select(func.max(Project.episode_number))
            )).scalar()
            project.episode_number = (max_ep or 100) + 1

        # Set closing date + transition
        project.closing_scheduled_at = closing_at
        project.status = "scheduled"
        project.wizard_step = 4

        await session.commit()
        project_data = _project_to_dict(project)

    closing_at_ts = closing_at.timestamp()

    # Guest CRM: update linked guests → 'recorded' (async, non-fatal)
    asyncio.create_task(_update_guest_statuses(
        project_id=project_id,
        guest_ids=project_data.get("guest_ids", []),
        new_status="recorded",
    ))

    # Create Post rows for the episode
    asyncio.create_task(_create_closing_posts(
        project_id=project_id,
        project_uuid=pid,
        platforms=platforms,
        closing_at=closing_at,
        location_id=project_data.get("location_id"),
        title=project_data.get("title", ""),
        show_notes=project_data.get("show_notes", ""),
    ))

    # Distribution: upload to Buzzsprout (private draft) + YouTube (private)
    # _distribute_project persists buzzsprout_url, buzzsprout_episode_id,
    # youtube_url, youtube_video_id back to the project record.
    asyncio.create_task(_distribute_project(
        project_id=project_id,
        closing_at_ts=closing_at_ts,
    ))

    return JSONResponse({
        **project_data,
        "message": f"Closing lined up for {closing_at.strftime('%B %d at %-I:%M %p')}. "
                   f"Posts go up across {len(platforms)} platforms. "
                   f"Episode uploading to Buzzsprout and YouTube in the background.",
    })


async def _update_guest_statuses(
    project_id: str,
    guest_ids: list,
    new_status: str,
) -> None:
    """Update linked guests in guests.json when Project status changes."""
    if not guest_ids:
        return

    guests_file = Path(__file__).parent / "data" / "guests.json"
    if not guests_file.exists():
        return

    try:
        with open(guests_file) as f:
            guests = json.load(f)

        updated = False
        for g in guests:
            if g.get("id") in guest_ids:
                # Only advance status (never go backwards)
                STATUS_ORDER = ["prospect", "booked", "recorded", "aired"]
                current_idx = STATUS_ORDER.index(g.get("status", "prospect")) if g.get("status") in STATUS_ORDER else 0
                new_idx = STATUS_ORDER.index(new_status) if new_status in STATUS_ORDER else 0
                if new_idx > current_idx:
                    g["status"] = new_status
                    g["project_id"] = project_id
                    updated = True

        if updated:
            with open(guests_file, "w") as f:
                json.dump(guests, f, indent=2)

    except Exception as _err:
        import logging
        logging.getLogger("podclick.projects").warning("Guest CRM update failed: %s", _err)


async def _create_closing_posts(
    project_id: str,
    project_uuid,
    platforms: list,
    closing_at,
    location_id: str,
    title: str,
    show_notes: str,
) -> None:
    """
    Create Post rows for the episode closing.
    One Post with per-platform PostVariant entries.
    Uses existing posts table from Phase 2.
    """
    from db.engine import async_session as _async_session
    from db.models import Post, PostVariant
    from sqlalchemy import insert
    import uuid as _uuid

    try:
        base_caption = show_notes[:500] if show_notes else f"New episode: {title}"

        async with _async_session() as session:
            # Main episode post
            post_id = _uuid.uuid4()
            post = Post(
                id=post_id,
                location_id=location_id,
                project_id=project_uuid,
                bucket="podcast",
                base_caption=base_caption,
                scheduled_at=closing_at,
                status="scheduled",
                source="clip_distributor",
            )
            session.add(post)

            # Per-platform variants
            PLATFORM_OFFSETS = {
                "linkedin": 0, "x": 60, "facebook": 120,
                "instagram": 180, "tiktok": 240, "youtube": 300, "gmb": 360,
            }
            from datetime import timedelta
            for plat in platforms:
                offset_s = PLATFORM_OFFSETS.get(plat, 0)
                variant_time = closing_at + timedelta(seconds=offset_s)
                variant = PostVariant(
                    post_id=post_id,
                    platform=plat,
                    caption=base_caption,
                    platform_specific={"stagger_offset_s": offset_s},
                )
                session.add(variant)

            await session.commit()

    except Exception as _err:
        import logging
        logging.getLogger("podclick.projects").warning("Closing post creation failed: %s", _err)


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    return HTMLResponse((FRONTEND_DIR / "index.html").read_text())


@app.get("/studio", response_class=HTMLResponse)
async def serve_studio():
    """Solo studio — local camera/mic/screen capture in the browser
    with a voice-activity-driven teleprompter. Records locally so
    the captured file is independent of network quality, then hands
    the WebM to the existing /api/run pipeline via the Publish button.
    """
    return HTMLResponse((FRONTEND_DIR / "studio.html").read_text())


@app.get("/projects", response_class=HTMLResponse)
async def serve_projects_list():
    """Phase 5 — Job Site: list all projects."""
    return HTMLResponse((FRONTEND_DIR / "projects.html").read_text())


@app.get("/vsl-editor", response_class=HTMLResponse)
async def serve_vsl_editor():
    """VSL Auto-Editor — script-driven video editing with graphics overlays."""
    return HTMLResponse((FRONTEND_DIR / "vsl-editor.html").read_text())


@app.get("/project/{project_id}", response_class=HTMLResponse)
async def serve_project_wizard(project_id: str):
    """Phase 5 — Ship It wizard. 4-step review before Closing."""
    return HTMLResponse((FRONTEND_DIR / "project.html").read_text())


@app.post("/api/process")
async def start_processing(
    audio:          List[UploadFile] = File(...),
    clip_types:     str              = Form(""),     # comma-separated types matching audio order
    model_size:     str              = Form("base"),
    podcast_name:   str              = Form(""),
    studio_mode:              str = Form("audio"),   # "audio" | "video" | "both"
    subtitle_style:           str = Form("reels"),   # "reels" | "youtube" | "clean"
    episode_number_override:  int = Form(0),         # 0 = auto-increment from history
):
    """
    Accept one or more audio/video/image files.
    clip_types: comma-separated list matching the order of files.
      e.g. "intro,main,commercial,outro"
    If omitted, types are auto-detected from filenames.
    studio_mode: "audio" (MP3 only), "video" (MP4 only), "both" (MP3 + MP4)
    """
    from pipeline.assemble import detect_clip_type

    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

    job_id  = str(uuid.uuid4())
    types   = [t.strip() for t in clip_types.split(",")] if clip_types else []

    # Write uploads to data/uploads/<job_id>/ instead of TMPDIR. macOS
    # was rotating /var/folders/.../T/ files between upload and
    # assembly, and a sibling job's finally-cleanup compounded the
    # problem. Persistent paths mean a failed job can be retried with
    # no re-upload, and a server restart mid-pipeline doesn't strand
    # the clips.
    job_upload_dir = UPLOADS_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)

    saved_clips = []
    for i, upload in enumerate(audio):
        suffix   = Path(upload.filename).suffix or ".mp3"
        is_image = suffix.lower() in IMAGE_EXTS

        # Preserve the original basename for debuggability; prefix with
        # an index so duplicate filenames don't collide. Sanitize by
        # taking only the leaf component (defends against "../" etc.).
        safe_name = Path(upload.filename).name or f"clip{suffix}"
        dest_path = job_upload_dir / f"{i:02d}_{safe_name}"
        async with aiofiles.open(dest_path, "wb") as f:
            await f.write(await upload.read())

        # Use provided type if given, else auto-detect
        clip_type = types[i] if i < len(types) else detect_clip_type(upload.filename)

        saved_clips.append({
            "path":     str(dest_path),
            "type":     clip_type,
            "filename": upload.filename,
            "is_image": is_image,
        })

    filenames = [c["filename"] for c in saved_clips]
    main_file = next((c["filename"] for c in saved_clips if c["type"] == "main"), filenames[0])

    jobs[job_id] = {
        "job_id":                job_id,
        "filename":              main_file,
        "clips":                 [{"filename": c["filename"], "type": c["type"]} for c in saved_clips],
        "status":                "queued",
        "step":                  "queued",
        "log":                   [],
        "transcript":            None,
        "words":                 [],
        "segments":              [],
        "title":                 None,
        "description":           None,
        "episode_number":        None,
        "mp3_path":              None,
        "mp4_path":              None,
        "srt_path":              None,
        "studio_mode":              studio_mode,
        "subtitle_style":           subtitle_style,
        "episode_number_override":  episode_number_override,
        "fillers_removed":       0,
        "duration_saved":        0.0,
        "final_duration":        None,
        "commercial_inserted_at": None,
        "has_intro":             False,
        "has_commercial":        False,
        "has_outro":             False,
        "links":                 [],
        "error":                 None,
    }
    job_ws_queues[job_id] = asyncio.Queue()

    asyncio.ensure_future(run_pipeline(job_id, saved_clips, model_size, podcast_name, studio_mode, subtitle_style, episode_number_override))

    return JSONResponse({"job_id": job_id, "clips": jobs[job_id]["clips"]})


@app.post("/api/retry/{old_job_id}")
async def retry_from_uploads(
    old_job_id: str,
    podcast_name:   str = Form(""),
    model_size:     str = Form("base"),
    studio_mode:    str = Form("both"),
    subtitle_style: str = Form("youtube"),
    episode_number_override: int = Form(0),
    clip_types:     str = Form(""),  # comma-separated override, matches sorted file order
):
    """Resume a job whose files are still in data/uploads/<old_job_id>/.

    Use case: uvicorn --reload (or a crash, or any worker death)
    killed an in-flight job. The in-memory jobs[] dict is wiped, but
    the upload files survive on disk thanks to the persistence work.
    This endpoint reconstructs the clips list from filesystem state
    and kicks off a fresh pipeline run — no re-upload required.

    Clip type is auto-detected from filename (intro/main/commercial/
    outro) using the same heuristic the upload endpoint uses. Files
    are sorted by their NN_ prefix so the original order is honored.
    """
    from pipeline.assemble import detect_clip_type

    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

    src_dir = UPLOADS_DIR / old_job_id
    if not src_dir.is_dir():
        return JSONResponse(
            {"error": f"No upload dir for job {old_job_id}. Files may have been swept."},
            status_code=404,
        )

    # Sort files by the NN_ prefix saved at upload time. Strips it
    # when computing the user-facing filename so detect_clip_type sees
    # the original name (e.g. "Panda Dash Commercial.mp3").
    entries = sorted(src_dir.iterdir(), key=lambda p: p.name)
    # Optional type override — same shape /api/run accepts. Lets the
    # caller fix a misdetection (e.g. an "intro" filename that lacks
    # the substring "intro" — typos defeat auto-detect).
    type_overrides = [t.strip() for t in clip_types.split(",")] if clip_types else []
    saved_clips = []
    for i, p in enumerate([e for e in entries if e.is_file()]):
        suffix = p.suffix.lower()
        # Strip the "NN_" prefix to recover the user's original filename.
        display = p.name.split("_", 1)[1] if "_" in p.name[:4] else p.name
        if i < len(type_overrides) and type_overrides[i]:
            clip_type = type_overrides[i]
        else:
            clip_type = detect_clip_type(display)
        saved_clips.append({
            "path":     str(p),
            "type":     clip_type,
            "filename": display,
            "is_image": suffix in IMAGE_EXTS,
        })

    if not saved_clips:
        return JSONResponse(
            {"error": f"Upload dir {old_job_id} is empty."},
            status_code=400,
        )

    # New job_id so the old (dead) state isn't reused. The files keep
    # living at data/uploads/<old_job_id>/ — saved_clips paths point
    # there. Cleanup on success will delete the OLD path (different
    # job_id), so we explicitly skip the empty-dir removal in
    # run_pipeline's finally-block for retry runs by writing to a
    # fresh upload dir? No — simpler: just reuse the old paths and
    # let the finally-block log "best-effort" failures harmlessly.
    job_id = str(uuid.uuid4())
    filenames = [c["filename"] for c in saved_clips]
    main_file = next((c["filename"] for c in saved_clips if c["type"] == "main"), filenames[0])

    jobs[job_id] = {
        "job_id":                job_id,
        "filename":              main_file,
        "clips":                 [{"filename": c["filename"], "type": c["type"]} for c in saved_clips],
        "status":                "queued",
        "step":                  "queued",
        "log":                   [f"Retry of {old_job_id} — reusing files in data/uploads/{old_job_id}/"],
        "transcript":            None,
        "words":                 [],
        "segments":              [],
        "title":                 None,
        "description":           None,
        "episode_number":        None,
        "mp3_path":              None,
        "mp4_path":              None,
        "srt_path":              None,
        "studio_mode":              studio_mode,
        "subtitle_style":           subtitle_style,
        "episode_number_override":  episode_number_override,
        "fillers_removed":       0,
        "duration_saved":        0.0,
        "final_duration":        None,
        "commercial_inserted_at": None,
        "has_intro":             any(c["type"] == "intro" for c in saved_clips),
        "has_commercial":        any(c["type"] == "commercial" for c in saved_clips),
        "has_outro":             any(c["type"] == "outro" for c in saved_clips),
        "links":                 [],
        "error":                 None,
        "retry_of":              old_job_id,
    }
    job_ws_queues[job_id] = asyncio.Queue()

    asyncio.ensure_future(run_pipeline(
        job_id, saved_clips, model_size, podcast_name,
        studio_mode, subtitle_style, episode_number_override,
    ))

    return JSONResponse({
        "job_id":   job_id,
        "retry_of": old_job_id,
        "clips":    jobs[job_id]["clips"],
    })


@app.get("/api/status")
async def get_status():
    """Bot-friendly status endpoint — returns active jobs summary."""
    active = [
        {
            "job_id":   jid,
            "filename": j.get("filename", ""),
            "status":   j.get("status", ""),
            "step":     j.get("step", ""),
        }
        for jid, j in jobs.items()
        if j.get("status") not in ("ready", "uploaded", "scheduled", "error", "upload_error")
    ]
    return JSONResponse({"active_jobs": active, "total_jobs": len(jobs)})


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = _get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return JSONResponse({k: v for k, v in job.items() if k not in ("words", "segments")})


@app.post("/api/upload")
async def start_upload(payload: dict):
    job_id = payload.get("job_id")
    job    = _get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    if job["status"] not in ("ready", "upload_error"):
        return JSONResponse({"error": "Job not ready for upload"}, status_code=400)

    title          = payload.get("title",          job["title"])
    description    = payload.get("description",    job["description"])
    episode_number = int(payload.get("episode_number", job["episode_number"]))
    guest_id       = payload.get("guest_id", "")
    mp3_path       = job["mp3_path"]

    if not mp3_path or not Path(mp3_path).exists():
        return JSONResponse({"error": "Processed MP3 not found"}, status_code=400)

    job["status"] = "uploading"
    await _send_progress(job_id, "Uploading to Buzzsprout…", "uploading")

    async def do_upload():
        from pipeline.upload import upload_episode
        loop = asyncio.get_event_loop()

        def mk_upload_cb():
            def cb(msg):
                asyncio.run_coroutine_threadsafe(
                    _send_progress(job_id, msg),
                    loop,
                )
            return cb

        result = await upload_episode(
            mp3_path=mp3_path,
            title=title,
            description=description,
            episode_number=episode_number,
            progress_cb=mk_upload_cb(),
        )
        if result["success"]:
            job["status"]      = "uploaded"
            job["spotify_url"] = result["url"]
            episodes = _load_episodes()
            if not any(e.get("job_id") == job_id for e in episodes):
                episodes.append({
                    "job_id":          job_id,
                    "episode_number":  episode_number,
                    "title":           title,
                    "description":     description,
                    "spotify_url":     result["url"],
                    "filename":        job["filename"],
                    "fillers_removed": job["fillers_removed"],
                    "duration_saved":  job["duration_saved"],
                    "has_intro":       job.get("has_intro", False),
                    "has_commercial":  job.get("has_commercial", False),
                    "has_outro":       job.get("has_outro", False),
                })
                _save_episodes(episodes)
            _persist_job(job_id)
            await _send_progress(job_id, "✓ Uploaded to Buzzsprout! Distributing to Spotify now…", "uploaded")

            # Auto-link guest with episode URLs
            guest_name = ""
            if guest_id:
                guests = _load_guests()
                g = next((x for x in guests if x["id"] == guest_id), None)
                if g:
                    guest_name = g.get("name", "")
                    g["episode_number"]      = episode_number
                    g["episode_title"]       = title
                    g["episode_url_spotify"] = result["url"]
                    if g.get("status") in ("booked", "recorded"):
                        g["status"] = "aired"
                    _save_guests(guests)

            # Telegram notification
            from pipeline.telegram import send as tg_send
            tg_msg = (
                f"🎙️ *EP. {episode_number} uploaded!*\n"
                f"_{title}_\n\n"
                f"✅ Live on Buzzsprout — distributing to Spotify now.\n"
                f"Fillers removed: {job.get('fillers_removed', 0)} · "
                f"Time saved: {job.get('duration_saved', 0):.1f}s"
            )
            if guest_name:
                tg_msg += f"\n\n📦 *Don't forget to send assets to {guest_name}!*\nhttp://localhost:8765 → Guests tab"
            await tg_send(tg_msg)
        else:
            job["status"] = "upload_error"
            job["error"]  = result["error"]
            await _send_progress(job_id, f"Upload failed: {result['error']}", "upload_error")
            from pipeline.telegram import send as tg_send
            await tg_send(f"⚠️ *EP. {episode_number} upload failed*\n`{result['error']}`")
        await _send_result(job_id, {"status": job["status"]})

    asyncio.ensure_future(do_upload())
    return JSONResponse({"status": "uploading"})


@app.post("/api/schedule")
async def schedule_episode(payload: dict):
    """
    Upload episode as a private Buzzsprout draft and add it to the release queue.
    payload: {job_id, title?, description?, episode_number?, scheduled_at (unix ts),
              youtube_url?, source?}
    """
    job_id = payload.get("job_id")
    job    = _get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    if job["status"] not in ("ready", "upload_error"):
        return JSONResponse({"error": "Job not ready"}, status_code=400)

    scheduled_at = payload.get("scheduled_at")
    if not scheduled_at:
        return JSONResponse({"error": "scheduled_at (unix timestamp) required"}, status_code=400)

    title          = payload.get("title",          job["title"])
    description    = payload.get("description",    job["description"])
    episode_number = int(payload.get("episode_number", job["episode_number"]))
    guest_id       = payload.get("guest_id", "")
    youtube_url    = payload.get("youtube_url")
    source         = payload.get("source", "manual")
    mp3_path       = job["mp3_path"]

    if not mp3_path or not Path(mp3_path).exists():
        return JSONResponse({"error": "Processed MP3 not found"}, status_code=400)

    job["status"] = "uploading"
    await _send_progress(job_id, "Uploading private draft to Buzzsprout for scheduling…", "uploading")

    async def do_schedule():
        from pipeline.upload import upload_episode
        from pipeline.scheduler import add_to_queue

        loop = asyncio.get_event_loop()
        def mk_cb():
            def cb(msg):
                asyncio.run_coroutine_threadsafe(_send_progress(job_id, msg), loop)
            return cb

        result = await upload_episode(
            mp3_path=mp3_path,
            title=title,
            description=description,
            episode_number=episode_number,
            private=True,
            progress_cb=mk_cb(),
        )
        if result["success"]:
            job["status"]      = "scheduled"
            job["spotify_url"] = result["url"]
            if youtube_url:
                job["youtube_url"] = youtube_url
            entry = add_to_queue(
                job_id=job_id,
                title=title,
                episode_number=episode_number,
                buzzsprout_episode_id=result["episode_id"],
                scheduled_at=float(scheduled_at),
                youtube_url=youtube_url,
                source=source,
            )
            _persist_job(job_id)
            from datetime import datetime
            dt_str = datetime.fromtimestamp(float(scheduled_at)).strftime("%b %-d at %-I:%M %p")
            await _send_progress(job_id, f"✓ Scheduled for {dt_str}", "scheduled")
            # Auto-link guest with scheduled episode
            guest_name = ""
            if guest_id:
                guests = _load_guests()
                g = next((x for x in guests if x["id"] == guest_id), None)
                if g:
                    guest_name = g.get("name", "")
                    g["episode_number"] = episode_number
                    g["episode_title"]  = title
                    if g.get("status") in ("booked", "recorded"):
                        g["status"] = "aired"
                    _save_guests(guests)

            from pipeline.telegram import send as tg_send
            tg_msg = (
                f"📅 *EP. {episode_number} scheduled!*\n"
                f"_{title}_\n\n"
                f"Publishes: *{dt_str}*\n"
                f"Queue ID: `{entry['entry_id']}`"
            )
            if youtube_url:
                tg_msg += f"\n▶️ YouTube: {youtube_url}"
            if guest_name:
                tg_msg += f"\n\n📦 *After it publishes, send assets to {guest_name}!*\nhttp://localhost:8765 → Guests tab"
            await tg_send(tg_msg)
        else:
            job["status"] = "upload_error"
            job["error"]  = result["error"]
            await _send_progress(job_id, f"Schedule failed: {result['error']}", "upload_error")
        await _send_result(job_id, {"status": job["status"]})

    asyncio.ensure_future(do_schedule())
    return JSONResponse({"status": "scheduling"})


# ── Queue endpoints ────────────────────────────────────────────────────────────

@app.get("/api/queue")
async def get_queue():
    from pipeline.scheduler import list_queue
    return JSONResponse(list_queue())


@app.delete("/api/queue/{entry_id}")
async def delete_queue_entry(entry_id: str):
    from pipeline.scheduler import remove_from_queue
    if remove_from_queue(entry_id):
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Entry not found"}, status_code=404)


@app.patch("/api/queue/{entry_id}")
async def reschedule_entry(entry_id: str, payload: dict):
    from pipeline.scheduler import reschedule
    new_ts = payload.get("scheduled_at")
    if not new_ts:
        return JSONResponse({"error": "scheduled_at required"}, status_code=400)
    entry = reschedule(entry_id, float(new_ts))
    if not entry:
        return JSONResponse({"error": "Entry not found or not reschedulable"}, status_code=404)
    return JSONResponse(entry)


@app.post("/api/queue/{entry_id}/publish")
async def publish_now(entry_id: str):
    """Bypass the schedule and publish immediately."""
    from pipeline.scheduler import queue as sq, save_queue
    from pipeline.upload import flip_to_public
    entry = sq.get(entry_id)
    if not entry:
        return JSONResponse({"error": "Entry not found"}, status_code=404)
    entry["status"] = "publishing"
    save_queue()
    result = await flip_to_public(entry["buzzsprout_episode_id"])
    if result["success"]:
        from datetime import datetime
        entry["status"]       = "published"
        entry["published_at"] = datetime.now().isoformat()
        save_queue()
        return JSONResponse({"ok": True})
    entry["status"] = "failed"
    entry["error"]  = result.get("error")
    save_queue()
    return JSONResponse({"error": entry["error"]}, status_code=500)


@app.post("/api/mark_published")
async def mark_published(payload: dict):
    """
    Called by the Upload Assistant when the user manually clicks 'Mark as Published'.
    Records the episode in episodes.json exactly like an automated upload would.
    """
    job_id = payload.get("job_id")
    job    = _get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    title          = payload.get("title",          job.get("title", ""))
    description    = payload.get("description",    job.get("description", ""))
    episode_number = int(payload.get("episode_number", job.get("episode_number", 1)))

    episodes = _load_episodes()
    # Avoid duplicate entries for the same job
    if not any(e.get("job_id") == job_id for e in episodes):
        episodes.append({
            "job_id":          job_id,
            "episode_number":  episode_number,
            "title":           title,
            "description":     description,
            "spotify_url":     job.get("spotify_url", "https://creators.spotify.com/pod/dashboard/episodes"),
            "filename":        job.get("filename", ""),
            "fillers_removed": job.get("fillers_removed", 0),
            "duration_saved":  job.get("duration_saved", 0.0),
            "has_intro":       job.get("has_intro", False),
            "has_commercial":  job.get("has_commercial", False),
            "has_outro":       job.get("has_outro", False),
        })
        _save_episodes(episodes)

    job["status"] = "uploaded"
    return JSONResponse({"ok": True})


@app.post("/api/timestamps/{job_id}")
async def generate_timestamps_endpoint(job_id: str):
    """Re-generate chapter timestamps for a completed job on demand."""
    job = _get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    segments = job.get("segments", [])
    if not segments:
        return JSONResponse({"error": "No segment data — re-process the episode to enable timestamps"}, status_code=400)

    loop = asyncio.get_event_loop()

    def do_timestamps():
        from pipeline.content import generate_timestamps
        return generate_timestamps(segments)

    try:
        timestamps_block = await loop.run_in_executor(None, do_timestamps)
        return JSONResponse({"timestamps": timestamps_block})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/episodes")
async def list_episodes():
    return JSONResponse(_load_episodes())


@app.patch("/api/episodes/{job_id}")
async def patch_episode(job_id: str, request: Request):
    """Update editable fields on a saved episode (episode_number, title, description)."""
    data     = await request.json()
    episodes = _load_episodes()
    match    = next((e for e in episodes if e.get("job_id") == job_id), None)
    if not match:
        return JSONResponse({"error": "Episode not found"}, status_code=404)
    if "episode_number" in data:
        match["episode_number"] = int(data["episode_number"])
    if "title" in data:
        match["title"] = str(data["title"]).strip()
    _save_episodes(episodes)
    return JSONResponse(match)


@app.delete("/api/episodes/{job_id}")
async def delete_episode(job_id: str):
    """Remove a test/duplicate episode from history."""
    episodes = _load_episodes()
    before   = len(episodes)
    episodes = [e for e in episodes if e.get("job_id") != job_id]
    if len(episodes) == before:
        return JSONResponse({"error": "Episode not found"}, status_code=404)
    _save_episodes(episodes)
    return JSONResponse({"ok": True, "deleted": job_id})


# ── Bumper Library ─────────────────────────────────────────────────────────────

@app.get("/api/library")
async def get_library(slot_type: str = ""):
    items = _load_library()
    if slot_type:
        items = [i for i in items if i.get("slot_type") == slot_type]
    return JSONResponse(items)


@app.post("/api/library")
async def add_to_library(
    file:      UploadFile = File(...),
    slot_type: str        = Form(...),   # intro | main | commercial | outro
    label:     str        = Form(""),    # friendly name; defaults to filename
):
    """Save an audio file to the persistent bumper library."""
    item_id  = str(uuid.uuid4())[:8]
    suffix   = Path(file.filename or "audio").suffix or ".mp3"
    dest     = LIBRARY_DIR / f"{item_id}{suffix}"

    async with aiofiles.open(str(dest), "wb") as f:
        await f.write(await file.read())

    size_bytes = dest.stat().st_size
    item = {
        "id":        item_id,
        "label":     label.strip() or Path(file.filename or "").stem or item_id,
        "filename":  file.filename or dest.name,
        "slot_type": slot_type,
        "size":      size_bytes,
        "path":      str(dest),
        "created_at": __import__("datetime").datetime.now().isoformat(),
    }
    items = _load_library()
    items.append(item)
    _save_library(items)
    return JSONResponse(item, status_code=201)


@app.delete("/api/library/{item_id}")
async def delete_library_item(item_id: str):
    items = _load_library()
    match = next((i for i in items if i["id"] == item_id), None)
    if not match:
        return JSONResponse({"error": "Not found"}, status_code=404)
    # Remove the file from disk
    try:
        Path(match["path"]).unlink(missing_ok=True)
    except Exception:
        pass
    items = [i for i in items if i["id"] != item_id]
    _save_library(items)
    return JSONResponse({"ok": True})


from fastapi.responses import FileResponse

@app.get("/api/library/{item_id}/file")
async def serve_library_file(item_id: str):
    """Serve the audio file for browser preview / slot loading."""
    items = _load_library()
    match = next((i for i in items if i["id"] == item_id), None)
    if not match or not Path(match["path"]).exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(match["path"], media_type="audio/mpeg",
                        filename=match["filename"])


# ── YouTube Studio — AI Persona Library ──────────────────────────────────────

@app.get("/api/yt/ai-persona")
async def yt_ai_persona_get():
    """Return uploaded AI persona photos for thumbnail generation."""
    store = _load_ai_persona()
    photos = [_persona_public(p) for p in store.get("photos", []) if Path(p.get("path", "")).exists()]
    return JSONResponse({"photos": photos})


@app.post("/api/yt/ai-persona/photos")
async def yt_ai_persona_upload(
    files: List[UploadFile] = File(...),
    shot_type: str = Form("headshot"),
    label: str = Form(""),
):
    """Upload headshots/body shots for the AI Persona thumbnail library."""
    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    normalized_type = shot_type if shot_type in {"headshot", "body", "expression", "other"} else "other"
    store = _load_ai_persona()
    saved = []

    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower() or ".jpg"
        if suffix not in allowed:
            return JSONResponse({"error": f"Unsupported image type: {suffix}"}, status_code=400)

        item_id = str(uuid.uuid4())[:8]
        dest = AI_PERSONA_DIR / f"{item_id}{suffix}"
        async with aiofiles.open(str(dest), "wb") as f:
            await f.write(await upload.read())

        item = {
            "id": item_id,
            "label": label.strip() or Path(upload.filename or "").stem or f"{normalized_type} {item_id}",
            "filename": upload.filename or dest.name,
            "shot_type": normalized_type,
            "size": dest.stat().st_size,
            "path": str(dest),
            "created_at": __import__("datetime").datetime.now().isoformat(),
        }
        store.setdefault("photos", []).append(item)
        saved.append(_persona_public(item))

    _save_ai_persona(store)
    return JSONResponse({"photos": saved}, status_code=201)


@app.delete("/api/yt/ai-persona/photos/{photo_id}")
async def yt_ai_persona_delete(photo_id: str):
    store = _load_ai_persona()
    photos = store.get("photos", [])
    match = next((p for p in photos if p.get("id") == photo_id), None)
    if not match:
        return JSONResponse({"error": "Photo not found"}, status_code=404)
    try:
        Path(match.get("path", "")).unlink(missing_ok=True)
    except Exception:
        pass
    store["photos"] = [p for p in photos if p.get("id") != photo_id]
    _save_ai_persona(store)
    return JSONResponse({"ok": True, "deleted": photo_id})


@app.get("/api/yt/ai-persona/photos/{photo_id}")
async def yt_ai_persona_photo(photo_id: str):
    store = _load_ai_persona()
    match = next((p for p in store.get("photos", []) if p.get("id") == photo_id), None)
    if not match or not Path(match.get("path", "")).exists():
        return JSONResponse({"error": "Photo not found"}, status_code=404)
    suffix = Path(match["path"]).suffix.lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    return FileResponse(match["path"], media_type=media_type, filename=match.get("filename") or Path(match["path"]).name)


# ── Show Profiles API ──────────────────────────────────────────────────────────

@app.get("/api/profiles")
async def get_profiles():
    """Return all profiles and the active_id."""
    return JSONResponse(_load_profiles_store())


@app.post("/api/profiles")
async def create_profile(request: Request):
    """Create a new show profile."""
    data  = await request.json()
    store = _load_profiles_store()
    pid   = str(uuid.uuid4())[:8]
    profile = {
        "id":                 pid,
        "name":               data.get("name", "My Podcast").strip(),
        "footer_template":    data.get("footer_template", "").strip(),
        "patreon_url":        data.get("patreon_url", "").strip(),
        "patreon_cta":        data.get("patreon_cta", "").strip(),
        "merch_url":          data.get("merch_url", "").strip(),
        "merch_label":        data.get("merch_label", "").strip(),
        "apple_podcasts_url": data.get("apple_podcasts_url", "").strip(),
        "created_at":         __import__("datetime").datetime.now().isoformat(),
    }
    store["profiles"].append(profile)
    # Auto-activate if it's the first profile
    if not store.get("active_id"):
        store["active_id"] = pid
    _save_profiles_store(store)
    return JSONResponse(profile, status_code=201)


@app.put("/api/profiles/{profile_id}")
async def update_profile(profile_id: str, request: Request):
    """Update an existing show profile."""
    data  = await request.json()
    store = _load_profiles_store()
    match = next((p for p in store["profiles"] if p["id"] == profile_id), None)
    if not match:
        return JSONResponse({"error": "Profile not found"}, status_code=404)
    fields = ["name", "footer_template", "patreon_url", "patreon_cta",
              "merch_url", "merch_label", "apple_podcasts_url"]
    for f in fields:
        if f in data:
            match[f] = data[f].strip() if isinstance(data[f], str) else data[f]
    _save_profiles_store(store)
    return JSONResponse(match)


@app.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: str):
    """Delete a show profile."""
    store = _load_profiles_store()
    store["profiles"] = [p for p in store["profiles"] if p["id"] != profile_id]
    if store.get("active_id") == profile_id:
        store["active_id"] = store["profiles"][0]["id"] if store["profiles"] else None
    _save_profiles_store(store)
    return JSONResponse({"ok": True})


@app.post("/api/profiles/{profile_id}/activate")
async def activate_profile(profile_id: str):
    """Set the active show profile."""
    store = _load_profiles_store()
    if not any(p["id"] == profile_id for p in store["profiles"]):
        return JSONResponse({"error": "Profile not found"}, status_code=404)
    store["active_id"] = profile_id
    _save_profiles_store(store)
    return JSONResponse({"ok": True, "active_id": profile_id})


# ── Sponsor Marketplace API ────────────────────────────────────────────────────

@app.get("/api/sponsors")
async def get_sponsors(niche: str = "", status: str = ""):
    items = _load_sponsors()
    if niche:  items = [i for i in items if i.get("niche") == niche]
    if status: items = [i for i in items if i.get("status") == status]
    return JSONResponse(items)


@app.post("/api/sponsors")
async def create_sponsor(request: Request):
    from datetime import datetime as _dt
    data = await request.json()
    sid  = str(uuid.uuid4())[:8]
    sponsor = {
        "id":            sid,
        "company":       data.get("company", "").strip(),
        "contact_name":  data.get("contact_name", "").strip(),
        "contact_email": data.get("contact_email", "").strip(),
        "website":       data.get("website", "").strip(),
        "affiliate_url": data.get("affiliate_url", "").strip(),
        "niche":         data.get("niche", "Other"),
        "rate":          float(data.get("rate", 0)),
        "rate_type":     data.get("rate_type", "flat"),   # flat | CPM | affiliate
        "commission":    float(data.get("commission", 0) or 0),  # numeric %
        "status":        data.get("status", "prospect"),  # prospect|active|paused|closed
        "notes":         data.get("notes", "").strip(),
        "episodes":      [],
        "total_earned":  0.0,
        "created_at":    _dt.now().isoformat(),
    }
    items = _load_sponsors()
    items.append(sponsor)
    _save_sponsors(items)
    return JSONResponse(sponsor, status_code=201)


@app.put("/api/sponsors/{sponsor_id}")
async def update_sponsor(sponsor_id: str, request: Request):
    data  = await request.json()
    items = _load_sponsors()
    match = next((i for i in items if i["id"] == sponsor_id), None)
    if not match:
        return JSONResponse({"error": "Not found"}, status_code=404)
    str_fields = ["company","contact_name","contact_email","website","affiliate_url",
                  "niche","rate_type","commission","status","notes"]
    for f in str_fields:
        if f in data:
            match[f] = data[f].strip() if isinstance(data[f], str) else data[f]
    if "rate" in data:
        match["rate"] = float(data["rate"])
    _save_sponsors(items)
    return JSONResponse(match)


@app.delete("/api/sponsors/{sponsor_id}")
async def delete_sponsor(sponsor_id: str):
    items = [i for i in _load_sponsors() if i["id"] != sponsor_id]
    _save_sponsors(items)
    return JSONResponse({"ok": True})


@app.post("/api/sponsors/{sponsor_id}/log_episode")
async def log_sponsor_episode(sponsor_id: str, request: Request):
    """Record an episode number against a sponsor and increment earnings."""
    data  = await request.json()
    ep    = data.get("episode_number")
    items = _load_sponsors()
    match = next((i for i in items if i["id"] == sponsor_id), None)
    if not match:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if ep and ep not in match["episodes"]:
        match["episodes"].append(ep)
    if match.get("rate_type") == "flat":
        match["total_earned"] = match.get("total_earned", 0) + match.get("rate", 0)
    _save_sponsors(items)
    return JSONResponse({"ok": True, "total_earned": match["total_earned"]})


@app.get("/api/sponsors/{sponsor_id}/outreach")
async def generate_outreach(sponsor_id: str):
    """Generate a personalized cold-pitch email — Foundation-powered."""
    import json as _json
    import anthropic as _anthropic
    from db.engine import async_session as _async_session
    from config import get_current_location_id as _get_loc, settings as _settings
    from services.foundation import (
        assert_foundation_ready as _assert_ready,
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandContextError,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType

    items   = _load_sponsors()
    sponsor = next((i for i in items if i["id"] == sponsor_id), None)
    if not sponsor:
        return JSONResponse({"error": "Not found"}, status_code=404)

    profile      = _get_active_profile() or {}
    podcast_name = profile.get("name") or "the podcast"
    apple_url    = profile.get("apple_podcasts_url", "")

    # ── Foundation gate ──────────────────────────────────────────────────────
    location_id = _get_loc()
    topic_str = f"{sponsor['company']} sponsorship pitch"
    async with _async_session() as session:
        try:
            await _assert_ready(session=session, location_id=location_id)
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc), "foundation_not_ready": True}, status_code=422)
        try:
            ctx = await _get_brand_ctx(
                session=session,
                location_id=location_id,
                task_type=_TaskType.sponsor_pitch,
                topic=topic_str,
                platform=None,
                audience=None,
            )
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    # ── Foundation voice preamble ────────────────────────────────────────────
    bp = ctx.brand_profile
    vp = ctx.voice_profile
    vocab = ctx.vocabulary
    tone_str = ", ".join(vp.tone if isinstance(vp.tone, list) else [vp.tone]) if vp.tone else "direct, genuine"
    cadence_str = vp.cadence or "natural, punchy"
    vocab_yes = "Use naturally: " + ", ".join(vocab.use if isinstance(vocab.use, list) else [vocab.use]) if vocab.use else ""
    vocab_no  = "Avoid entirely: " + ", ".join(vocab.avoid if isinstance(vocab.avoid, list) else [vocab.avoid]) if vocab.avoid else ""
    voice_examples = ""
    if ctx.voice_samples:
        examples = [f'  — "{s.text[:300]}"' for s in ctx.voice_samples[:3]]
        voice_examples = "Voice samples (match this style):\n" + "\n".join(examples)
    voice_preamble = (
        f"VOICE PROFILE:\n- Tone: {tone_str}\n- Cadence: {cadence_str}\n- POV: {vp.pov or 'first-person'}\n"
    )
    if vocab_yes:      voice_preamble += f"- {vocab_yes}\n"
    if vocab_no:       voice_preamble += f"- {vocab_no}\n"
    if voice_examples: voice_preamble += f"\n{voice_examples}\n"

    prompt = f"""Write a short, professional podcast sponsorship pitch email from a host to a potential sponsor.

Podcast: {podcast_name}
Target company: {sponsor['company']}
Their industry / niche: {sponsor['niche']}
Proposed rate: ${sponsor['rate']} per episode ({sponsor['rate_type']})
{'Affiliate commission available: ' + sponsor['commission'] if sponsor.get('commission') else ''}

Rules:
- Subject line on line 1, blank line, then body
- 4 short paragraphs: opener referencing their company, audience fit, what the deal includes, CTA
- Conversational and confident — not salesy or desperate
- Mention the rate naturally in the third paragraph
- CTA: invite them to a 15-min call or reply to discuss
- Sign off with the podcast name
- Do NOT use placeholder brackets like [Name] — write it as if sending to their team
"""
    system_prompt = voice_preamble + "\nYou write podcast sponsorship pitches in the host's authentic voice."

    try:
        client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            temperature=0.75,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (message.content[0].text if message.content else "").strip()

        # Audit log (non-blocking)
        try:
            import uuid as _uuid
            async with _async_session() as _audit:
                await _audit.execute(
                    __import__("sqlalchemy").text("""
                        INSERT INTO audit_log (id, location_id, action, payload, created_at)
                        VALUES (:id, :loc_id, 'sponsor_pitch', CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {"id": str(_uuid.uuid4()), "loc_id": location_id,
                     "payload": _json.dumps({"topic": topic_str, "model": "claude-sonnet-4-5",
                                             "sample_count": ctx.metadata.sample_count})},
                )
                await _audit.commit()
        except Exception:
            pass

        return JSONResponse({
            "outreach": text,
            "foundation": True,
            "_foundation_thin": ctx.metadata.sample_count < 15,
            "_sample_count": ctx.metadata.sample_count,
        })
    except Exception:
        # Fallback template (LLM call failure only — Foundation gate already passed)
        text = (
            f"Subject: Sponsorship Opportunity — {podcast_name} × {sponsor['company']}\n\n"
            f"Hi {sponsor['company']} team,\n\n"
            f"I host {podcast_name}, a podcast that dives deep into {sponsor['niche']} topics. "
            f"Our audience is made up of professionals actively looking for tools and services in this space — "
            f"exactly the kind of buyers {sponsor['company']} is after.\n\n"
            f"I'd love to feature {sponsor['company']} as a sponsor. "
            f"The package includes a dedicated mid-roll read, a highlighted link in the show notes, "
            f"and a mention in our social promotion for each episode. "
            f"Rate: ${sponsor['rate']:.0f} per episode ({sponsor['rate_type']})."
            + (f"\n\nWe also offer an affiliate structure at {sponsor['commission']} per conversion if that works better for your team." if sponsor.get("commission") else "")
            + f"\n\nWould you be open to a 15-minute call this week to see if we're a good fit?\n\nBest,\n{podcast_name}"
        )
        return JSONResponse({"outreach": text, "foundation": False})


# ── Google Drive ──────────────────────────────────────────────────────────────

@app.get("/api/drive/status")
async def drive_status():
    from pipeline.drive import is_configured
    return JSONResponse({"configured": is_configured()})


@app.post("/api/drive/create_folder")
async def drive_create_folder(request: Request):
    """
    Create a Drive folder for an episode and optionally upload local assets.
    payload: {episode_number, episode_title, job_id?, guest_id?}
    """
    from pipeline.drive import is_configured, create_episode_folder, upload_file_to_folder
    if not is_configured():
        return JSONResponse({"error": "Google Drive not configured. Add GOOGLE_SERVICE_ACCOUNT_JSON to .env"}, status_code=400)

    data           = await request.json()
    ep_num         = int(data.get("episode_number", 0))
    ep_title       = data.get("episode_title", "Episode")
    job_id         = data.get("job_id", "")
    guest_id       = data.get("guest_id", "")

    # Create the folder
    folder = create_episode_folder(ep_title, ep_num)
    if not folder["ok"]:
        return JSONResponse({"error": folder["error"]}, status_code=500)

    fid      = folder["folder_id"]
    uploaded = []

    # Upload available assets from the job
    if job_id:
        job = _get_job(job_id)
        if job:
            # MP3
            mp3 = job.get("mp3_path", "")
            if mp3 and Path(mp3).exists():
                r = upload_file_to_folder(fid, mp3, "audio/mpeg", f"EP{ep_num}-{ep_title}.mp3")
                if r["ok"]: uploaded.append("audio")
            # Transcript TXT
            txt = job.get("transcript_path", "")
            if not txt:
                txt = str(Path(mp3).with_suffix(".txt")) if mp3 else ""
            if txt and Path(txt).exists():
                r = upload_file_to_folder(fid, txt, "text/plain", f"EP{ep_num}-transcript.txt")
                if r["ok"]: uploaded.append("transcript")

    # Update guest record with folder URL
    if guest_id:
        guests = _load_guests()
        g = next((x for x in guests if x["id"] == guest_id), None)
        if g:
            g["assets_drive_url"] = folder["folder_url"]
            _save_guests(guests)

    return JSONResponse({
        "ok":         True,
        "folder_url": folder["folder_url"],
        "folder_id":  fid,
        "uploaded":   uploaded,
    })


# ── Guest CRM ─────────────────────────────────────────────────────────────────

@app.post("/api/guests/extract")
async def extract_guest_from_url(request: Request):
    """
    Extract guest info from a URL (LinkedIn, website, etc.) or pasted text.
    Accepts JSON body with either `url` or `text` field.

    Returns: {name, email, topic, bio, company, website,
              linkedin, twitter, instagram, youtube, tiktok, facebook,
              booking_url}
    """
    import re
    data        = await request.json()
    url         = (data.get("url") or "").strip()
    pasted_text = (data.get("text") or "").strip()

    if not url and not pasted_text:
        return JSONResponse({"error": "url or text is required"}, status_code=400)

    if pasted_text:
        # Use the pasted text directly — no fetch needed
        page_text    = pasted_text[:6000]
        source_label = "Pasted text"
    else:
        # Fetch the page and strip HTML
        try:
            import httpx
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            }
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                resp = await client.get(url, headers=headers)
                html = resp.text
        except Exception as e:
            return JSONResponse({"error": f"Could not fetch URL: {e}"}, status_code=400)

        page_text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL)
        page_text = re.sub(r"<script[^>]*>.*?</script>", " ", page_text, flags=re.DOTALL)
        page_text = re.sub(r"<[^>]+>", " ", page_text)
        page_text = re.sub(r"\s{2,}", " ", page_text).strip()
        page_text = page_text[:6000]
        source_label = f"Page URL: {url}"

    # GPT-4o extraction
    try:
        from pipeline.content import _get_client
        client = _get_client()
        if not client:
            return JSONResponse({"error": "OpenAI API key not configured"}, status_code=500)

        prompt = f"""You are extracting guest contact and social info for a podcast booking system.

Source: {source_label}
Content:
{page_text}

Extract the following fields. Return ONLY a JSON object — no markdown, no explanation.
If a field is not found, use an empty string "".

Fields:
- name: Full name of the person
- email: Email address (if visible)
- company: Company, organization, or title
- bio: 1-2 sentence professional bio or key credential
- topic: Best episode topic for a podcast interview (infer from their expertise)
- website: Personal or company website URL
- linkedin: LinkedIn profile URL
- twitter: Twitter/X profile URL (look for twitter.com or x.com links)
- instagram: Instagram profile URL
- youtube: YouTube channel URL
- tiktok: TikTok profile URL
- facebook: Facebook profile URL
- booking_url: Booking link (Calendly, Cal.com, or similar scheduling link)

Return JSON only."""

        resp = client.chat.completions.create(
            model    = "gpt-4o",
            messages = [{"role": "user", "content": prompt}],
            max_tokens  = 600,
            temperature = 0.1,
        )
        raw = resp.choices[0].message.content.strip()

        # Parse JSON — strip any accidental markdown fences
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("` \n")
        extracted = json.loads(raw)
        return JSONResponse(extracted)

    except json.JSONDecodeError:
        return JSONResponse({"error": "Could not parse GPT response as JSON"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/guests")
async def get_guests(status: str = ""):
    items = _load_guests()
    if status:
        items = [g for g in items if g.get("status") == status]
    return JSONResponse(items)


@app.post("/api/guests")
async def create_guest(request: Request):
    from datetime import datetime as _dt
    data = await request.json()
    gid  = str(uuid.uuid4())[:8]
    guest = {
        "id":            gid,
        "name":          data.get("name", "").strip(),
        "email":         data.get("email", "").strip(),
        "topic":         data.get("topic", "").strip(),
        "bio":           data.get("bio", "").strip(),
        "booking_url":   data.get("booking_url", "").strip(),
        "instagram":     data.get("instagram", "").strip(),
        "linkedin":      data.get("linkedin", "").strip(),
        "youtube":       data.get("youtube", "").strip(),
        "website":       data.get("website", "").strip(),
        "status":        data.get("status", "prospect"),  # prospect|booked|recorded|aired
        "episode_number": data.get("episode_number", None),
        "episode_title":  data.get("episode_title", "").strip(),
        "episode_url_spotify": data.get("episode_url_spotify", "").strip(),
        "episode_url_apple":   data.get("episode_url_apple", "").strip(),
        "episode_url_youtube": data.get("episode_url_youtube", "").strip(),
        "assets_drive_url":    data.get("assets_drive_url", "").strip(),
        "notes":         data.get("notes", "").strip(),
        "created_at":    _dt.now().isoformat(),
    }
    items = _load_guests()
    items.append(guest)
    _save_guests(items)
    return JSONResponse(guest, status_code=201)


@app.put("/api/guests/{guest_id}")
async def update_guest(guest_id: str, request: Request):
    data  = await request.json()
    items = _load_guests()
    match = next((g for g in items if g["id"] == guest_id), None)
    if not match:
        return JSONResponse({"error": "Not found"}, status_code=404)
    str_fields = ["name","email","topic","bio","booking_url","instagram","linkedin",
                  "youtube","website","status","episode_title","episode_url_spotify",
                  "episode_url_apple","episode_url_youtube","assets_drive_url","notes"]
    for f in str_fields:
        if f in data:
            match[f] = data[f].strip() if isinstance(data[f], str) else data[f]
    for f in ["episode_number"]:
        if f in data:
            match[f] = data[f]
    _save_guests(items)
    return JSONResponse(match)


@app.delete("/api/guests/{guest_id}")
async def delete_guest(guest_id: str):
    items = [g for g in _load_guests() if g["id"] != guest_id]
    _save_guests(items)
    return JSONResponse({"ok": True})


@app.get("/api/guests/{guest_id}/asset_email")
async def generate_asset_email(guest_id: str):
    """
    Generate the guest asset-delivery email using GPT-4o.
    Merges episode URLs, Drive link, social handles, and a CTA.
    """
    items  = _load_guests()
    guest  = next((g for g in items if g["id"] == guest_id), None)
    if not guest:
        return JSONResponse({"error": "Not found"}, status_code=404)

    store        = _load_profiles_store()
    active_id    = store.get("active_id")
    profile      = next((p for p in store.get("profiles", []) if p["id"] == active_id), {})
    podcast_name = profile.get("name", "Success Agent Podcast")
    apple_url    = profile.get("apple_podcasts_url", "")

    spotify_url  = guest.get("episode_url_spotify", "")
    youtube_url  = guest.get("episode_url_youtube", "")
    apple_ep_url = guest.get("episode_url_apple", apple_url)
    drive_url    = guest.get("assets_drive_url", "")
    ep_num       = guest.get("episode_number", "")
    ep_title     = guest.get("episode_title", "")
    instagram    = guest.get("instagram", "")

    prompt = f"""Podcast: {podcast_name}
Guest name: {guest['name']}
Episode: {"EP. " + str(ep_num) + " — " + ep_title if ep_num else ep_title or "recent episode"}
{"Spotify: " + spotify_url if spotify_url else ""}
{"YouTube: " + youtube_url if youtube_url else ""}
{"Apple Podcasts: " + apple_ep_url if apple_ep_url else ""}
{"Google Drive assets folder: " + drive_url if drive_url else ""}
{"Host Instagram handle: @" + instagram.lstrip("@") if instagram else ""}

Write a warm, friendly email from the host to the guest with:
1. Subject line (first line)
2. Blank line
3. Opening: thank them, mention the episode aired
4. Share the episode links as a bullet list (only include platforms where URL provided)
{"5. Share the promotional assets Drive link" if drive_url else ""}
6. Ask them to share on social — include the host's Instagram handle if provided
7. Close with an offer to collaborate again
8. Sign with the podcast name

Keep it short and warm — 4-6 paragraphs max. Do NOT use placeholder brackets [like this] — write it ready to send."""

    # ── Foundation gate (after 404 check above) ─────────────────────────────
    import json as _json
    import re as _re
    import anthropic as _anthropic
    from db.engine import async_session as _async_session
    from config import get_current_location_id as _get_loc, settings as _settings
    from services.foundation import (
        assert_foundation_ready as _assert_ready,
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandContextError,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType

    location_id = _get_loc()
    async with _async_session() as session:
        try:
            await _assert_ready(session=session, location_id=location_id)
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc), "foundation_not_ready": True}, status_code=422)
        try:
            ctx = await _get_brand_ctx(
                session=session,
                location_id=location_id,
                task_type=_TaskType.guest_asset_email,
                topic=f"guest asset email for {guest['name']}",
                platform=None,
                audience=None,
            )
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    bp = ctx.brand_profile
    vp = ctx.voice_profile
    vocab = ctx.vocabulary
    tone_str = ", ".join(vp.tone if isinstance(vp.tone, list) else [vp.tone]) if vp.tone else "direct, genuine"
    cadence_str = vp.cadence or "natural, punchy"
    vocab_yes = "Use naturally: " + ", ".join(vocab.use if isinstance(vocab.use, list) else [vocab.use]) if vocab.use else ""
    vocab_no  = "Avoid entirely: " + ", ".join(vocab.avoid if isinstance(vocab.avoid, list) else [vocab.avoid]) if vocab.avoid else ""
    voice_examples = ""
    if ctx.voice_samples:
        examples = [f'  — "{s.text[:300]}"' for s in ctx.voice_samples[:3]]
        voice_examples = "Voice samples (match this style):\n" + "\n".join(examples)
    voice_preamble = f"VOICE PROFILE:\n- Tone: {tone_str}\n- Cadence: {cadence_str}\n- POV: {vp.pov or 'first-person'}\n"
    if vocab_yes: voice_preamble += f"- {vocab_yes}\n"
    if vocab_no:  voice_preamble += f"- {vocab_no}\n"
    if voice_examples: voice_preamble += f"\n{voice_examples}\n"

    system_prompt = voice_preamble + "\nYou are a podcast host writing warm, professional follow-up emails to podcast guests after their episodes air. Write in first person in the host's authentic voice."

    try:
        client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            temperature=0.75,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (message.content[0].text if message.content else "").strip()

        # Audit log (non-blocking)
        try:
            import uuid as _uuid
            async with _async_session() as _audit:
                await _audit.execute(
                    __import__("sqlalchemy").text("""
                        INSERT INTO audit_log (id, location_id, action, payload, created_at)
                        VALUES (:id, :loc_id, 'guest_asset_email', CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": str(_uuid.uuid4()),
                        "loc_id": location_id,
                        "payload": _json.dumps({
                            "topic": f"guest asset email for {guest['name']}",
                            "model": "claude-sonnet-4-5",
                            "sample_count": ctx.metadata.sample_count,
                        }),
                    },
                )
                await _audit.commit()
        except Exception:
            pass

        return JSONResponse({
            "email": text,
            "foundation": True,
            "guest_email": guest.get("email", ""),
            "_foundation_thin": ctx.metadata.sample_count < 15,
            "_sample_count": ctx.metadata.sample_count,
        })
    except Exception:
        # Fallback template
        lines = [
            f"Subject: Your Episode Is Live — {podcast_name}!",
            "",
            f"Hey {guest['name'].split()[0]},",
            "",
            f"Your episode just dropped on {podcast_name} and we couldn't be more excited to share it with the world!",
            "",
            "Here are your episode links:",
        ]
        if spotify_url:  lines.append(f"• Spotify: {spotify_url}")
        if youtube_url:  lines.append(f"• YouTube: {youtube_url}")
        if apple_ep_url: lines.append(f"• Apple Podcasts: {apple_ep_url}")
        if drive_url:
            lines += ["", f"All your promotional assets (audio, video, poster, clips) are here:", drive_url]
        lines += [
            "",
            "If you haven't already, we'd love if you shared the episode with your audience!",
            f"Tag us {('@' + instagram.lstrip('@')) if instagram else 'on Instagram'} and we'll reshare you.",
            "",
            f"Thanks again for being on the show — let's do it again sometime!",
            "",
            f"— {podcast_name}",
        ]
        return JSONResponse({"email": "\n".join(lines), "gpt": False, "guest_email": guest.get("email", "")})


@app.websocket("/ws/clip/{job_id}")
async def clip_websocket_endpoint(websocket: WebSocket, job_id: str):
    await websocket.accept()
    if job_id not in clip_ws_queues:
        clip_ws_queues[job_id] = asyncio.Queue()
    job = clip_jobs.get(job_id)
    if job:
        await websocket.send_json({
            "type": "state",
            "job":  {k: v for k, v in job.items() if k != "words"},
        })
    try:
        while True:
            try:
                msg = await asyncio.wait_for(clip_ws_queues[job_id].get(), timeout=30)
                await websocket.send_json(msg)
                if msg.get("type") == "result":
                    break
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    await websocket.accept()
    if job_id not in job_ws_queues:
        job_ws_queues[job_id] = asyncio.Queue()

    job = _get_job(job_id)
    if job:
        await websocket.send_json({
            "type": "state",
            "job":  {k: v for k, v in job.items() if k != "words"},
        })

    try:
        while True:
            try:
                msg = await asyncio.wait_for(job_ws_queues[job_id].get(), timeout=30)
                await websocket.send_json(msg)
                if msg.get("type") == "result" and msg.get("job", {}).get("status") == "error":
                    break
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# CLIP STUDIO
# ══════════════════════════════════════════════════════════════════════════════

CLIPS_FILE = DATA_DIR / "clips.json"
clip_jobs: dict[str, dict] = {}
clip_ws_queues: dict[str, asyncio.Queue] = {}


def _load_clips() -> list:
    if CLIPS_FILE.exists():
        return json.loads(CLIPS_FILE.read_text())
    return []

def _save_clips(clips: list):
    CLIPS_FILE.write_text(json.dumps(clips, indent=2))


async def _send_clip_progress(job_id: str, message: str, step: str = ""):
    clip_jobs[job_id]["log"].append(message)
    if step:
        clip_jobs[job_id]["step"] = step
    if job_id in clip_ws_queues:
        await clip_ws_queues[job_id].put({
            "type":    "progress",
            "message": message,
            "step":    step or clip_jobs[job_id].get("step", ""),
            "status":  clip_jobs[job_id]["status"],
        })


async def _send_clip_result(job_id: str):
    job = clip_jobs[job_id]
    if job_id in clip_ws_queues:
        await clip_ws_queues[job_id].put({
            "type": "result",
            "job":  {k: v for k, v in job.items() if k not in ("words",)},
        })


async def run_clip_job(job_id: str, video_path: str, model_size: str, num_clips: int):
    loop   = asyncio.get_event_loop()
    job    = clip_jobs[job_id]
    clips_dir = str(DATA_DIR / "clips" / job_id)
    Path(clips_dir).mkdir(parents=True, exist_ok=True)

    def mk_cb(step=""):
        def cb(msg):
            asyncio.run_coroutine_threadsafe(
                _send_clip_progress(job_id, msg, step),
                loop,
            )
        return cb

    try:
        job["status"] = "transcribing"
        await _send_clip_progress(job_id, "Starting transcription…", "transcribing")

        from pipeline.clip import run_clip_pipeline
        result = await run_clip_pipeline(
            job_id=job_id,
            video_path=video_path,
            model_size=model_size,
            num_clips=num_clips,
            output_dir=clips_dir,
            progress_cb=mk_cb("processing"),
        )

        job["status"]     = "subtitling"
        job["words"]      = result["words"]
        job["transcript"] = result["transcript"]
        await _send_clip_progress(job_id, "Adding viral subtitles to clips…", "subtitling")

        from pipeline.subtitles import process_clip_with_subtitles
        final_clips = []
        for clip in result["clips"]:
            subtitled = await process_clip_with_subtitles(
                clip_dict=clip,
                all_words=result["words"],
                output_dir=clips_dir,
                progress_cb=mk_cb("subtitling"),
            )
            final_clips.append(subtitled)

        job["clips"]  = [
            {
                "index":         c["index"],
                "start":         c["start"],
                "end":           c["end"],
                "duration":      c["duration"],
                "score":         round(c["score"], 3),
                "transcript":    c["transcript"],
                "raw_path":      c.get("raw_path", ""),
                "final_path":    c.get("subtitled_path", c.get("raw_path", "")),
                "tiktok_status": "ready",
                "publish_id":    None,
            }
            for c in final_clips
        ]
        job["status"] = "ready"

        await _send_clip_progress(job_id, f"✓ {len(final_clips)} clips ready to post!", "done")

        from pipeline.telegram import send as tg_send
        await tg_send(
            f"✂️ *{len(final_clips)} clips ready!*\n"
            f"Video: `{Path(video_path).name}`\n"
            f"Open the Clip Studio to post them → http://localhost:8765"
        )

    except Exception as exc:
        job["status"] = "error"
        job["error"]  = str(exc)
        await _send_clip_progress(job_id, f"Error: {exc}", "error")
        from pipeline.telegram import send as tg_send
        await tg_send(f"⚠️ *Clip job failed*\n`{str(exc)[:200]}`")
    finally:
        await _send_clip_result(job_id)


@app.post("/api/clip")
async def start_clip_job(video: UploadFile = File(...),
                         model_size: str = Form("tiny"),
                         num_clips: int = Form(3)):
    job_id = str(uuid.uuid4())[:8]

    tmp = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=Path(video.filename or "video.mp4").suffix or ".mp4",
    )
    async with aiofiles.open(tmp.name, "wb") as f:
        await f.write(await video.read())

    clip_jobs[job_id] = {
        "job_id":     job_id,
        "filename":   video.filename,
        "status":     "queued",
        "step":       "queued",
        "log":        [],
        "clips":      [],
        "transcript": None,
        "words":      [],
        "error":      None,
    }
    clip_ws_queues[job_id] = asyncio.Queue()
    asyncio.ensure_future(run_clip_job(job_id, tmp.name, model_size, num_clips))
    return JSONResponse({"job_id": job_id, "filename": video.filename})


@app.get("/api/clip/{job_id}")
async def get_clip_job(job_id: str):
    job = clip_jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Clip job not found"}, status_code=404)
    return JSONResponse({k: v for k, v in job.items() if k != "words"})


@app.post("/api/clip/{job_id}/post")
async def post_clip_to_tiktok(job_id: str, payload: dict):
    """Post a specific clip to TikTok (now or scheduled)."""
    job = clip_jobs.get(job_id)
    if not job or job["status"] != "ready":
        return JSONResponse({"error": "Job not ready"}, status_code=400)

    clip_idx   = int(payload.get("clip_index", 1)) - 1
    clips      = job.get("clips", [])
    if clip_idx < 0 or clip_idx >= len(clips):
        return JSONResponse({"error": "Invalid clip index"}, status_code=400)

    clip         = clips[clip_idx]
    title        = payload.get("title", clip["transcript"][:100])
    schedule_ts  = payload.get("schedule_time")   # optional Unix timestamp

    from pipeline.tiktok import upload_clip, is_authorized
    if not is_authorized():
        return JSONResponse({"error": "TikTok not authorized", "auth_url": "/api/tiktok/auth"}, status_code=401)

    async def do_post():
        try:
            result = await upload_clip(
                video_path=clip["final_path"],
                title=title,
                schedule_time=schedule_ts,
            )
            clip["tiktok_status"] = "posted"
            clip["publish_id"]    = result["publish_id"]
            # Save to history
            saved = _load_clips()
            saved.append({**clip, "job_id": job_id, "filename": job["filename"]})
            _save_clips(saved)
        except Exception as exc:
            clip["tiktok_status"] = "error"
            clip["tiktok_error"]  = str(exc)

    asyncio.ensure_future(do_post())
    return JSONResponse({"status": "posting"})


@app.get("/api/clips")
async def list_clips():
    return JSONResponse(_load_clips())


@app.get("/api/clip/{job_id}/video/{clip_index}")
async def serve_clip_video(job_id: str, clip_index: int):
    """Stream a clip MP4 for in-browser preview."""
    from fastapi.responses import FileResponse as _FR
    job = clip_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Clip job not found")
    clips = job.get("clips", [])
    idx = clip_index - 1
    if idx < 0 or idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip index out of range")
    path = clips[idx].get("final_path", "")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Clip file not found on disk")
    return _FR(path=path, media_type="video/mp4")


# ── Transcription Database ────────────────────────────────────────────────────

TRANSCRIPTIONS_DIR  = DATA_DIR / "transcriptions"
TRANSCRIPTIONS_FILE = DATA_DIR / "transcriptions.json"
TRANSCRIPTIONS_DIR.mkdir(exist_ok=True)


def _load_transcriptions() -> list:
    if TRANSCRIPTIONS_FILE.exists():
        return json.loads(TRANSCRIPTIONS_FILE.read_text())
    return []

def _save_transcriptions(items: list):
    TRANSCRIPTIONS_FILE.write_text(json.dumps(items, indent=2))

def _save_transcription_text(tx_id: str, record: dict):
    """Persist full text + segments to individual JSON file in transcriptions/."""
    path = TRANSCRIPTIONS_DIR / f"{tx_id}.json"
    path.write_text(json.dumps(record, indent=2))

def _load_transcription_full(tx_id: str) -> dict:
    path = TRANSCRIPTIONS_DIR / f"{tx_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


@app.get("/api/transcriptions")
async def list_transcriptions(q: str = "", tag: str = "", limit: int = 100):
    """
    List all stored transcriptions.
    Optional ?q= for full-text search across title + transcript.
    Optional ?tag= to filter by tag.
    """
    items = _load_transcriptions()
    if tag:
        items = [t for t in items if tag.lower() in [x.lower() for x in t.get("tags", [])]]
    if q:
        ql = q.lower()
        results = []
        for t in items:
            if ql in t.get("title", "").lower():
                results.append({**t, "_match": "title"})
                continue
            # Search full text from individual file
            full = _load_transcription_full(t["id"])
            if ql in full.get("transcript", "").lower():
                # Find surrounding context snippet
                txt = full.get("transcript", "")
                idx = txt.lower().find(ql)
                snippet = txt[max(0, idx-80):idx+120].strip()
                results.append({**t, "_match": "transcript", "_snippet": f"…{snippet}…"})
        items = results
    return JSONResponse(items[:limit])


@app.get("/api/transcriptions/{tx_id}")
async def get_transcription(tx_id: str):
    """Get full transcription record including segments and full text."""
    full = _load_transcription_full(tx_id)
    if not full:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(full)


@app.delete("/api/transcriptions/{tx_id}")
async def delete_transcription(tx_id: str):
    items = _load_transcriptions()
    before = len(items)
    items  = [t for t in items if t["id"] != tx_id]
    if len(items) == before:
        return JSONResponse({"error": "Not found"}, status_code=404)
    _save_transcriptions(items)
    # Remove individual file
    path = TRANSCRIPTIONS_DIR / f"{tx_id}.json"
    if path.exists():
        path.unlink()
    return JSONResponse({"ok": True})


@app.patch("/api/transcriptions/{tx_id}")
async def update_transcription(tx_id: str, request: Request):
    """Update title, tags, or notes on a stored transcription."""
    data  = await request.json()
    items = _load_transcriptions()
    for t in items:
        if t["id"] == tx_id:
            if "title"  in data: t["title"]  = data["title"].strip()
            if "tags"   in data: t["tags"]   = data["tags"]
            if "notes"  in data: t["notes"]  = data["notes"].strip()
            if "source" in data: t["source"] = data["source"].strip()
            break
    else:
        return JSONResponse({"error": "Not found"}, status_code=404)
    _save_transcriptions(items)
    # Also update full record
    full = _load_transcription_full(tx_id)
    if full:
        full.update({k: data[k] for k in ("title","tags","notes","source") if k in data})
        _save_transcription_text(tx_id, full)
    return JSONResponse({"ok": True})


@app.get("/api/transcriptions/{tx_id}/export")
async def export_transcription(tx_id: str, fmt: str = "txt"):
    """
    Export a transcription.
    fmt: txt | srt | json | md
    """
    full = _load_transcription_full(tx_id)
    if not full:
        return JSONResponse({"error": "Not found"}, status_code=404)

    title    = full.get("title", tx_id)
    segments = full.get("segments", [])
    text     = full.get("transcript", "")

    if fmt == "json":
        content      = json.dumps(full, indent=2)
        media_type   = "application/json"
        filename     = f"{tx_id}.json"

    elif fmt == "srt":
        from pipeline.video import segments_to_srt
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False, mode="w") as f:
            srt_path = f.name
        segments_to_srt(segments, srt_path)
        content    = Path(srt_path).read_text()
        media_type = "text/plain"
        filename   = f"{title[:40]}.srt"

    elif fmt == "md":
        def ts(s):
            m,sc = divmod(int(s),60); h,m = divmod(m,60)
            return f"{h:02d}:{m:02d}:{sc:02d}"
        lines = [f"# {title}", "", f"**Source:** {full.get('source','')}",
                 f"**Language:** {full.get('language','')}", f"**Duration:** {full.get('duration_fmt','')}",
                 f"**Words:** {full.get('word_count',0):,}", "", "---", "", "## Transcript", ""]
        for seg in segments:
            lines.append(f"**[{ts(seg['start'])}]** {seg['text'].strip()}")
            lines.append("")
        content    = "\n".join(lines)
        media_type = "text/markdown"
        filename   = f"{title[:40]}.md"

    else:  # txt
        lines = [f"{title}", "=" * len(title), ""]
        for seg in segments:
            m,sc = divmod(int(seg['start']),60)
            lines.append(f"[{m:02d}:{sc:02d}] {seg['text'].strip()}")
        content    = "\n".join(lines)
        media_type = "text/plain"
        filename   = f"{title[:40]}.txt"

    from fastapi.responses import Response
    return Response(
        content     = content,
        media_type  = media_type,
        headers     = {"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/transcribe")
async def transcribe_file(
    file:       UploadFile = File(...),
    title:      str        = Form(""),
    tags:       str        = Form(""),         # comma-separated
    source:     str        = Form(""),         # e.g. "YouTube", "Book recording", "Meeting"
    model_size: str        = Form("base"),
    notes:      str        = Form(""),
):
    """
    Transcribe any audio or video file and store in the transcription database.
    Returns the stored transcription record immediately (job runs async via WS).
    """
    from pipeline.transcribe import transcribe
    from pipeline.video      import extract_audio
    from datetime import datetime as _dt2

    suffix  = Path(file.filename).suffix or ".mp3"
    tmp     = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    async with aiofiles.open(tmp.name, "wb") as f:
        await f.write(await file.read())

    tx_id    = str(uuid.uuid4())
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    tx_title = title.strip() or Path(file.filename).stem

    # Create placeholder record immediately
    record = {
        "id":           tx_id,
        "title":        tx_title,
        "source":       source.strip(),
        "filename":     file.filename,
        "tags":         tag_list,
        "notes":        notes.strip(),
        "status":       "processing",
        "language":     "",
        "duration":     0.0,
        "duration_fmt": "",
        "word_count":   0,
        "transcript":   "",
        "segments":     [],
        "created_at":   _dt2.now().isoformat(),
        "model_size":   model_size,
    }
    # Save to index
    items = _load_transcriptions()
    # Index record (no full transcript to keep index lean)
    index_rec = {k: record[k] for k in
                 ("id","title","source","filename","tags","notes","status",
                  "language","duration","duration_fmt","word_count","created_at")}
    items.append(index_rec)
    _save_transcriptions(items)
    _save_transcription_text(tx_id, record)

    # Kick off transcription in background
    async def do_transcribe():
        loop2 = asyncio.get_event_loop()
        try:
            VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
            input_path = tmp.name
            if Path(tmp.name).suffix.lower() in VIDEO_EXTS:
                wav_path = tmp.name.replace(suffix, ".wav")
                ok = await loop2.run_in_executor(None, lambda: extract_audio(tmp.name, wav_path))
                if ok:
                    input_path = wav_path

            result = await loop2.run_in_executor(None, lambda: transcribe(
                input_path, model_size=model_size, progress_cb=None
            ))

            # Compute duration from segments
            segs = result.get("segments", [])
            dur  = segs[-1]["end"] if segs else 0.0
            m, sc = divmod(int(dur), 60); h, m = divmod(m, 60)
            dur_fmt = f"{h:02d}:{m:02d}:{sc:02d}" if h else f"{m:02d}:{sc:02d}"

            # GPT summary (brief)
            summary = ""
            try:
                from pipeline.content import _get_client
                client = _get_client()
                if client:
                    resp = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{
                            "role": "user",
                            "content": f"Summarize this transcript in 2-3 sentences:\n\n{result['text'][:3000]}"
                        }],
                        max_tokens=200,
                    )
                    summary = resp.choices[0].message.content.strip()
            except Exception:
                pass

            # Update record
            record.update({
                "status":       "ready",
                "language":     result.get("language", ""),
                "duration":     dur,
                "duration_fmt": dur_fmt,
                "word_count":   len(result["words"]),
                "transcript":   result["text"],
                "segments":     segs,
                "summary":      summary,
            })
            _save_transcription_text(tx_id, record)

            # Update index
            items2 = _load_transcriptions()
            for t in items2:
                if t["id"] == tx_id:
                    t.update({
                        "status":       "ready",
                        "language":     record["language"],
                        "duration":     record["duration"],
                        "duration_fmt": record["duration_fmt"],
                        "word_count":   record["word_count"],
                        "summary":      summary,
                    })
            _save_transcriptions(items2)

        except Exception as e:
            record["status"] = "error"
            record["error"]  = str(e)
            _save_transcription_text(tx_id, record)
            items3 = _load_transcriptions()
            for t in items3:
                if t["id"] == tx_id:
                    t["status"] = "error"; t["error"] = str(e)
            _save_transcriptions(items3)
        finally:
            try: os.unlink(tmp.name)
            except: pass

    asyncio.ensure_future(do_transcribe())
    return JSONResponse(record, status_code=202)


# ── TikTok OAuth ──────────────────────────────────────────────────────────────

@app.get("/api/tiktok/auth")
async def tiktok_auth():
    from pipeline.tiktok import get_auth_url, is_authorized
    if is_authorized():
        return HTMLResponse("<h2>✅ TikTok already connected!</h2><p>Return to <a href='/'>Podcast OS</a></p>")
    if not os.getenv("TIKTOK_CLIENT_KEY"):
        return HTMLResponse(
            "<h2>TikTok Setup Required</h2>"
            "<p>Add <code>TIKTOK_CLIENT_KEY</code> and <code>TIKTOK_CLIENT_SECRET</code> to your <code>.env</code> file.</p>"
            "<p>Get them from <a href='https://developers.tiktok.com' target='_blank'>developers.tiktok.com</a></p>"
        )
    url, _ = get_auth_url()
    import subprocess
    subprocess.Popen(["open", url])
    return HTMLResponse(f"<h2>Opening TikTok authorization…</h2><p>If the browser didn't open, <a href='{url}'>click here</a></p>")


@app.get("/api/tiktok/callback")
async def tiktok_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<h2>Authorization denied</h2><p>{error}</p>")
    from pipeline.tiktok import exchange_code
    try:
        await exchange_code(code, state)
        return HTMLResponse("<h2>✅ TikTok connected!</h2><p>You can close this tab and return to <a href='/'>Podcast OS</a></p>")
    except Exception as exc:
        return HTMLResponse(f"<h2>Error</h2><p>{exc}</p>")


@app.get("/api/tiktok/status")
async def tiktok_status():
    from pipeline.tiktok import is_authorized
    return JSONResponse({"authorized": is_authorized()})


@app.post("/api/tiktok/disconnect")
async def tiktok_disconnect():
    """Clear stored TikTok tokens from .env."""
    from dotenv import set_key as _set_key
    env_path = Path(".env")
    for key in ("TIKTOK_ACCESS_TOKEN", "TIKTOK_REFRESH_TOKEN", "TIKTOK_OPEN_ID"):
        _set_key(str(env_path), key, "")
    # Reload env so is_authorized() reflects the change immediately
    from dotenv import load_dotenv
    load_dotenv(override=True)
    return JSONResponse({"ok": True, "message": "TikTok disconnected"})


# ── Screen Recorder Widget JS ────────────────────────────────────────────────

@app.get("/recorder-widget.js")
async def serve_recorder_widget():
    """Floating Loom-style screen recorder widget included on every PodClick page."""
    from fastapi.responses import FileResponse as _FR
    return _FR(str(FRONTEND_DIR / "recorder-widget.js"), media_type="application/javascript")


# ── Screen Recording Conversion ──────────────────────────────────────────────

_VIDEO_LIBRARY_DIR = DATA_DIR / "video_library"
_VIDEO_LIBRARY_DIR.mkdir(exist_ok=True)
_VIDEO_LIBRARY_INDEX = DATA_DIR / "video_library.json"


def _load_video_library() -> list:
    if _VIDEO_LIBRARY_INDEX.exists():
        try:
            return json.loads(_VIDEO_LIBRARY_INDEX.read_text())
        except Exception:
            pass
    return []


def _save_video_library(items: list) -> None:
    _VIDEO_LIBRARY_INDEX.write_text(json.dumps(items, indent=2))


@app.post("/api/studio/save-direct-video")
async def save_direct_video(
    video: UploadFile = File(...),
    title:     str = Form(default=""),
    video_type: str = Form(default="other"),
):
    """
    Convert a browser WebM recording to MP4 and save to the video library.
    Used for ads, VSLs, intros, promos — NOT the podcast pipeline.

    video_type options: ad | vsl | intro | promo | b-roll | tutorial | other
    Returns: { id, title, video_type, url, size_mb, created_at }
    """
    import tempfile, subprocess as _sp, uuid as _uuid
    from datetime import datetime, timezone

    vid_id = str(_uuid.uuid4())
    raw = await video.read()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        src = tmp / "input.webm"
        src.write_bytes(raw)

        out = _VIDEO_LIBRARY_DIR / f"{vid_id}.mp4"

        result = _sp.run([
            "ffmpeg", "-y", "-i", str(src),
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(out),
        ], capture_output=True)

        if result.returncode != 0:
            err = result.stderr.decode(errors="replace")[-300:]
            raise HTTPException(status_code=500, detail=f"ffmpeg failed: {err}")

    safe_type = video_type.strip().lower() or "other"
    auto_name = f"{safe_type.title()} — {datetime.now(timezone.utc).strftime('%b %-d %Y')}"
    final_title = title.strip() or auto_name
    size_mb = round(out.stat().st_size / 1024 / 1024, 1)

    entry = {
        "id":         vid_id,
        "title":      final_title,
        "video_type": safe_type,
        "filename":   out.name,
        "size_mb":    size_mb,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    lib = _load_video_library()
    lib.insert(0, entry)
    _save_video_library(lib)

    return JSONResponse({**entry, "url": f"/api/studio/video-library/{vid_id}"})


@app.get("/api/studio/video-library")
async def list_video_library():
    """List all saved direct videos (ads, VSLs, etc.)."""
    items = _load_video_library()
    # Add URL + existence check
    out = []
    for item in items:
        path = _VIDEO_LIBRARY_DIR / item.get("filename", f"{item['id']}.mp4")
        if path.exists():
            out.append({**item, "url": f"/api/studio/video-library/{item['id']}"})
    return JSONResponse(out)


@app.get("/api/studio/video-library/{vid_id}")
async def serve_video_library_item(vid_id: str):
    """Stream a saved direct video."""
    from fastapi.responses import FileResponse as _FR
    # Basic safety — only alphanumeric + hyphens
    if not all(c.isalnum() or c == '-' for c in vid_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    path = _VIDEO_LIBRARY_DIR / f"{vid_id}.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return _FR(path=str(path), media_type="video/mp4", filename=f"{vid_id}.mp4")


@app.delete("/api/studio/video-library/{vid_id}")
async def delete_video_library_item(vid_id: str):
    """Delete a video from the library."""
    if not all(c.isalnum() or c == '-' for c in vid_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    path = _VIDEO_LIBRARY_DIR / f"{vid_id}.mp4"
    if path.exists():
        path.unlink()
    lib = [i for i in _load_video_library() if i.get("id") != vid_id]
    _save_video_library(lib)
    return JSONResponse({"ok": True})


@app.post("/api/studio/stitch-segments")
async def stitch_segments(
    segments: list[UploadFile] = File(...),
    usable_end: list[str] = Form(...),
):
    """
    Stitch punch-in recording segments into one MP4.

    Each segment has a corresponding usable_end seconds value:
      -1  = keep the full segment (last segment, no trim)
      N   = trim segment to N seconds from start

    Returns the stitched MP4 as a download.
    """
    import tempfile, subprocess as _sp
    from fastapi.responses import FileResponse as _FR

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        trimmed_paths = []

        for i, (upload, end_str) in enumerate(zip(segments, usable_end)):
            raw = await upload.read()
            ext = Path(upload.filename or "seg.webm").suffix or ".webm"
            raw_path = tmp / f"raw_{i}{ext}"
            raw_path.write_bytes(raw)

            out_path = tmp / f"seg_{i}.mp4"
            end_sec  = float(end_str)

            if end_sec < 0:
                # Keep full segment
                cmd = [
                    "ffmpeg", "-y", "-i", str(raw_path),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-c:a", "aac", "-b:a", "128k",
                    str(out_path),
                ]
            else:
                cmd = [
                    "ffmpeg", "-y", "-i", str(raw_path),
                    "-t", str(end_sec),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-c:a", "aac", "-b:a", "128k",
                    str(out_path),
                ]
            r = _sp.run(cmd, capture_output=True)
            if r.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Segment {i} encode failed")
            trimmed_paths.append(out_path)

        if len(trimmed_paths) == 1:
            final = trimmed_paths[0]
        else:
            # Concat with FFmpeg
            concat_list = tmp / "concat.txt"
            concat_list.write_text(
                "\n".join(f"file '{p}'" for p in trimmed_paths)
            )
            final = tmp / "stitched.mp4"
            r = _sp.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                str(final),
            ], capture_output=True)
            if r.returncode != 0:
                raise HTTPException(status_code=500, detail="Concat failed: " + r.stderr.decode()[-300:])

        # Return stitched file — keep tmpdir alive via FileResponse background task
        return _FR(
            path=str(final),
            media_type="video/mp4",
            filename="recording_stitched.mp4",
            background=None,
        )


@app.post("/api/vsl/parse")
async def vsl_parse_script(request: Request):
    """
    Parse a VSL script and return the edit plan (sections, CTA, sign-off).
    Body: { "script": "..." }
    """
    body = await request.json()
    script = body.get("script", "")
    if not script.strip():
        raise HTTPException(status_code=400, detail="script is required")
    from pipeline.vsl_editor import parse_vsl_script
    plan = parse_vsl_script(script)
    # Strip captions from parse preview (large)
    preview = {**plan, "captions": len(plan.get("captions", []))}
    return JSONResponse(preview)


_VSL_JOBS: dict = {}

@app.post("/api/vsl/render")
async def vsl_render(
    video: UploadFile = File(...),
    script: str = Form(...),
    style:  str = Form(default="bold"),
):
    """
    Render a VSL video with auto-generated graphic overlays.

    Multipart: video (MP4/WebM/MOV), script (text), style (bold)
    Response: { job_id, status: 'running' }  → poll GET /api/vsl/render/{job_id}
    """
    import uuid as _uuid, asyncio as _asyncio, time as _time

    job_id = str(_uuid.uuid4())
    _VSL_JOBS[job_id] = {"status": "running", "progress": "Saving upload…", "output": None, "error": None}

    # Save upload
    vsl_dir = DATA_DIR / "vsl_renders"
    vsl_dir.mkdir(exist_ok=True)
    ext = Path(video.filename or "video.mp4").suffix or ".mp4"
    src_path = vsl_dir / f"{job_id}_src{ext}"
    src_path.write_bytes(await video.read())

    async def _render_bg():
        try:
            from pipeline.vsl_editor import parse_vsl_script, render_vsl, VSL_STYLE_BOLD
            loop = _asyncio.get_event_loop()
            _VSL_JOBS[job_id]["progress"] = "Parsing script…"
            plan = await loop.run_in_executor(None, parse_vsl_script, script)
            _VSL_JOBS[job_id]["progress"] = "Rendering video with overlays…"
            out_path = str(vsl_dir / f"{job_id}_vsl.mp4")
            await loop.run_in_executor(
                None, render_vsl, str(src_path), plan, out_path, VSL_STYLE_BOLD
            )
            _VSL_JOBS[job_id]["status"]   = "done"
            _VSL_JOBS[job_id]["progress"] = "Complete"
            _VSL_JOBS[job_id]["output"]   = f"/api/vsl/download/{job_id}"
        except Exception as exc:
            _VSL_JOBS[job_id]["status"] = "error"
            _VSL_JOBS[job_id]["error"]  = str(exc)

    asyncio.create_task(_render_bg())
    return JSONResponse({"job_id": job_id, "status": "running"})


@app.get("/api/vsl/render/{job_id}")
async def vsl_render_status(job_id: str):
    job = _VSL_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(job)


@app.get("/api/vsl/download/{job_id}")
async def vsl_download(job_id: str):
    from fastapi.responses import FileResponse as _FR
    if not all(c.isalnum() or c == '-' for c in job_id):
        raise HTTPException(status_code=400, detail="Invalid job id")
    path = DATA_DIR / "vsl_renders" / f"{job_id}_vsl.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Output not ready")
    return _FR(path=str(path), media_type="video/mp4", filename="vsl_edited.mp4")


@app.post("/api/screen-record/convert")
async def screen_record_convert(file: UploadFile = File(...)):
    """Convert a WebM screen recording to MP4 using ffmpeg."""
    import tempfile, subprocess as _sp
    from fastapi.responses import FileResponse as _FileResponse

    tmp_dir = Path(tempfile.mkdtemp())
    webm_path = tmp_dir / "recording.webm"
    mp4_path  = tmp_dir / "recording.mp4"

    content = await file.read()
    webm_path.write_bytes(content)

    result = _sp.run(
        [
            "ffmpeg", "-y", "-i", str(webm_path),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(mp4_path),
        ],
        capture_output=True,
    )

    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[-300:]
        raise HTTPException(status_code=500, detail=f"ffmpeg conversion failed: {detail}")

    return _FileResponse(
        path=str(mp4_path),
        media_type="video/mp4",
        filename="screen-recording.mp4",
    )


# ── YouTube OAuth & Upload ────────────────────────────────────────────────────

@app.get("/api/youtube/status")
async def youtube_status():
    from pipeline.youtube import is_configured, is_authorized, get_channel_info
    return JSONResponse({
        "configured": is_configured(),
        "authorized": is_authorized(),
        "channel":    get_channel_info() if is_authorized() else {},
    })


@app.get("/api/youtube/auth")
async def youtube_auth():
    from pipeline.youtube import is_configured, is_authorized, get_auth_url, get_channel_info
    if is_authorized():
        ch = get_channel_info()
        title = ch.get("title", "Your channel")
        return HTMLResponse(
            f"<h2>✅ YouTube already connected!</h2>"
            f"<p>Channel: <strong>{title}</strong></p>"
            f"<p>Return to <a href='/'>Podcast OS</a></p>"
        )
    if not is_configured():
        return HTMLResponse(
            "<h2>YouTube Setup Required</h2>"
            "<p>Place your OAuth 2.0 client secrets file at "
            "<code>data/youtube_client_secrets.json</code> "
            "or set <code>YOUTUBE_CLIENT_SECRETS_JSON</code> in your <code>.env</code>.</p>"
            "<ol>"
            "<li>Go to <a href='https://console.cloud.google.com/apis/credentials' target='_blank'>Google Cloud Console → Credentials</a></li>"
            "<li>Create an OAuth 2.0 Client ID (Web Application)</li>"
            "<li>Add <code>http://localhost:8765/api/youtube/callback</code> as a redirect URI</li>"
            "<li>Download JSON → rename to <code>youtube_client_secrets.json</code> → place in the <code>data/</code> folder</li>"
            "<li>Return here and click Connect YouTube again</li>"
            "</ol>"
            "<p><a href='/'>← Back to Podcast OS</a></p>"
        )
    try:
        url = get_auth_url()
    except Exception as e:
        return HTMLResponse(f"<h2>Error</h2><p>{e}</p>")
    import subprocess
    subprocess.Popen(["open", url])
    return HTMLResponse(
        f"<h2>Opening YouTube authorization…</h2>"
        f"<p>A browser window should open for you to grant access.</p>"
        f"<p>If it didn't open, <a href='{url}'>click here</a>.</p>"
        f"<p><a href='/'>← Back to Podcast OS</a></p>"
    )


@app.get("/api/youtube/callback")
async def youtube_callback(code: str = "", error: str = "", state: str = ""):
    if error:
        return HTMLResponse(
            f"<h2>Authorization denied</h2><p>{error}</p>"
            f"<p><a href='/'>← Back to Podcast OS</a></p>"
        )
    from pipeline.youtube import exchange_code
    result = exchange_code(code)
    if result.get("ok"):
        channels = result.get("channels", [result.get("channel", {})])
        active   = result.get("channel", {})
        # Build channel picker HTML
        channel_rows = ""
        for ch in channels:
            is_active = ch.get("id") == active.get("id")
            thumb = ch.get("thumbnail", "")
            img = f'<img src="{thumb}" style="width:36px;height:36px;border-radius:50%;object-fit:cover;vertical-align:middle;margin-right:10px;">' if thumb else "📺 "
            checked = "checked" if is_active else ""
            channel_rows += (
                f'<label style="display:flex;align-items:center;gap:10px;padding:12px;'
                f'border:1px solid {"#d95f1e" if is_active else "#333"};border-radius:8px;'
                f'margin-bottom:8px;cursor:pointer;background:{"rgba(217,95,30,0.08)" if is_active else "#111"};">'
                f'<input type="radio" name="ch" value="{ch["id"]}" {checked} style="accent-color:#d95f1e;">'
                f'{img}'
                f'<div><strong style="color:#F5F7FA;">{ch["title"]}</strong>'
                f'<div style="font-size:12px;color:#847d74;">{int(ch.get("subscribers","0") or 0):,} subscribers · {ch.get("videos","0")} videos</div></div>'
                f'</label>'
            )
        return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>YouTube Connected · PodClick</title>
<style>body{{background:#0b0a08;color:#F5F7FA;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}}
.card{{background:#161410;border:1px solid #272420;border-radius:12px;padding:32px;max-width:480px;width:100%;}}
h2{{color:#52c47a;margin-bottom:6px;}} .sub{{color:#847d74;font-size:13px;margin-bottom:24px;}}
.save-btn{{width:100%;padding:12px;background:linear-gradient(135deg,#d95f1e,#f07030);color:#fff;border:none;border-radius:9px;font-size:14px;font-weight:700;cursor:pointer;margin-top:16px;}}
.save-btn:hover{{opacity:.9;}} a{{color:#f07030;}}</style></head>
<body><div class="card">
  <h2>✅ YouTube Connected!</h2>
  <p class="sub">Pick which channel PodClick uploads to by default. You can switch any time.</p>
  <form id="f">{channel_rows}
    <button class="save-btn" type="submit">Set Active Channel →</button>
  </form>
  <p style="margin-top:16px;font-size:12px;color:#4a4540;">Or <a href="/">skip — use first channel</a></p>
</div>
<script>
document.getElementById('f').addEventListener('submit', async e => {{
  e.preventDefault();
  const id = document.querySelector('input[name=ch]:checked').value;
  await fetch('/api/youtube/channels/select', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{channel_id:id}})}});
  window.location.href = '/projects';
}});
</script></body></html>""")
    else:
        return HTMLResponse(
            f"<h2>Error connecting YouTube</h2>"
            f"<p>{result.get('error', 'Unknown error')}</p>"
            f"<p><a href='/api/youtube/auth'>Try again</a></p>"
        )


@app.post("/api/youtube/disconnect")
async def youtube_disconnect():
    from pipeline.youtube import revoke_token
    revoke_token()
    return JSONResponse({"ok": True})


@app.get("/api/youtube/channels")
async def youtube_list_channels():
    """Return all YouTube channels available on the connected account."""
    from pipeline.youtube import is_authorized, list_channels, get_active_channel_id
    if not is_authorized():
        return JSONResponse({"error": "YouTube not connected"}, status_code=401)
    return JSONResponse({
        "channels": list_channels(),
        "active_channel_id": get_active_channel_id(),
    })


@app.post("/api/youtube/channels/select")
async def youtube_select_channel(request: Request):
    """Set the active upload channel by ID."""
    from pipeline.youtube import list_channels, _save_active_channel
    body = await request.json()
    channel_id = body.get("channel_id", "").strip()
    if not channel_id:
        return JSONResponse({"error": "channel_id required"}, status_code=400)
    channels = list_channels()
    match = next((c for c in channels if c["id"] == channel_id), None)
    if not match:
        return JSONResponse({"error": "Channel not found in connected account"}, status_code=404)
    _save_active_channel(channel_id)
    # Update legacy channel cache
    from pathlib import Path as _P
    (_P(__file__).parent / "data" / "youtube_channel.json").write_text(
        __import__("json").dumps(match, indent=2)
    )
    return JSONResponse({"ok": True, "active": match})


@app.post("/api/studio/publish/telegram")
async def studio_publish_telegram(request: Request):
    """
    Receive a video blob from Studio and send it directly to Telegram.
    Body: multipart/form-data with fields:
      video   — WebM video blob (required)
      caption — message caption (optional)
    """
    from pipeline.telegram import is_configured, _token, _chat_id
    import httpx as _httpx

    if not is_configured():
        return JSONResponse(
            {"error": "Telegram not configured. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env"},
            status_code=400,
        )

    form = await request.form()
    video_field = form.get("video")
    if not video_field:
        return JSONResponse({"error": "No video field in form"}, status_code=400)

    video_bytes = await video_field.read()
    caption = form.get("caption") or "🎙️ New recording from PodClick Studio"

    try:
        async with _httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{_token()}/sendVideo",
                data={
                    "chat_id":             _chat_id(),
                    "caption":             caption,
                    "supports_streaming":  "true",
                },
                files={"video": ("studio-recording.webm", video_bytes, "video/webm")},
            )
        if resp.status_code == 200:
            return JSONResponse({"ok": True})
        detail = resp.json().get("description", resp.text[:200])
        return JSONResponse({"error": detail}, status_code=502)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/studio/publish/youtube")
async def studio_publish_youtube(request: Request):
    """
    Receive a video blob from Studio and upload it directly to YouTube.
    Body: multipart/form-data with fields:
      video       — WebM video blob (required)
      title       — video title (required)
      description — video description (optional)
      privacy     — "private"|"unlisted"|"public" (optional, default "private")
    """
    import tempfile
    import os as _os
    from pipeline.youtube import is_authorized, upload_video

    if not is_authorized():
        return JSONResponse(
            {"error": "YouTube not connected. Go to Settings → Connect YouTube."},
            status_code=400,
        )

    form = await request.form()
    video_field = form.get("video")
    if not video_field:
        return JSONResponse({"error": "No video field in form"}, status_code=400)

    title = (form.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)

    desc    = form.get("description") or ""
    privacy = form.get("privacy") or "private"

    video_bytes = await video_field.read()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name

        result = upload_video(
            video_path=tmp_path,
            title=title,
            description=desc,
            tags=["podcast", "successagent"],
            privacy_status=privacy,
        )
        video_id = result.get("id") or result.get("video_id", "")
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        return JSONResponse({"ok": True, "video_id": video_id, "url": url})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    finally:
        if tmp_path and _os.path.exists(tmp_path):
            _os.unlink(tmp_path)


@app.post("/api/studio/social-posts")
async def studio_social_posts(request: Request):
    """
    Generate platform-optimized social media posts for a podcast episode.
    Body (JSON):
      title       str — episode title
      hook_line   str — episode hook line
      topic       str — episode topic
      pillar      str — content pillar
      market      str — target market / city
      episode_url str — episode URL (optional)
    """
    data        = await request.json()
    title       = data.get("title", "")
    hook_line   = data.get("hook_line", "")
    topic       = data.get("topic", "")
    pillar      = data.get("pillar", "")
    market      = data.get("market", "")
    episode_url = data.get("episode_url", "")

    if not title and not topic:
        return JSONResponse(
            {"error": "At least one of 'title' or 'topic' is required"},
            status_code=400,
        )

    user_lines = []
    if title:       user_lines.append(f"Episode title: {title}")
    if hook_line:   user_lines.append(f"Hook line: {hook_line}")
    if topic:       user_lines.append(f"Topic: {topic}")
    if pillar:      user_lines.append(f"Pillar: {pillar}")
    if market:      user_lines.append(f"Market: {market}")
    if episode_url: user_lines.append(f"Episode URL: {episode_url}")

    user_prompt = (
        "Generate social media posts for each of the following platforms based on this episode:\n\n"
        + "\n".join(user_lines)
        + "\n\nRequirements:\n"
        "- LinkedIn: 150-200 words, professional insight angle. Lead with a bold insight or stat.\n"
        "- Facebook: 100-150 words, community/story angle. Conversational, end with a question.\n"
        "- Instagram: 3 punchy lines (short, bold) followed by exactly 5 hashtags on a new line.\n"
        "- X (Twitter): under 280 characters, bold hook, no hashtags unless essential.\n"
        "If an episode URL was provided, include it naturally in LinkedIn and Facebook posts.\n"
        + (f"Use '{market}' specifically by name wherever the market is referenced — never use 'your market' or a generic placeholder.\n" if market else "")
        + "\nReturn ONLY a JSON object with keys: linkedin, facebook, instagram, x"
    )

    import openai as _openai
    client = _openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a social media copywriter for a real estate podcast. "
                    "Write platform-optimized posts that are direct, value-packed, and avoid cringe. "
                    "Use JP's voice: faith-driven, action-oriented, practitioner who shares what actually works. "
                    "All content must comply with the Fair Housing Act. Never reference protected classes, "
                    "neighborhood demographics, school quality, or any language that implies who should or "
                    "should not live somewhere. Focus only on property features, agent expertise, market "
                    "conditions, and client goals."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
    )

    import json as _json
    posts = _json.loads(response.choices[0].message.content)
    return JSONResponse({
        "linkedin":  posts.get("linkedin", ""),
        "facebook":  posts.get("facebook", ""),
        "instagram": posts.get("instagram", ""),
        "x":         posts.get("x", ""),
    })


@app.post("/api/studio/show-notes")
async def studio_show_notes(request: Request):
    """
    Generate Buzzsprout-ready show notes for a podcast episode.
    Body (JSON):
      title     str — episode title
      hook_line str — episode hook line
      topic     str — episode topic
      pillar    str — content pillar
      market    str — target market / city
      script    str — episode script (optional, used to extract real talking points)
    """
    import json as _json
    import re as _re
    import anthropic as _anthropic
    from db.engine import async_session as _async_session
    from config import get_current_location_id as _get_loc, settings as _settings
    from services.foundation import (
        assert_foundation_ready as _assert_ready,
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandContextError,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType

    data      = await request.json()
    title     = data.get("title", "")
    hook_line = data.get("hook_line", "")
    topic     = data.get("topic", "")
    pillar    = data.get("pillar", "")
    market    = data.get("market", "")
    script    = data.get("script", "")

    # ── Foundation gate ──────────────────────────────────────────────────────
    location_id = _get_loc()
    async with _async_session() as session:
        try:
            await _assert_ready(session=session, location_id=location_id)
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc), "foundation_not_ready": True}, status_code=422)
        try:
            ctx = await _get_brand_ctx(
                session=session,
                location_id=location_id,
                task_type=_TaskType.show_notes,
                topic=(topic or title) or None,
                platform=None,
                audience=None,
            )
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    # ── Foundation voice preamble ────────────────────────────────────────────
    bp = ctx.brand_profile
    vp = ctx.voice_profile
    vocab = ctx.vocabulary
    tone_str = ", ".join(vp.tone if isinstance(vp.tone, list) else [vp.tone]) if vp.tone else "direct, genuine"
    cadence_str = vp.cadence or "natural, punchy"
    vocab_yes = "Use naturally: " + ", ".join(vocab.use if isinstance(vocab.use, list) else [vocab.use]) if vocab.use else ""
    vocab_no  = "Avoid entirely: " + ", ".join(vocab.avoid if isinstance(vocab.avoid, list) else [vocab.avoid]) if vocab.avoid else ""
    voice_examples = ""
    if ctx.voice_samples:
        examples = [f'  — "{s.text[:300]}"' for s in ctx.voice_samples[:3]]
        voice_examples = "Voice samples (match this style):\n" + "\n".join(examples)
    voice_preamble = (
        f"VOICE PROFILE:\n- Tone: {tone_str}\n- Cadence: {cadence_str}\n- POV: {vp.pov or 'first-person'}\n"
    )
    if vocab_yes:      voice_preamble += f"- {vocab_yes}\n"
    if vocab_no:       voice_preamble += f"- {vocab_no}\n"
    if voice_examples: voice_preamble += f"\n{voice_examples}\n"

    user_lines = []
    if title:     user_lines.append(f"Episode title: {title}")
    if hook_line: user_lines.append(f"Hook line: {hook_line}")
    if topic:     user_lines.append(f"Topic: {topic}")
    if pillar:    user_lines.append(f"Pillar: {pillar}")
    if market:    user_lines.append(f"Market: {market}")
    if script:
        # Truncate script to avoid token overload — first 3000 chars is usually enough
        user_lines.append(f"\nEpisode script (excerpt):\n{script[:3000]}")

    user_prompt = (
        "Generate complete Buzzsprout-ready show notes for this podcast episode:\n\n"
        + "\n".join(user_lines)
        + "\n\nInclude:\n"
        "1. Episode summary (2-3 sentences, clear and value-packed)\n"
        "2. Key takeaways (3-5 bullet points, specific and actionable)\n"
        "3. Call to action (subscribe, leave a review, connect with JP)\n\n"
        "If a script was provided, extract the real talking points from it rather than inventing them.\n"
        "Format in clean markdown. Do not include a title heading — Buzzsprout adds that separately."
    )

    system_prompt = voice_preamble + "\n" + (
        "You are a podcast producer writing show notes for a real estate podcast. "
        "Be concise, value-focused, and SEO-aware. "
        "All content must comply with the Fair Housing Act. Never reference protected classes, "
        "neighborhood demographics, school quality, or any language that implies who should or "
        "should not live somewhere. Focus only on property features, agent expertise, market "
        "conditions, and client goals."
    )

    try:
        client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1200,
            temperature=0.75,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        show_notes = (message.content[0].text if message.content else "").strip()

        # Audit log (non-blocking)
        try:
            import uuid as _uuid
            async with _async_session() as _audit:
                await _audit.execute(
                    __import__("sqlalchemy").text("""
                        INSERT INTO audit_log (id, location_id, action, payload, created_at)
                        VALUES (:id, :loc_id, 'show_notes', CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {"id": str(_uuid.uuid4()), "loc_id": location_id,
                     "payload": _json.dumps({"topic": (topic or title), "model": "claude-sonnet-4-5",
                                             "sample_count": ctx.metadata.sample_count})},
                )
                await _audit.commit()
        except Exception:
            pass

        return JSONResponse({
            "show_notes": show_notes,
            "_foundation_thin": ctx.metadata.sample_count < 15,
            "_sample_count": ctx.metadata.sample_count,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Brand Studio
# ---------------------------------------------------------------------------

@app.post("/api/brand/profile-audit")
async def brand_profile_audit(request: Request):
    """
    Audit an existing agent profile URL and return a Brand Score.
    Body (JSON — all optional):
      url      — profile URL (LinkedIn, YouTube, Instagram)
      platform — "linkedin" | "youtube" | "instagram" | "manual"
      bio_text — manually pasted bio text (fallback)
    """
    data      = await request.json()
    url       = data.get("url", "")
    bio_text  = data.get("bio_text", "")

    scraped_text = ""
    scraped_ok   = False
    if url:
        try:
            import requests as _req
            from bs4 import BeautifulSoup as _BS
            resp = _req.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; PodClick/1.0)"},
                timeout=8,
            )
            soup = _BS(resp.text, "html.parser")
            parts = [t.get_text(" ", strip=True) for t in soup.find_all(["p", "h1", "h2", "h3"])]
            scraped_text = " ".join(parts)[:3000]
            scraped_ok   = bool(scraped_text.strip())
        except Exception:
            scraped_text = ""

    profile_text = scraped_text or bio_text
    if not profile_text.strip():
        return JSONResponse({
            "error": "manual_required",
            "message": (
                "We could not access that profile automatically. "
                "Please paste your bio text below and resubmit with platform='manual'."
            ),
        })

    audit_prompt = (
        "Audit this real estate agent's profile text and return a brand score JSON.\n\n"
        f"PROFILE TEXT:\n{profile_text}\n\n"
        "Return JSON with these exact keys:\n"
        '- "brand_score": integer 1-100 overall brand strength\n'
        '- "axes": object with integer scores 1-20 each for: '
        "clarity, niche_authority, consistency, cta_strength, visual_identity\n"
        '- "strengths": list of 3 specific strengths (quote from the profile where possible)\n'
        '- "gaps": list of 3 specific gaps with an actionable fix for each\n'
        '- "bio_rewrite": rewritten improved bio, 150-200 words\n'
        "Be direct and specific. Quote their actual words. No generic advice."
    )

    try:
        import openai as _openai
        client = _openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an elite personal brand strategist auditing a real estate agent's "
                        "online profile. Be direct, specific, and actionable. "
                        "Quote their actual words. No generic advice."
                    ),
                },
                {"role": "user", "content": audit_prompt},
            ],
        )
        import json as _json
        result = _json.loads(response.choices[0].message.content)
        result["scraped_ok"] = scraped_ok
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/brand/voice-brain")
async def brand_voice_brain(request: Request):
    """
    Extract a voice fingerprint from content URLs or transcript text.
    Body (JSON):
      urls        — list of YouTube video URLs (optional)
      transcripts — list of transcript/script text strings (optional)
    """
    data        = await request.json()
    urls        = data.get("urls", [])
    transcripts = data.get("transcripts", [])

    import re as _re
    collected_text = []

    yt_api_key = os.getenv("YOUTUBE_API_KEY", "")
    for u in urls:
        match = _re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", u)
        if match and yt_api_key:
            vid_id = match.group(1)
            meta   = _yt_get(
                f"https://www.googleapis.com/youtube/v3/videos"
                f"?part=snippet&id={vid_id}&key={yt_api_key}"
            )
            items = meta.get("items", [])
            if items:
                snip = items[0].get("snippet", {})
                collected_text.append(
                    f"Title: {snip.get('title', '')}\n"
                    f"Description: {snip.get('description', '')[:500]}"
                )

    for t in transcripts:
        if t.strip():
            collected_text.append(t.strip())

    combined = "\n\n".join(collected_text)[:6000]
    if not combined.strip():
        return JSONResponse({
            "error": "no_content",
            "message": "No content could be extracted. Please paste transcripts directly.",
        })

    fingerprint_prompt = (
        "Analyze this content from a real estate agent and extract their authentic voice fingerprint.\n\n"
        f"CONTENT:\n{combined}\n\n"
        "Return JSON with these exact keys:\n"
        '- "vocabulary": list of 8-12 words or short phrases this person uses frequently\n'
        '- "energy_level": one sentence describing their energy and pace\n'
        '- "signature_phrases": list of 3-5 actual phrases or sentence patterns they use '
        "(quote directly from the content where possible)\n"
        '- "topics": list of 4-6 topics they return to most\n'
        '- "communication_style": 2-3 sentences on HOW they communicate, not what they say\n'
        '- "system_prompt_fragment": a direct instruction for an AI to write in this person\'s voice. '
        'Start with "Write in the voice of someone who:" then list 3-4 specific behavioral traits '
        "with quoted examples from their content. Be specific enough that output from this instruction "
        "sounds like them, not generic real estate content.\n"
        "Quote their actual words wherever possible."
    )

    try:
        import openai as _openai
        client = _openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.4,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a voice and brand analyst specializing in real estate content creators. "
                        "Extract authentic communication patterns. Quote actual phrases when possible. "
                        "The system_prompt_fragment must be specific enough for another AI to replicate "
                        "their voice, not produce generic real estate copy."
                    ),
                },
                {"role": "user", "content": fingerprint_prompt},
            ],
        )
        import json as _json
        fp = _json.loads(response.choices[0].message.content)
        return JSONResponse({"voice_fingerprint": fp})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/brand/voice-capture")
async def brand_voice_capture(request: Request):
    """
    Transcribe a mic recording blob and extract a voice fingerprint.
    Body: multipart/form-data
      audio — audio blob (webm/opus from browser MediaRecorder)
    """
    import tempfile as _tmp

    form       = await request.form()
    audio_file = form.get("audio")
    if not audio_file:
        return JSONResponse({"error": "missing audio field"}, status_code=400)

    tmp_path = None
    try:
        import openai as _openai
        client = _openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        audio_bytes = await audio_file.read()
        with _tmp.NamedTemporaryFile(delete=False, suffix=".webm") as tf:
            tf.write(audio_bytes)
            tmp_path = tf.name

        with open(tmp_path, "rb") as af:
            transcript_obj = client.audio.transcriptions.create(
                model="whisper-1",
                file=af,
                response_format="text",
            )
        transcript_text = transcript_obj if isinstance(transcript_obj, str) else str(transcript_obj)

        fingerprint_prompt = (
            "Analyze this short spoken response from a real estate agent and extract their voice fingerprint.\n\n"
            f"TRANSCRIPT:\n{transcript_text}\n\n"
            "Return JSON with these exact keys:\n"
            '- "vocabulary": list of 5-8 distinctive words or phrases\n'
            '- "energy_level": one sentence describing their speaking energy\n'
            '- "signature_phrases": list of 2-3 notable phrases or sentence patterns\n'
            '- "topics": list of 3-4 themes they touched on\n'
            '- "communication_style": 1-2 sentences on HOW they communicate\n'
            '- "system_prompt_fragment": direct AI instruction starting with '
            '"Write in the voice of someone who:" with 2-3 specific traits and quoted examples\n'
        )

        fp_response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.4,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a voice analyst. Extract authentic communication patterns "
                        "from a spoken response. Even from a short sample, identify what makes "
                        "this person's voice distinctive."
                    ),
                },
                {"role": "user", "content": fingerprint_prompt},
            ],
        )
        import json as _json
        fp = _json.loads(fp_response.choices[0].message.content)
        return JSONResponse({"transcript": transcript_text, "voice_fingerprint": fp})

    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


@app.post("/api/brand/intake")
async def brand_intake(request: Request):
    """
    Generate a complete brand brief for a real estate agent.
    Body (JSON — all fields optional):
      name, market, niche, price_range, years, brokerage, tone, personality,
      style, mood, differentiator, platform, frequency
    """
    data         = await request.json()
    name         = data.get("name", "")
    market       = data.get("market", "")
    niche        = data.get("niche", "")
    price_range  = data.get("price_range", "")
    years        = data.get("years", "")
    brokerage    = data.get("brokerage", "")
    tone         = data.get("tone", "")
    personality  = data.get("personality", "")
    style        = data.get("style", "")
    mood         = data.get("mood", "")
    differentiator = data.get("differentiator", "")
    platform     = data.get("platform", "")
    frequency         = data.get("frequency", "")
    voice_fingerprint = data.get("voice_fingerprint", None)

    sig_phrases = (voice_fingerprint or {}).get("signature_phrases", [])
    sig_line    = (
        f"- Their signature phrases (weave these into outputs): {', '.join(sig_phrases[:3])}\n"
        if sig_phrases else ""
    )

    user_prompt = (
        "Build a complete brand brief for a real estate agent with these inputs:\n"
        f"- Name: {name}, Market: {market}, Niche: {niche}\n"
        f"- Price range: {price_range}, Years active: {years}, Brokerage: {brokerage}\n"
        f"- Brand voice: {tone} / {personality} / {style}\n"
        f"- Visual direction: {mood}\n"
        f"- What makes them different: {differentiator}\n"
        f"- Primary platform: {platform}, Posting frequency: {frequency}\n"
        + sig_line
        + "IMPORTANT: Avoid generic real estate phrases — use the agent's specific market, niche, and voice throughout every output. "
        "Every field must reflect this specific agent, not a template agent.\n\n"
        "Return JSON with:\n"
        '- "value_prop" (string): One-line value proposition: "I help [specific who] [achieve specific outcome] without [specific obstacle]"\n'
        '- "ica_description" (string): 2-3 sentences describing the ideal client avatar with specific details\n'
        '- "pain_points" (list of 5 strings): Specific emotional and practical pain points of the ICA\n'
        '- "brand_voice_guide" (string): 3-4 sentences describing how to write and speak in this brand\'s voice with examples\n'
        '- "color_direction" (string): Specific palette recommendation (2-3 colors, hex codes, rationale)\n'
        '- "typography_direction" (string): Font pairing recommendation with rationale\n'
        '- "content_pillars" (list of 4 strings): Named content pillars specific to this niche and market\n'
        '- "thumbnail_formula" (string): The thumbnail formula for this brand (emotion + text + visual element)\n'
        '- "bio_one_liner" (string): The punchy one-liner bio that goes at the top of every profile\n'
        '- "brand_brief_markdown" (string): A complete formatted brand brief document in markdown (use the other fields to compose it — 400-600 words)'
    )

    try:
        import openai as _openai
        client = _openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.7,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an elite personal brand strategist for real estate agents. "
                        "You distill an agent's inputs into a complete, differentiated brand brief. "
                        "Be specific and direct — no generic advice. "
                        "Avoid generic real estate phrases — every output must use the agent's specific market, niche, and voice. "
                        "All content must comply with the Fair Housing Act. Never reference protected classes, "
                        "neighborhood demographics, school quality, or any language that implies who should or "
                        "should not live somewhere. Focus only on property features, agent expertise, market "
                        "conditions, and client goals."
                        + (
                            "\n\nVOICE FINGERPRINT — this agent's authentic communication style:\n"
                            + (voice_fingerprint or {}).get("system_prompt_fragment", "")
                            + "\nUse their actual phrases and sentence patterns throughout. "
                            "Do NOT default to generic real estate agent language."
                            if voice_fingerprint and (voice_fingerprint or {}).get("system_prompt_fragment")
                            else ""
                        )
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        )
        import json as _json
        data_out = _json.loads(response.choices[0].message.content)
        return JSONResponse(data_out)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/brand/bio-pack")
async def brand_bio_pack(request: Request):
    """
    Generate platform-optimized bios + headshot prompt from brand brief.
    Body (JSON):
      brand_data — the full brand_brief JSON from /api/brand/intake
    """
    data       = await request.json()
    brand_data = data.get("brand_data", {})

    brand_brief_markdown = brand_data.get("brand_brief_markdown", "")
    value_prop           = brand_data.get("value_prop", "")
    bio_one_liner        = brand_data.get("bio_one_liner", "")
    brand_voice_guide    = brand_data.get("brand_voice_guide", "")

    user_prompt = (
        "Write platform-optimized bios for a real estate agent with this brand brief:\n"
        f"{brand_brief_markdown}\n"
        f"Value prop: {value_prop}\n"
        f"Bio one-liner: {bio_one_liner}\n"
        + (f"Brand voice guide: {brand_voice_guide}\n" if brand_voice_guide else "")
        + "\nIMPORTANT: Quote the value_prop verbatim in at least one bio — use their exact words, not a paraphrase. "
        "Avoid generic real estate phrases — every bio must reflect this agent's specific market, niche, and voice.\n\n"
        "Platform length limits (hard constraints):\n"
        "- Instagram: ≤150 characters total\n"
        "- LinkedIn: ≤300 words\n"
        "- YouTube: 150-200 words\n"
        "- Facebook: 100-150 words\n"
        "- Google Business: ≤100 words\n\n"
        "Return JSON with:\n"
        '- "instagram" (string): Instagram bio (≤150 chars, punchy, includes value prop + market + CTA line)\n'
        '- "linkedin" (string): LinkedIn "About" section (≤300 words, professional story arc, ends with CTA)\n'
        '- "youtube" (string): YouTube channel description (150-200 words, keyword-rich, includes what viewers get from subscribing)\n'
        '- "facebook" (string): Facebook Page "About" (100-150 words, community-focused, warm tone)\n'
        '- "google_business" (string): Google Business description (≤100 words, local SEO optimized)\n'
        '- "headshot_prompt" (string): A detailed AI image generation prompt for creating professional headshots for this brand\'s style (mood, attire, background, expression, lighting — be very specific, 100 words)\n'
        '- "consistency_checklist" (list of 8 strings): Specific checklist items for ensuring brand consistency across all platforms'
    )

    try:
        import openai as _openai
        client = _openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.7,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a social media copywriter who specializes in real estate agent personal brands. "
                        "You write bios that are punchy, specific, and convert viewers to followers to leads. "
                        "All content must comply with the Fair Housing Act. Never reference protected classes, "
                        "neighborhood demographics, school quality, or any language that implies who should or "
                        "should not live somewhere. Focus only on property features, agent expertise, market "
                        "conditions, and client goals."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        )
        import json as _json
        data_out = _json.loads(response.choices[0].message.content)
        return JSONResponse(data_out)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/brand/content-plan")
async def brand_content_plan(request: Request):
    """
    Generate a 12-month content plan from brand brief.
    Body (JSON):
      brand_data — the full brand_brief JSON from /api/brand/intake
    """
    data       = await request.json()
    brand_data = data.get("brand_data", {})

    niche           = brand_data.get("niche", "")
    market          = brand_data.get("market", "")
    content_pillars = brand_data.get("content_pillars", [])
    platform        = brand_data.get("platform", "")
    frequency       = brand_data.get("frequency", "")
    value_prop      = brand_data.get("value_prop", "")

    user_prompt = (
        "Create a 12-month content plan for:\n"
        f"Niche: {niche}, Market: {market}\n"
        f"Content pillars: {content_pillars}\n"
        f"Platform: {platform}, Frequency: {frequency}\n"
        f"Value prop: {value_prop}\n\n"
        "IMPORTANT RULES:\n"
        f"- Every topic title MUST include the market name '{market}' — never use a placeholder like 'your city' or 'your market'.\n"
        f"- Every topic must connect to the agent's niche: '{niche}'.\n"
        "- Use the provided content pillars exactly as the pillar categories — do not invent new pillar names.\n\n"
        "Return JSON with:\n"
        '- "months" (list of 12 objects): Each object has:\n'
        '  - "month" (string): e.g. "January"\n'
        '  - "theme" (string): Monthly content theme\n'
        '  - "topics" (list of 4 objects): Each topic has "title" (string), "pillar" (string), '
        '"hook" (string, 1 punchy opening line), "format" (string: "Short-form" | "YouTube" | "Both")'
    )

    try:
        import openai as _openai
        client = _openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.7,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a content strategist for real estate agents who specializes in YouTube and social media growth. "
                        "You create 12-month plans that build authority and generate leads. "
                        "All content must comply with the Fair Housing Act. Never reference protected classes, "
                        "neighborhood demographics, school quality, or any language that implies who should or "
                        "should not live somewhere. Focus only on property features, agent expertise, market "
                        "conditions, and client goals."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        )
        import json as _json
        data_out = _json.loads(response.choices[0].message.content)
        return JSONResponse(data_out)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/brand/conversion")
async def brand_conversion(request: Request):
    """
    Generate a full conversion copy pack (lead magnet, VSL, email sequence).
    Body (JSON):
      brand_data — the full brand_brief JSON from /api/brand/intake
    """
    data       = await request.json()
    brand_data = data.get("brand_data", {})

    niche            = brand_data.get("niche", "")
    market           = brand_data.get("market", "")
    ica_description  = brand_data.get("ica_description", "")
    pain_points      = brand_data.get("pain_points", [])
    value_prop       = brand_data.get("value_prop", "")
    brand_voice_guide  = brand_data.get("brand_voice_guide", "")
    content_pillars  = brand_data.get("content_pillars", [])

    user_prompt = (
        "Create a full conversion copy pack for:\n"
        f"Niche: {niche}, Market: {market}\n"
        f"ICA (primary targeting anchor — write directly TO this person): {ica_description}\n"
        f"Pain points: {pain_points}\n"
        f"Value prop: {value_prop}\n"
        f"Brand voice: {brand_voice_guide}\n"
        + (f"Content pillars: {content_pillars}\n" if content_pillars else "")
        + "\n"
        "Return JSON with:\n"
        '- "lead_magnet_title" (string): Compelling lead magnet title (checklist/guide/cheat sheet format)\n'
        '- "lead_magnet_outline" (list of 7-10 strings): Specific bullet points that make up the lead magnet content\n'
        '- "vsl_script" (string): 300-400 word Video Sales Letter script (Hook → Problem → Agitate → Solution → Proof → CTA structure, broken into labeled sections)\n'
        '- "emails" (list of 5 objects): Each has "subject" (string) and "body" (string, 150-200 words). '
        'Email sequence: Welcome → Value → Story → Objection → CTA'
    )

    try:
        import openai as _openai
        client = _openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.7,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a direct response copywriter who specializes in real estate agent lead generation funnels. "
                        "You write copy that converts viewers into booked appointments. "
                        "All content must comply with the Fair Housing Act. Never reference protected classes, "
                        "neighborhood demographics, school quality, or any language that implies who should or "
                        "should not live somewhere. Focus only on property features, agent expertise, market "
                        "conditions, and client goals."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        )
        import json as _json
        data_out = _json.loads(response.choices[0].message.content)
        return JSONResponse(data_out)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/youtube/upload")
async def youtube_upload(request: Request):
    """
    Upload a processed episode video to YouTube.

    Body (JSON):
      job_id         str  — job whose output_path to upload
      title          str  — video title
      description    str  — video description (optional)
      tags           list — list of tag strings (optional)
      privacy        str  — "private"|"unlisted"|"public" (default: "private")
      category_id    str  — YouTube category ID (default: "22")
      thumbnail_path str  — absolute path to thumbnail image (optional)
      is_short       bool — if true, use portrait-cropped clip path
    """
    from pipeline.youtube import upload_video, is_authorized

    if not is_authorized():
        return JSONResponse({"error": "YouTube not connected. Go to Settings → Connect YouTube."}, status_code=400)

    data      = await request.json()
    job_id    = data.get("job_id", "")
    title     = data.get("title", "").strip()
    desc      = data.get("description", "")
    tags      = data.get("tags", [])
    privacy   = data.get("privacy", "private")
    cat_id    = data.get("category_id", "22")
    thumb     = data.get("thumbnail_path", "")
    is_short  = data.get("is_short", False)

    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)

    # Resolve video file path from job
    video_path = ""
    if job_id:
        job_file = JOBS_DIR / f"{job_id}.json"
        if job_file.exists():
            job = json.loads(job_file.read_text())
            # Prefer short clip output if is_short=True
            if is_short and job.get("clip_vertical_path"):
                video_path = job["clip_vertical_path"]
            else:
                video_path = job.get("output_path") or job.get("normalized_path") or ""
    else:
        # Direct path override
        video_path = data.get("video_path", "")

    if not video_path or not Path(video_path).exists():
        return JSONResponse({"error": f"Video file not found for job {job_id}"}, status_code=404)

    # Append #Shorts to title for shorts
    if is_short and "#Shorts" not in title:
        title = title + " #Shorts"

    # Add common podcast tags
    if not tags:
        tags = ["podcast", "successagent"]

    result = upload_video(
        video_path     = video_path,
        title          = title,
        description    = desc,
        tags           = tags,
        category_id    = cat_id,
        privacy_status = privacy,
        thumbnail_path = thumb,
    )

    if result.get("ok"):
        # Save URL back to job
        if job_id:
            job_file = JOBS_DIR / f"{job_id}.json"
            if job_file.exists():
                job = json.loads(job_file.read_text())
                field = "youtube_short_url" if is_short else "youtube_url"
                job[field] = result["url"]
                job_file.write_text(json.dumps(job, indent=2))

                # Also update guest record if linked
                guest_id = job.get("guest_id", "")
                if guest_id and not is_short:
                    guests = _load_guests()
                    for g in guests:
                        if g.get("id") == guest_id:
                            g["episode_url_youtube"] = result["url"]
                            break
                    _save_guests(guests)

        return JSONResponse({
            "ok":       True,
            "video_id": result["video_id"],
            "url":      result["url"],
            "privacy":  privacy,
        })
    else:
        return JSONResponse({"error": result.get("error", "Upload failed")}, status_code=500)


# ── Automation Ingest ──────────────────────────────────────────────────────────
@app.post("/api/automation/ingest")
async def automation_ingest(request: Request):
    """
    Called by the podcast-automation pipeline (Telegram → Approve).
    Accepts a pre-rendered MP3 path + episode metadata, kicks off processing,
    and returns a job_id to poll for status.

    Body (JSON):
      audio_path      str  — absolute path to the rendered MP3 on this machine
      title           str  — episode title
      episode_number  int  — episode number
      podcast_name    str  — podcast name
      description     str  — script/description (optional)
      keywords        list — keyword strings (optional)
    """
    import shutil

    data           = await request.json()
    audio_path     = (data.get("audio_path") or "").strip()
    title          = (data.get("title") or "Untitled Episode").strip()
    episode_number = int(data.get("episode_number") or 0)
    podcast_name   = (data.get("podcast_name") or os.getenv("PODCAST_NAME", "Success Agent Podcast")).strip()
    description    = (data.get("description") or "").strip()

    if not audio_path or not Path(audio_path).exists():
        return JSONResponse({"error": f"audio_path not found: {audio_path}"}, status_code=400)

    # Copy MP3 into a job upload dir so PodClickAI owns it
    job_id  = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    dest_path = job_dir / Path(audio_path).name
    shutil.copy2(audio_path, dest_path)

    # Register job
    jobs[job_id] = {
        "job_id":         job_id,
        "status":         "queued",
        "step":           "queued",
        "log":            ["Automation ingest received"],
        "title":          title,
        "episode_number": episode_number or _next_episode_number(),
        "podcast_name":   podcast_name,
        "description":    description,
        "mp3_path":       str(dest_path),
    }
    job_ws_queues[job_id] = asyncio.Queue()

    clips = [{
        "path":     str(dest_path),
        "type":     "main",
        "filename": dest_path.name,
        "is_image": False,
    }]

    # Kick off pipeline in background
    asyncio.ensure_future(run_pipeline(
        job_id=job_id,
        clips=clips,
        model_size="base",
        podcast_name=podcast_name,
        studio_mode="audio",
        episode_number_override=episode_number,
    ))

    return JSONResponse({
        "ok":     True,
        "job_id": job_id,
        "status": "processing",
        "poll":   f"/api/jobs/{job_id}",
    })


# ── YouTube Studio ─────────────────────────────────────────────────────────────

import httpx as _httpx

# In-memory job store for YouTube Competitor Spy analysis jobs.
yt_spy_jobs: dict[str, dict] = {}

_YT_STEPS = [
    "scanning_market",
    "identifying_competitors",
    "analyzing_viral",
    "finding_outliers",
    "mapping_standards",
    "discovering_searches",
    "compiling_intelligence",
    "complete",
]


@app.get("/youtube-studio")
async def youtube_studio_page():
    return FileResponse("frontend/youtube-studio.html")


@app.get("/brand-studio")
async def brand_studio_page():
    return FileResponse("frontend/brand-studio.html")


@app.get("/social-studio")
async def social_studio_page():
    return FileResponse("frontend/social-studio.html")


@app.get("/foundation")
async def foundation_page():
    return FileResponse("frontend/foundation.html")


@app.get("/blueprint")
async def blueprint_page():
    return FileResponse("frontend/blueprint.html")


# ---------------------------------------------------------------------------
# Social Studio
# ---------------------------------------------------------------------------

_SOCIAL_HASHTAGS_PATH = "data/social_hashtags.json"
_SOCIAL_CALENDAR_PATH = "data/social_calendar.json"
_SOCIAL_FH_CLAUSE = (
    "All content must comply with the Fair Housing Act. Never reference protected classes, "
    "neighborhood demographics, school quality, or any language that implies who should or "
    "should not live somewhere. Focus only on property features, agent expertise, market "
    "conditions, and client goals."
)


@app.get("/api/social/hashtags")
async def social_hashtags_load():
    """Return saved hashtag sets, or empty defaults."""
    import json as _json
    default = {"core": [], "niche": [], "local": [], "trending": [], "market": "", "niche_input": ""}
    try:
        if os.path.exists(_SOCIAL_HASHTAGS_PATH):
            with open(_SOCIAL_HASHTAGS_PATH) as f:
                return JSONResponse(_json.load(f))
    except Exception:
        pass
    return JSONResponse(default)


@app.post("/api/social/hashtags")
async def social_hashtags_generate(request: Request):
    """Generate 4 hashtag sets for a market+niche and persist to disk."""
    import json as _json
    import re as _re
    import anthropic as _anthropic
    from db.engine import async_session as _async_session
    from config import get_current_location_id as _get_loc, settings as _settings
    from services.foundation import (
        assert_foundation_ready as _assert_ready,
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandContextError,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType

    data = await request.json()
    market = data.get("market", "")
    niche_input = data.get("niche_input", "")

    # ── Foundation gate ──────────────────────────────────────────────────────
    location_id = _get_loc()
    async with _async_session() as session:
        try:
            await _assert_ready(session=session, location_id=location_id)
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc), "foundation_not_ready": True}, status_code=422)
        try:
            ctx = await _get_brand_ctx(
                session=session,
                location_id=location_id,
                task_type=_TaskType.hashtag_set,
                topic=(niche_input or market) or None,
                platform=None,
                audience=None,
            )
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    # ── Foundation voice preamble ────────────────────────────────────────────
    bp = ctx.brand_profile
    vp = ctx.voice_profile
    vocab = ctx.vocabulary
    tone_str = ", ".join(vp.tone if isinstance(vp.tone, list) else [vp.tone]) if vp.tone else "direct, genuine"
    cadence_str = vp.cadence or "natural, punchy"
    vocab_yes = "Use naturally: " + ", ".join(vocab.use if isinstance(vocab.use, list) else [vocab.use]) if vocab.use else ""
    vocab_no  = "Avoid entirely: " + ", ".join(vocab.avoid if isinstance(vocab.avoid, list) else [vocab.avoid]) if vocab.avoid else ""
    voice_examples = ""
    if ctx.voice_samples:
        examples = [f'  — "{s.text[:300]}"' for s in ctx.voice_samples[:3]]
        voice_examples = "Voice samples (match this style):\n" + "\n".join(examples)
    voice_preamble = (
        f"VOICE PROFILE:\n- Tone: {tone_str}\n- Cadence: {cadence_str}\n- POV: {vp.pov or 'first-person'}\n"
    )
    if vocab_yes:      voice_preamble += f"- {vocab_yes}\n"
    if vocab_no:       voice_preamble += f"- {vocab_no}\n"
    if voice_examples: voice_preamble += f"\n{voice_examples}\n"

    user_prompt = (
        f"Generate optimized hashtag sets for a real estate agent.\n"
        f"Market/city: {market}\n"
        f"Niche: {niche_input}\n\n"
        "Return JSON with exactly these keys:\n"
        '- "core" (list of 10 strings): Evergreen hashtags to use on EVERY post — brand, profession, content-type tags\n'
        '- "niche" (list of 10 strings): Real estate niche-specific hashtags for this agent\'s specialty\n'
        '- "local" (list of 10 strings): Market and city-specific hashtags (use the actual city/market name)\n'
        '- "trending" (list of 5 strings): Currently popular hashtags in real estate content\n'
        "Each hashtag must include the # prefix. No duplicates across sets. Mix popularity levels."
    )

    system_prompt = voice_preamble + "\n" + (
        "You are a social media strategist specializing in real estate agent content. "
        "Generate hashtag sets that maximize reach and niche authority. "
        + _SOCIAL_FH_CLAUSE
    )

    try:
        client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=800,
            temperature=0.75,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text if message.content else "{}"
        raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.IGNORECASE)
        raw = _re.sub(r"\s*```$", "", raw.strip())
        result = _json.loads(raw.strip())
        result["market"] = market
        result["niche_input"] = niche_input
        os.makedirs("data", exist_ok=True)
        with open(_SOCIAL_HASHTAGS_PATH, "w") as f:
            _json.dump(result, f, indent=2)

        # Audit log (non-blocking)
        try:
            import uuid as _uuid
            async with _async_session() as _audit:
                await _audit.execute(
                    __import__("sqlalchemy").text("""
                        INSERT INTO audit_log (id, location_id, action, payload, created_at)
                        VALUES (:id, :loc_id, 'hashtag_set', CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {"id": str(_uuid.uuid4()), "loc_id": location_id,
                     "payload": _json.dumps({"topic": (niche_input or market), "model": "claude-sonnet-4-5",
                                             "sample_count": ctx.metadata.sample_count})},
                )
                await _audit.commit()
        except Exception:
            pass

        result["_foundation_thin"] = ctx.metadata.sample_count < 15
        result["_sample_count"] = ctx.metadata.sample_count
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/social/forge")
async def social_forge(request: Request):
    """Generate platform-optimized social posts — Foundation-powered.

    Gate: assert_foundation_ready() → BrandContextError if < 5 samples.
    Flow: get_brand_context() → voice samples as few-shot → Claude claude-sonnet-4-5 call.
    Returns foundation_not_ready error when Foundation is not poured.
    """
    import json as _json
    import anthropic as _anthropic
    from db.engine import async_session as _async_session
    from config import get_current_location_id as _get_loc, settings as _settings
    from services.foundation import (
        assert_foundation_ready as _assert_ready,
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandContextError,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType

    data = await request.json()
    mode      = data.get("mode", "idea")
    topic     = data.get("topic", "")
    title     = data.get("title", "")
    hook_line = data.get("hook_line", "")
    market    = data.get("market", "")
    template  = data.get("template", "")
    extra     = data.get("extra", {})

    # ── 1. Foundation gate ──────────────────────────────────────────────────
    location_id = _get_loc()
    async with _async_session() as session:
        try:
            await _assert_ready(session=session, location_id=location_id)
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc), "foundation_not_ready": True}, status_code=422)

        # ── 2. Brand context retrieval ──────────────────────────────────────
        try:
            ctx = await _get_brand_ctx(
                session=session,
                location_id=location_id,
                task_type=_TaskType.linkedin_post,
                topic=topic or title or template or None,
                platform=None,
                audience=None,
            )
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    # ── 3. Build content block ───────────────────────────────────────────────
    if mode == "idea":
        content_block = f"Topic/idea to post about: {topic}\nMarket: {market}"
    elif mode == "episode":
        content_block = (
            f"Episode title: {title}\nHook line: {hook_line}\nTopic: {topic}\nMarket: {market}"
        )
    else:
        tmpl_fields = "\n".join(f"{k}: {v}" for k, v in extra.items() if v)
        content_block = f"Template: {template}\nMarket: {market}\n{tmpl_fields}"

    # ── 4. Build Foundation-powered system prompt ────────────────────────────
    bp = ctx.brand_profile
    vp = ctx.voice_profile
    vocab = ctx.vocabulary

    brand_block = ""
    if bp.one_liner:
        brand_block += f"\nValue proposition: {bp.one_liner}"
    if bp.niche_primary:
        brand_block += f"\nNiche: {bp.niche_primary}"
    if bp.audience_primary:
        brand_block += f"\nPrimary audience: {bp.audience_primary}"
    if bp.differentiators:
        brand_block += f"\nDifferentiators: {', '.join(bp.differentiators)}"

    tone_str = ""
    if vp.tone:
        tones = vp.tone if isinstance(vp.tone, list) else [vp.tone]
        tone_str = ", ".join(tones)
    cadence_str = vp.cadence or ""
    pov_str = vp.pov or "first-person"
    humor_str = vp.humor_level or "none"

    vocab_yes = ""
    vocab_no = ""
    if vocab.use:
        words = vocab.use if isinstance(vocab.use, list) else [vocab.use]
        vocab_yes = "Use naturally: " + ", ".join(words)
    if vocab.avoid:
        words = vocab.avoid if isinstance(vocab.avoid, list) else [vocab.avoid]
        vocab_no = "Avoid entirely: " + ", ".join(words)

    # Few-shot voice examples from Foundation
    voice_examples = ""
    if ctx.voice_samples:
        examples = [f'  — "{s.text[:300]}"' for s in ctx.voice_samples[:3]]
        voice_examples = "Voice samples (match this style):\n" + "\n".join(examples)

    system_prompt = (
        f"You are a social media copywriter writing in the authentic voice of a specific person.\n\n"
        f"VOICE PROFILE:\n"
        f"- Tone: {tone_str or 'direct, genuine'}\n"
        f"- Cadence: {cadence_str or 'natural, punchy'}\n"
        f"- POV: {pov_str}\n"
        f"- Humor: {humor_str}\n"
    )
    if vocab_yes:
        system_prompt += f"- {vocab_yes}\n"
    if vocab_no:
        system_prompt += f"- {vocab_no}\n"
    if voice_examples:
        system_prompt += f"\n{voice_examples}\n"
    system_prompt += (
        f"\n{brand_block}\n\n"
        "Write posts that sound EXACTLY like these voice samples — same rhythm, same vocabulary, "
        "same level of directness. Never generic. Never corporate.\n\n"
        + _SOCIAL_FH_CLAUSE
    )

    user_prompt = (
        f"Generate social media posts.\n\n"
        f"{content_block}\n\n"
        + (f"Use '{market}' specifically by name — never 'your market' or a placeholder.\n" if market else "")
        + "Requirements:\n"
        "- LinkedIn: 150-200 words, professional insight, lead with a bold statement or stat\n"
        "- Facebook: 100-150 words, community/story angle, conversational, end with a question\n"
        "- Instagram: 3 punchy bold lines followed by 5 relevant hashtags on a new line\n"
        "- X (Twitter): under 280 characters, bold hook, no hashtags unless essential\n"
        "- TikTok: hook line first (pattern-interrupt, 10 words max), then 3-4 short punchy lines, "
        "end with 3 trending hashtags. Casual, energetic, spoken-word style.\n\n"
        "Return ONLY a JSON object with keys: linkedin, facebook, instagram, x, tiktok"
    )

    # ── 5. Claude call ───────────────────────────────────────────────────────
    try:
        anthropic_key = _settings.anthropic_api_key
        if not anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")

        client = _anthropic.Anthropic(api_key=anthropic_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2000,
            temperature=0.75,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text if message.content else "{}"
        # Strip code fences if present
        import re as _re
        raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.IGNORECASE)
        raw = _re.sub(r"\s*```$", "", raw.strip())
        posts = _json.loads(raw.strip())

        # ── 6. Audit log ─────────────────────────────────────────────────────
        try:
            import uuid as _uuid
            async with _async_session() as audit_session:
                await audit_session.execute(
                    __import__("sqlalchemy").text("""
                        INSERT INTO audit_log
                            (id, location_id, action, payload, created_at)
                        VALUES
                            (:id, :loc_id, 'forge_post', CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": str(_uuid.uuid4()),
                        "loc_id": location_id,
                        "payload": _json.dumps({
                            "mode": mode, "topic": topic, "model": "claude-sonnet-4-5",
                            "sample_count": ctx.metadata.sample_count,
                            "foundation_score": ctx.foundation_score,
                        }),
                    },
                )
                await audit_session.commit()
        except Exception:
            pass  # audit log failure must never block content delivery

        return JSONResponse({
            "linkedin":          posts.get("linkedin", ""),
            "facebook":          posts.get("facebook", ""),
            "instagram":         posts.get("instagram", ""),
            "x":                 posts.get("x", ""),
            "tiktok":            posts.get("tiktok", ""),
            "_foundation_thin":  ctx.metadata.sample_count < 15,
            "_sample_count":     ctx.metadata.sample_count,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/social/calendar")
async def social_calendar_load():
    """Return saved calendar entries."""
    import json as _json
    try:
        if os.path.exists(_SOCIAL_CALENDAR_PATH):
            with open(_SOCIAL_CALENDAR_PATH) as f:
                entries = _json.load(f)
                return JSONResponse({"entries": entries})
    except Exception:
        pass
    return JSONResponse({"entries": []})


@app.post("/api/social/calendar")
async def social_calendar_save(request: Request):
    """Add a calendar entry and persist."""
    import json as _json
    import uuid as _uuid
    data = await request.json()
    entry = {
        "id":       str(_uuid.uuid4()),
        "day":      data.get("day", "Mon"),
        "platform": data.get("platform", "all"),
        "title":    data.get("title", ""),
        "content":  data.get("content", ""),
        "date":     data.get("date", ""),
    }
    try:
        os.makedirs("data", exist_ok=True)
        entries = []
        if os.path.exists(_SOCIAL_CALENDAR_PATH):
            with open(_SOCIAL_CALENDAR_PATH) as f:
                entries = _json.load(f)
        entries.append(entry)
        with open(_SOCIAL_CALENDAR_PATH, "w") as f:
            _json.dump(entries, f, indent=2)
        return JSONResponse({"ok": True, "id": entry["id"]})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.delete("/api/social/calendar/{entry_id}")
async def social_calendar_delete(entry_id: str):
    """Remove a calendar entry by id."""
    import json as _json
    try:
        if not os.path.exists(_SOCIAL_CALENDAR_PATH):
            return JSONResponse({"ok": True})
        with open(_SOCIAL_CALENDAR_PATH) as f:
            entries = _json.load(f)
        entries = [e for e in entries if e.get("id") != entry_id]
        with open(_SOCIAL_CALENDAR_PATH, "w") as f:
            _json.dump(entries, f, indent=2)
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/social/repurpose")
async def social_repurpose(request: Request):
    """Extract 5 social post angles from a URL or transcript."""
    import json as _json
    import re as _re
    import anthropic as _anthropic
    from db.engine import async_session as _async_session
    from config import get_current_location_id as _get_loc, settings as _settings
    from services.foundation import (
        assert_foundation_ready as _assert_ready,
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandContextError,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType

    data       = await request.json()
    url        = data.get("url", "")
    transcript = data.get("transcript", "")
    market     = data.get("market", "")

    content_summary = ""
    if url:
        try:
            yt_meta = _yt_get(f"https://www.googleapis.com/youtube/v3/videos?part=snippet&id={url.split('v=')[-1].split('&')[0]}&key={os.environ.get('YOUTUBE_DATA_API_KEY','')}")
            items = yt_meta.get("items", [])
            if items:
                snip = items[0].get("snippet", {})
                content_summary = f"Title: {snip.get('title','')}\nDescription: {snip.get('description','')[:800]}"
        except Exception:
            pass
    if not content_summary and transcript:
        content_summary = transcript[:3000]
    if not content_summary:
        content_summary = f"URL: {url}"

    # ── Foundation gate ─────────────────────────────────────────────────────
    topic_for_ctx = url or (transcript[:80] if transcript else None)
    location_id = _get_loc()
    async with _async_session() as session:
        try:
            await _assert_ready(session=session, location_id=location_id)
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc), "foundation_not_ready": True}, status_code=422)
        try:
            ctx = await _get_brand_ctx(
                session=session,
                location_id=location_id,
                task_type=_TaskType.clip_caption,
                topic=topic_for_ctx,
                platform=None,
                audience=None,
            )
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    bp = ctx.brand_profile
    vp = ctx.voice_profile
    vocab = ctx.vocabulary
    tone_str = ", ".join(vp.tone if isinstance(vp.tone, list) else [vp.tone]) if vp.tone else "direct, genuine"
    cadence_str = vp.cadence or "natural, punchy"
    vocab_yes = "Use naturally: " + ", ".join(vocab.use if isinstance(vocab.use, list) else [vocab.use]) if vocab.use else ""
    vocab_no  = "Avoid entirely: " + ", ".join(vocab.avoid if isinstance(vocab.avoid, list) else [vocab.avoid]) if vocab.avoid else ""
    voice_examples = ""
    if ctx.voice_samples:
        examples = [f'  — "{s.text[:300]}"' for s in ctx.voice_samples[:3]]
        voice_examples = "Voice samples (match this style):\n" + "\n".join(examples)
    voice_preamble = f"VOICE PROFILE:\n- Tone: {tone_str}\n- Cadence: {cadence_str}\n- POV: {vp.pov or 'first-person'}\n"
    if vocab_yes: voice_preamble += f"- {vocab_yes}\n"
    if vocab_no:  voice_preamble += f"- {vocab_no}\n"
    if voice_examples: voice_preamble += f"\n{voice_examples}\n"

    user_prompt = (
        f"Extract 5 distinct social post angles from this content for a real estate agent.\n\n"
        f"Content:\n{content_summary}\n\n"
        f"Market: {market}\n"
        + "\nEach angle should target a different platform or perspective. "
        "Mix educational, inspirational, conversational, and CTA-driven angles.\n\n"
        "Return JSON with:\n"
        '- "angles" (list of 5 objects): Each has:\n'
        '  - "angle" (string): The angle/hook in 5-8 words\n'
        '  - "platform" (string): "linkedin" | "facebook" | "instagram" | "x"\n'
        '  - "post" (string): The complete post text ready to publish (respects platform length)\n'
    )

    existing_system_content = (
        "You are a content repurposing strategist for real estate agents. "
        "Extract maximum social value from existing content, creating platform-native posts "
        "that feel fresh and specific — not recycled summaries. "
        + _SOCIAL_FH_CLAUSE
    )
    system_prompt = voice_preamble + "\n" + existing_system_content

    try:
        client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            temperature=0.75,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text if message.content else "{}"
        raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.IGNORECASE)
        raw = _re.sub(r"\s*```$", "", raw.strip())
        result = _json.loads(raw.strip())

        # Audit log (non-blocking)
        try:
            import uuid as _uuid
            async with _async_session() as _audit:
                await _audit.execute(
                    __import__("sqlalchemy").text("""
                        INSERT INTO audit_log (id, location_id, action, payload, created_at)
                        VALUES (:id, :loc_id, 'repurpose_social', CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": str(_uuid.uuid4()),
                        "loc_id": location_id,
                        "payload": _json.dumps({
                            "topic": topic_for_ctx, "model": "claude-sonnet-4-5",
                            "sample_count": ctx.metadata.sample_count,
                        }),
                    },
                )
                await _audit.commit()
        except Exception:
            pass

        return JSONResponse({
            "angles": result.get("angles", []),
            "_foundation_thin": ctx.metadata.sample_count < 15,
            "_sample_count": ctx.metadata.sample_count,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Social Publishing — OAuth + Direct Publish ────────────────────────────────

_SOCIAL_TOKENS_PATH = "data/social_tokens.json"
_social_oauth_states: dict = {}  # state → platform, ephemeral CSRF store

_META_GRAPH_VER = "v19.0"
_META_AUTH_URL  = "https://www.facebook.com/v19.0/dialog/oauth"
_META_TOKEN_URL = "https://graph.facebook.com/v19.0/oauth/access_token"
_META_GRAPH_URL = "https://graph.facebook.com/" + _META_GRAPH_VER

_LI_AUTH_URL    = "https://www.linkedin.com/oauth/v2/authorization"
_LI_TOKEN_URL   = "https://www.linkedin.com/oauth/v2/accessToken"
_LI_API_URL     = "https://api.linkedin.com/v2"


def _load_social_tokens():
    if os.path.exists(_SOCIAL_TOKENS_PATH):
        with open(_SOCIAL_TOKENS_PATH) as f:
            return json.load(f)
    return {}


def _save_social_tokens(data):
    with open(_SOCIAL_TOKENS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _base_url(request: Request):
    """Derive the server base URL from the incoming request."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host   = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{scheme}://{host}"


# ── Meta (Facebook + Instagram) ───────────────────────────────────────────────

@app.get("/api/social/meta/status")
async def meta_status():
    """Return Meta connection status and page list."""
    tokens = _load_social_tokens()
    meta   = tokens.get("meta", {})
    if not meta.get("user_token"):
        return JSONResponse({"connected": False})
    pages  = meta.get("pages", [])
    selected = meta.get("selected_page_id", pages[0]["id"] if pages else None)
    return JSONResponse({
        "connected": True,
        "pages": pages,
        "selected_page_id": selected,
    })


@app.get("/api/social/meta/auth")
async def meta_auth(request: Request):
    """Redirect browser to Meta OAuth dialog."""
    app_id = os.getenv("META_APP_ID", "")
    if not app_id:
        return JSONResponse({"error": "META_APP_ID not set in .env"}, status_code=500)
    state = str(uuid.uuid4())
    _social_oauth_states[state] = "meta"
    redirect_uri = _base_url(request) + "/api/social/meta/callback"
    scopes = "pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish"
    url = (
        f"{_META_AUTH_URL}?client_id={app_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes}"
        f"&state={state}"
        f"&response_type=code"
    )
    return RedirectResponse(url)


@app.get("/api/social/meta/callback")
async def meta_callback(request: Request):
    """Handle Meta OAuth callback — exchange code for tokens and fetch pages."""
    code  = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    error = request.query_params.get("error", "")

    if error:
        return HTMLResponse(f"<h3>Meta OAuth denied: {error}</h3><p><a href='/social-studio'>Back</a></p>")
    if not code or _social_oauth_states.pop(state, None) != "meta":
        return HTMLResponse("<h3>Invalid OAuth state.</h3><p><a href='/social-studio'>Back</a></p>")

    app_id     = os.getenv("META_APP_ID", "")
    app_secret = os.getenv("META_APP_SECRET", "")
    redirect_uri = _base_url(request) + "/api/social/meta/callback"

    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            # Exchange code for short-lived user token
            resp = await client.get(_META_TOKEN_URL, params={
                "client_id":     app_id,
                "client_secret": app_secret,
                "redirect_uri":  redirect_uri,
                "code":          code,
            })
            resp.raise_for_status()
            short_token = resp.json().get("access_token", "")

            # Exchange for long-lived token (~60 days)
            ll_resp = await client.get(_META_TOKEN_URL, params={
                "grant_type":        "fb_exchange_token",
                "client_id":         app_id,
                "client_secret":     app_secret,
                "fb_exchange_token": short_token,
            })
            ll_resp.raise_for_status()
            user_token = ll_resp.json().get("access_token", short_token)

            # Fetch user's pages
            pages_resp = await client.get(
                f"{_META_GRAPH_URL}/me/accounts",
                params={"access_token": user_token, "fields": "id,name,access_token,instagram_business_account"},
            )
            pages_resp.raise_for_status()
            raw_pages = pages_resp.json().get("data", [])

        pages = []
        for p in raw_pages:
            ig_id = None
            ig_obj = p.get("instagram_business_account")
            if ig_obj:
                ig_id = ig_obj.get("id")
            pages.append({
                "id":           p["id"],
                "name":         p.get("name", ""),
                "access_token": p.get("access_token", ""),
                "instagram_business_id": ig_id,
            })

        tokens = _load_social_tokens()
        tokens["meta"] = {
            "user_token": user_token,
            "pages": pages,
            "selected_page_id": pages[0]["id"] if pages else None,
        }
        _save_social_tokens(tokens)

    except Exception as exc:
        return HTMLResponse(f"<h3>Meta auth error: {exc}</h3><p><a href='/social-studio'>Back</a></p>")

    return HTMLResponse(
        "<h3>✅ Facebook connected!</h3>"
        f"<p>Found {len(pages)} page(s). Redirecting…</p>"
        "<script>setTimeout(()=>window.location='/social-studio',1500)</script>"
    )


@app.post("/api/social/meta/select-page")
async def meta_select_page(request: Request):
    """Set the active Facebook page for publishing."""
    body    = await request.json()
    page_id = body.get("page_id", "")
    tokens  = _load_social_tokens()
    if "meta" not in tokens:
        return JSONResponse({"error": "Not connected"}, status_code=400)
    tokens["meta"]["selected_page_id"] = page_id
    _save_social_tokens(tokens)
    return JSONResponse({"ok": True})


@app.post("/api/social/meta/disconnect")
async def meta_disconnect():
    tokens = _load_social_tokens()
    tokens.pop("meta", None)
    _save_social_tokens(tokens)
    return JSONResponse({"ok": True})


@app.post("/api/social/publish/facebook")
async def publish_facebook(request: Request):
    """Post a text message to the selected Facebook Page."""
    body    = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    tokens = _load_social_tokens()
    meta   = tokens.get("meta", {})
    if not meta.get("user_token"):
        return JSONResponse({"error": "Facebook not connected"}, status_code=400)

    pages       = meta.get("pages", [])
    selected_id = meta.get("selected_page_id")
    page        = next((p for p in pages if p["id"] == selected_id), pages[0] if pages else None)
    if not page:
        return JSONResponse({"error": "No Facebook page configured"}, status_code=400)

    try:
        async with _httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{_META_GRAPH_URL}/{page['id']}/feed",
                data={"message": message, "access_token": page["access_token"]},
            )
            resp.raise_for_status()
            data = resp.json()
        return JSONResponse({"ok": True, "post_id": data.get("id"), "page": page["name"]})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/social/publish/instagram")
async def publish_instagram(request: Request):
    """
    Instagram Feed requires a media URL — text-only posts are not supported.
    This endpoint returns the text so the client can copy it + open Instagram.
    """
    body    = await request.json()
    message = (body.get("message") or "").strip()
    tokens  = _load_social_tokens()
    meta    = tokens.get("meta", {})
    if not meta.get("user_token"):
        return JSONResponse({"error": "Instagram not connected (connect via Facebook)"}, status_code=400)
    # Instagram Feed requires an image — return the text for manual posting
    return JSONResponse({
        "ok": False,
        "requires_image": True,
        "message": message,
        "hint": "Instagram Feed requires an image. Copy the caption below and add your photo in the app.",
    })


# ── LinkedIn ──────────────────────────────────────────────────────────────────

@app.get("/api/social/linkedin/status")
async def linkedin_status():
    tokens = _load_social_tokens()
    li     = tokens.get("linkedin", {})
    if not li.get("access_token"):
        return JSONResponse({"connected": False})
    return JSONResponse({"connected": True, "name": li.get("name", ""), "urn": li.get("person_urn", "")})


@app.get("/api/social/linkedin/auth")
async def linkedin_auth(request: Request):
    client_id = os.getenv("LINKEDIN_CLIENT_ID", "")
    if not client_id:
        return JSONResponse({"error": "LINKEDIN_CLIENT_ID not set in .env"}, status_code=500)
    state = str(uuid.uuid4())
    _social_oauth_states[state] = "linkedin"
    redirect_uri = _base_url(request) + "/api/social/linkedin/callback"
    scopes = "w_member_social,r_liteprofile,r_emailaddress"
    url = (
        f"{_LI_AUTH_URL}?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes}"
        f"&state={state}"
    )
    return RedirectResponse(url)


@app.get("/api/social/linkedin/callback")
async def linkedin_callback(request: Request):
    code  = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    error = request.query_params.get("error", "")

    if error:
        return HTMLResponse(f"<h3>LinkedIn OAuth denied: {error}</h3><p><a href='/social-studio'>Back</a></p>")
    if not code or _social_oauth_states.pop(state, None) != "linkedin":
        return HTMLResponse("<h3>Invalid OAuth state.</h3><p><a href='/social-studio'>Back</a></p>")

    client_id     = os.getenv("LINKEDIN_CLIENT_ID", "")
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET", "")
    redirect_uri  = _base_url(request) + "/api/social/linkedin/callback"

    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            # Exchange code for access token
            token_resp = await client.post(_LI_TOKEN_URL, data={
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  redirect_uri,
                "client_id":     client_id,
                "client_secret": client_secret,
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})
            token_resp.raise_for_status()
            access_token = token_resp.json().get("access_token", "")

            # Fetch basic profile
            me_resp = await client.get(
                f"{_LI_API_URL}/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            me_resp.raise_for_status()
            me_data = me_resp.json()
            person_id  = me_data.get("id", "")
            first_name = (me_data.get("localizedFirstName") or "")
            last_name  = (me_data.get("localizedLastName") or "")
            name       = f"{first_name} {last_name}".strip()

        tokens = _load_social_tokens()
        tokens["linkedin"] = {
            "access_token": access_token,
            "person_urn":   f"urn:li:person:{person_id}",
            "name":         name,
        }
        _save_social_tokens(tokens)

    except Exception as exc:
        return HTMLResponse(f"<h3>LinkedIn auth error: {exc}</h3><p><a href='/social-studio'>Back</a></p>")

    return HTMLResponse(
        f"<h3>✅ LinkedIn connected as {name}!</h3>"
        "<p>Redirecting…</p>"
        "<script>setTimeout(()=>window.location='/social-studio',1500)</script>"
    )


@app.post("/api/social/linkedin/disconnect")
async def linkedin_disconnect():
    tokens = _load_social_tokens()
    tokens.pop("linkedin", None)
    _save_social_tokens(tokens)
    return JSONResponse({"ok": True})


@app.post("/api/social/publish/linkedin")
async def publish_linkedin(request: Request):
    """Post a text update to the authenticated member's LinkedIn feed."""
    body    = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    tokens = _load_social_tokens()
    li     = tokens.get("linkedin", {})
    if not li.get("access_token"):
        return JSONResponse({"error": "LinkedIn not connected"}, status_code=400)

    author = li["person_urn"]
    payload = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": message},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    try:
        async with _httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{_LI_API_URL}/ugcPosts",
                json=payload,
                headers={
                    "Authorization":            f"Bearer {li['access_token']}",
                    "X-Restli-Protocol-Version": "2.0.0",
                    "Content-Type":              "application/json",
                },
            )
            resp.raise_for_status()
            post_id = resp.headers.get("x-restli-id", "")
        return JSONResponse({"ok": True, "post_id": post_id})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/social/connections")
async def social_connections():
    """Return all platform connection statuses in one call."""
    tokens = _load_social_tokens()
    meta   = tokens.get("meta", {})
    li     = tokens.get("linkedin", {})
    pages  = meta.get("pages", [])
    selected = meta.get("selected_page_id", pages[0]["id"] if pages else None)

    from pipeline.tiktok import is_authorized as _tt_authorized
    tt_connected = _tt_authorized()

    return JSONResponse({
        "facebook": {
            "connected": bool(meta.get("user_token")),
            "pages": pages,
            "selected_page_id": selected,
        },
        "instagram": {
            "connected": bool(meta.get("user_token")),
            "note": "Instagram requires an image — caption copy only",
        },
        "linkedin": {
            "connected": bool(li.get("access_token")),
            "name": li.get("name", ""),
        },
        "tiktok": {
            "connected": tt_connected,
            "note": "Video-only platform — post clips from Clip Studio",
        },
    })


# ── GHL Social Planner — delegates to GHLAdapter (the ONLY file that calls GHL) ─

# NOTE: No direct calls to services.leadconnectorhq.com from this file.
# ALL GHL HTTP calls are in services/ghl_adapter.py.
# Verify with: rg 'leadconnectorhq' --type py (must return only ghl_adapter.py)

import logging as _logging
_ghl_logger = _logging.getLogger("podclick.social")

def _current_location_id_str() -> str:
    """Resolve active location_id string (Phase 2A: TITAN_LOCATION_ID)."""
    from config import get_current_location_id as _gcl
    return _gcl()


@app.get("/api/social/ghl/accounts")
async def ghl_social_accounts():
    """Return all connected GHL social accounts for this location via GHLAdapter."""
    from services.ghl_adapter import ghl_adapter as _ghl
    from services.social_service import SocialPublishError, SocialProviderError, SocialAuthError
    loc = _current_location_id_str()
    try:
        accounts = await _ghl.list_accounts(loc)
        return JSONResponse({"accounts": accounts})
    except SocialPublishError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except SocialAuthError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/social/ghl/publish")
async def ghl_publish_post(request: Request):
    """
    Send a post to GHL Social Planner via GHLAdapter.

    Body: { platform: str, content: str, account_id: str,
            scheduled_at: Optional[str], media_urls: Optional[list] }

    Creates PostAttempt audit record and emits lifecycle log events:
      [publish.requested]  — on receipt
      [publish.attempted]  — before the GHL call
      [publish.completed]  — on success
      [publish.failed]     — on non-retryable error
    """
    import uuid as _uuid
    from datetime import datetime, timezone
    from config import get_current_location_id as _gcl
    from db.engine import async_session as _async_session
    from db.models import Post as _Post, PostVariant as _PostVariant, PostAttempt as _PostAttempt
    from services.ghl_adapter import ghl_adapter as _ghl
    from services.social_service import (
        SocialPublishError as _PubErr,
        SocialAuthError as _AuthErr,
        SocialRateLimitError as _RLErr,
        SocialProviderError as _ProvErr,
    )

    body         = await request.json()
    content      = (body.get("content") or "").strip()
    account_id   = (body.get("account_id") or "").strip()
    platform     = (body.get("platform") or "").strip().lower()
    scheduled_at = body.get("scheduled_at")   # ISO string or None
    media_urls   = body.get("media_urls") or []

    if not content:
        return JSONResponse({"error": "content required"}, status_code=400)
    if not account_id:
        return JSONResponse({"error": "account_id required"}, status_code=400)

    loc = _gcl()

    # ── Write audit records ────────────────────────────────────────────────────
    async with _async_session() as session:
        import uuid as _uuidmod
        from sqlalchemy.dialects.postgresql import UUID as _PGUUID

        loc_uuid = _uuidmod.UUID(loc)

        post_obj = _Post(
            location_id=loc_uuid,
            status="publishing",
            source="manual",
        )
        session.add(post_obj)
        await session.flush()  # get post_obj.id

        variant_obj = _PostVariant(
            post_id=post_obj.id,
            platform=platform or "unknown",
            caption=content,
            media_urls=media_urls or None,
            platform_specific={"ghl_account_id": account_id},
        )
        session.add(variant_obj)
        await session.flush()

        attempt_obj = _PostAttempt(
            post_id=post_obj.id,
            variant_id=variant_obj.id,
            platform=platform or "unknown",
            provider="ghl",
            status="queued",
        )
        session.add(attempt_obj)
        await session.commit()

        attempt_id_str = str(attempt_obj.id)

    _ghl_logger.info(
        "[publish.requested] attempt_id=%s platform=%s location=%s",
        attempt_id_str, platform, loc,
    )

    # ── Execute publish via GHLAdapter ─────────────────────────────────────────
    async with _async_session() as session:
        from sqlalchemy import update as _upd
        from db.models import PostAttempt as _PA

        _ghl_logger.info(
            "[publish.attempted] attempt_id=%s platform=%s",
            attempt_id_str, platform,
        )

        try:
            if scheduled_at:
                provider_post_id = await _ghl.schedule(
                    location_id=loc,
                    platform=platform,
                    caption=content,
                    account_id=account_id,
                    scheduled_at=scheduled_at,
                    media_urls=media_urls or None,
                )
            else:
                provider_post_id = await _ghl.publish(
                    location_id=loc,
                    platform=platform,
                    caption=content,
                    account_id=account_id,
                    media_urls=media_urls or None,
                )

            now = datetime.now(timezone.utc)
            await session.execute(
                _upd(_PA)
                .where(_PA.id == attempt_obj.id)
                .values(
                    status="published",
                    provider_post_id=provider_post_id,
                    attempt_count=1,
                    published_at=now,
                )
            )
            await session.commit()

            _ghl_logger.info(
                "[publish.completed] attempt_id=%s provider_post_id=%s",
                attempt_id_str, provider_post_id,
            )
            return JSONResponse({
                "ok":       True,
                "post_id":  provider_post_id,
                "status":   "scheduled" if scheduled_at else "published",
                "attempt_id": attempt_id_str,
            })

        except _PubErr as exc:
            await session.execute(
                _upd(_PA).where(_PA.id == attempt_obj.id)
                .values(status="failed", last_error=str(exc)[:500], attempt_count=1)
            )
            await session.commit()
            _ghl_logger.error("[publish.failed] attempt_id=%s error=%s", attempt_id_str, exc)
            return JSONResponse({"error": str(exc)}, status_code=400)

        except _AuthErr as exc:
            await session.execute(
                _upd(_PA).where(_PA.id == attempt_obj.id)
                .values(status="failed", last_error=str(exc)[:500], attempt_count=1)
            )
            await session.commit()
            _ghl_logger.error("[publish.failed] attempt_id=%s auth_error", attempt_id_str)
            return JSONResponse({"error": str(exc)}, status_code=401)

        except Exception as exc:
            await session.execute(
                _upd(_PA).where(_PA.id == attempt_obj.id)
                .values(status="failed", last_error=str(exc)[:500], attempt_count=1)
            )
            await session.commit()
            _ghl_logger.error("[publish.failed] attempt_id=%s error=%s", attempt_id_str, exc)
            return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/social/ghl/publish/multi")
async def ghl_publish_multi(request: Request):
    """
    Publish to multiple GHL platforms simultaneously with per-platform stagger offsets.

    Body: {
      "variants": [
        { "platform": "linkedin", "content": "...", "account_id": "...", "media_urls": [] },
        { "platform": "facebook", "content": "...", "account_id": "..." },
        ...
      ],
      "scheduled_at": "2026-05-26T09:00:00Z"  # optional base time
    }

    Returns: { "enqueued": [ { "platform", "attempt_id", "stagger_offset_s" } ] }

    Stagger offsets (SOW section 8 Layer 1):
      linkedin +0s | x +60s | facebook +120s | instagram +180s
      tiktok +240s | youtube +300s | gmb +360s

    Layer 3: deterministic jitter applied when scheduled_at falls on :00/:15/:30/:45.
    """
    import uuid as _uuidmod
    from datetime import datetime, timezone
    from config import get_current_location_id as _gcl
    from db.engine import async_session as _async_session
    from db.models import Post as _Post, PostVariant as _PostVariant, PostAttempt as _PostAttempt
    from workers.publish_jobs import stagger_offset_seconds as _stagger
    from sqlalchemy import update as _upd

    body     = await request.json()
    variants = body.get("variants") or []
    sched_at = body.get("scheduled_at")  # ISO or None

    if not variants:
        return JSONResponse({"error": "variants array required"}, status_code=400)

    loc = _gcl()
    loc_uuid = _uuidmod.UUID(loc)

    # Parse base scheduled_at if provided
    base_dt = None
    if sched_at:
        try:
            from datetime import datetime as _dt
            base_dt = _dt.fromisoformat(sched_at.replace("Z", "+00:00"))
        except ValueError:
            return JSONResponse({"error": "invalid scheduled_at format"}, status_code=400)

    enqueued = []

    async with _async_session() as session:
        # One Post record for the whole multi-platform publish
        post_obj = _Post(
            location_id=loc_uuid,
            status="publishing",
            source="manual",
            scheduled_at=base_dt,
        )
        session.add(post_obj)
        await session.flush()

        for v in variants:
            platform   = (v.get("platform") or "").strip().lower()
            content    = (v.get("content") or "").strip()
            account_id = (v.get("account_id") or "").strip()
            media_urls = v.get("media_urls") or []

            if not platform or not content or not account_id:
                continue

            offset_s = _stagger(platform, loc, base_dt)

            variant_obj = _PostVariant(
                post_id=post_obj.id,
                platform=platform,
                caption=content,
                media_urls=media_urls or None,
                platform_specific={"ghl_account_id": account_id, "stagger_offset_s": offset_s},
            )
            session.add(variant_obj)
            await session.flush()

            attempt_obj = _PostAttempt(
                post_id=post_obj.id,
                variant_id=variant_obj.id,
                platform=platform,
                provider="ghl",
                status="queued",
            )
            session.add(attempt_obj)
            await session.flush()

            enqueued.append({
                "platform":        platform,
                "attempt_id":      str(attempt_obj.id),
                "stagger_offset_s": offset_s,
            })

            _ghl_logger.info(
                "[publish.requested] attempt_id=%s platform=%s stagger_offset_s=%d location=%s",
                str(attempt_obj.id), platform, offset_s, loc,
            )

        await session.commit()

    # Enqueue arq jobs with stagger delay
    try:
        from arq import create_pool
        from workers.publish_worker import WorkerSettings as _WS
        redis = await create_pool(settings.redis_url if hasattr(settings, "redis_url") else None or _WS.redis_settings)
        for item in enqueued:
            await redis.enqueue_job(
                "publish_variant",
                item["attempt_id"],
                _defer_by=item["stagger_offset_s"],
            )
        await redis.close()
    except Exception as exc:
        _ghl_logger.warning("Failed to enqueue arq jobs: %s — variants queued in DB only", exc)

    return JSONResponse({"enqueued": enqueued, "post_id": str(post_obj.id) if enqueued else None})


# ── End Social Publishing ──────────────────────────────────────────────────────

@app.post("/api/yt/competitor-spy")
async def start_competitor_spy(request: Request):
    """Start a background Competitor Spy analysis. Returns job_id immediately."""
    yt_api_key = os.environ.get("YOUTUBE_DATA_API_KEY", "")
    if not yt_api_key:
        return JSONResponse(
            {"error": "YOUTUBE_DATA_API_KEY is not configured. Add it to your .env file."},
            status_code=500,
        )

    body     = await request.json()
    city     = (body.get("city") or "").strip()
    audience = (body.get("audience") or "Relocation Buyers").strip()
    channels = (body.get("channels") or "").strip()

    if not city:
        return JSONResponse({"error": "city is required"}, status_code=400)

    job_id = str(uuid.uuid4())
    # Pre-populate step_statuses so the very first poll has correct shape
    _work_steps = [s for s in _YT_STEPS if s != "complete"]
    yt_spy_jobs[job_id] = {
        "status":         "running",
        "step":           "scanning_market",
        "steps_complete": [],
        "step_statuses":  {k: {"status": "pending", "error": None} for k in _work_steps},
        "result":         None,
        "error":          None,
        "city":           city,
        "audience":       audience,
    }

    asyncio.create_task(
        _run_competitor_spy(job_id, city, audience, channels, yt_api_key)
    )

    return JSONResponse({"job_id": job_id, "status": "running"})


@app.get("/api/yt/competitor-spy/{job_id}")
async def get_competitor_spy_status(job_id: str):
    """Poll for the status and results of a Competitor Spy job."""
    job = yt_spy_jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return JSONResponse(job)


@app.post("/api/yt/scout-remix")
async def scout_remix(request: Request):
    """Rewrite a Scout video concept in the user's own voice via Foundation.

    The ONLY LLM call in Scout. All virality numbers are deterministic math
    from the YouTube Data API — no LLM-generated numbers ever enter this route.

    Request:  { "title": "...", "channel": "...", "views": 123456, "score": 4.2,
                "popular": true, "market": "Springfield MO" }
    Response: { "script": "...", "hook": "...", "cta": "..." }
    Error 422: { "error": "...", "foundation_not_ready": true }
    """
    import anthropic as _anthropic
    from db.engine import async_session as _async_session
    from config import get_current_location_id as _get_loc, settings as _settings
    from services.foundation import (
        assert_foundation_ready as _assert_ready,
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandContextError,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType

    body    = await request.json()
    title   = (body.get("title") or "").strip()
    channel = (body.get("channel") or "").strip()
    views   = body.get("views", 0)
    score   = body.get("score")
    popular = body.get("popular", False)
    market  = (body.get("market") or "").strip()

    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)

    location_id = _get_loc()
    async with _async_session() as session:
        try:
            await _assert_ready(session=session, location_id=location_id)
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc), "foundation_not_ready": True}, status_code=422)

        try:
            ctx = await _get_brand_ctx(
                session=session,
                location_id=location_id,
                task_type=_TaskType.scout_remix_script,
                topic=title,
                platform=None,
                audience=None,
            )
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    # Build context block from real API numbers (no LLM-generated figures)
    perf_notes = []
    if views:
        perf_notes.append(f"Views: {int(views):,}")
    if score is not None:
        perf_notes.append(f"Virality score: {score:.1f}x (views ÷ channel subs)")
    if popular:
        perf_notes.append("Marked POPULAR — over-performed vs channel average")
    perf_line = " | ".join(perf_notes) if perf_notes else ""

    bp  = ctx.brand_profile
    vp  = ctx.voice_profile
    vocab = ctx.vocabulary

    tone_block  = ", ".join(vp.tones[:3]) if vp.tones else "direct and conversational"
    niche_block = bp.niche_primary or "real estate"
    market_line = f" in {market}" if market else ""

    samples_block = ""
    if ctx.voice_samples:
        excerpts = [s.text[:120] for s in ctx.voice_samples[:3]]
        samples_block = "\n\nVoice samples from my actual content:\n" + "\n---\n".join(excerpts)

    sys_prompt = (
        f"You write video concepts in my voice. My tone: {tone_block}. "
        f"My niche: {niche_block}{market_line}. "
        f"I never sound corporate or generic — I'm direct, relatable, no fluff."
        f"{samples_block}"
    )

    user_prompt = (
        f"I found a competitor video I want to remix in my own voice.\n\n"
        f"Original video: \"{title}\" by {channel or 'a competitor'}\n"
        f"{f'Performance: {perf_line}' if perf_line else ''}\n\n"
        f"Rewrite this concept for my market{market_line}. Give me:\n"
        f"1. HOOK: A punchy opening line (under 15 words) that hooks my specific audience\n"
        f"2. CONCEPT: A 2-3 sentence pitch for this video idea in my voice\n"
        f"3. ANGLE: What makes my take different from the competitor\n"
        f"4. CTA: One strong call-to-action line\n\n"
        f"Return JSON with keys: hook, concept, angle, cta"
    )

    try:
        ai = _anthropic.AsyncAnthropic(api_key=_settings.anthropic_api_key)
        msg = await ai.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            system=sys_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        import json as _json
        result = _json.loads(raw)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/yt/script")
async def generate_yt_script(request: Request):
    """Generate a full YouTube video script with Hook, Early CTA, Main Content, End CTA."""
    body     = await request.json()
    topic    = (body.get("topic") or "").strip()
    city     = (body.get("city") or "").strip()
    audience = (body.get("audience") or "Relocation Buyers").strip()
    angle    = (body.get("angle") or "").strip()

    if not topic or not city:
        return JSONResponse({"error": "topic and city are required"}, status_code=400)

    try:
        from pipeline.content import _get_client
        client = _get_client()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    angle_line = f" Your unique angle: {angle}." if angle else ""
    prompt = (
        f"You're a YouTube strategist helping real estate agents generate inbound buyer and "
        f"seller leads. Generate the full video script for: '{topic}' targeting {audience} "
        f"in {city}.{angle_line}\n\n"
        "Include exactly these 4 sections:\n"
        "1) HOOK - stop the viewer, grab attention, give them a reason to watch (2-3 sentences, "
        "must create curiosity or fear of missing out)\n"
        "2) EARLY CTA - in the first 30 seconds, natural invitation to reach out (mention "
        "contacting via description)\n"
        "3) MAIN CONTENT - educational, practical, local expertise, addresses common questions/"
        "concerns/misconceptions specific to the city (5-7 sections with headers)\n"
        "4) END SCREEN CTA - drive to next video, keep them on channel.\n\n"
        "Write naturally, conversationally, like a local expert. No fluff.\n\n"
        "Return ONLY a valid JSON object with keys: hook, early_cta, main_content, end_cta. "
        "Values are plain text strings (no markdown inside the values)."
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        data = json.loads(completion.choices[0].message.content)
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/yt/seo-package")
async def generate_seo_package(request: Request):
    """Generate YouTube SEO package: 3 titles, 3 thumbnail concepts, description, tags."""
    body   = await request.json()
    script = (body.get("script") or "").strip()
    topic  = (body.get("topic") or "").strip()
    city   = (body.get("city") or "").strip()

    if not script or not topic:
        return JSONResponse({"error": "script and topic are required"}, status_code=400)

    try:
        from pipeline.content import _get_client
        client = _get_client()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    prompt = (
        f"Using this video script for '{topic}' in {city}, generate the full YouTube SEO package.\n\n"
        f"Script:\n{script[:3000]}\n\n"
        "Return ONLY a valid JSON object with this exact structure:\n"
        '{"titles": [{"title": "...", "reason": "...", "recommended": true}, ...], '
        '"thumbnail_concepts": [{"concept": "...", "text_overlay": "...", "background": "...", "emotion": "..."}, ...], '
        '"description": "...", "tags": ["...", ...]}\n\n'
        "Rules:\n"
        "- 3 title options, first one has recommended: true, others false\n"
        "- Titles under 70 characters, front-load the keyword\n"
        "- 3 thumbnail concepts with bold, visual descriptions\n"
        "- Description: 150-200 words, keyword-rich, includes a CTA paragraph\n"
        "- Tags: 10-15 tags mixing broad + specific, single string each (no #)"
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.6,
        )
        data = json.loads(completion.choices[0].message.content)
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/yt/content-calendar")
async def generate_content_calendar(request: Request):
    """Generate an 8-video content calendar across 5 pillars."""
    body               = await request.json()
    city               = (body.get("city") or "").strip()
    audience           = (body.get("audience") or "General Real Estate").strip()
    competitor_insights = body.get("competitor_insights") or {}

    if not city:
        return JSONResponse({"error": "city is required"}, status_code=400)

    try:
        from pipeline.content import _get_client
        client = _get_client()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    context = f"\nCompetitor insights: {json.dumps(competitor_insights)}" if competitor_insights else ""
    prompt = (
        f"Create an 8-video Trend Radar for a real estate agent in {city} "
        f"targeting {audience}.{context}\n\n"
        "The ideas should feel like topics before they peak: hyper-local, search-friendly, "
        "timely for 2026, and specific enough to attract buyer/seller leads without chasing broad virality.\n\n"
        "Distribute across these 5 pillars: Relocation, Market Updates, Neighborhood Deep Dive, Home Tour, Lifestyle & Community.\n\n"
        "For each idea include:\n"
        "- week: 1-8\n"
        "- title: clickable YouTube title with the local keyword near the front\n"
        "- pillar: one of the five pillar names above\n"
        "- why: why this can trend or produce leads in this market\n"
        "- hook: a first-30-seconds curiosity hook angle\n"
        "- search_intent: what the viewer is trying to decide\n\n"
        "Return ONLY valid JSON:\n"
        '{"calendar": [{"week": 1, "title": "...", "pillar": "Relocation", "why": "...", "hook": "...", "search_intent": "..."}, ...]}'
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        data = json.loads(completion.choices[0].message.content)
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Competitor Spy background task ────────────────────────────────────────────

async def _yt_get(client: "_httpx.AsyncClient", url: str) -> dict:
    """GET a YouTube Data API URL, return parsed JSON dict (empty on error)."""
    try:
        r = await client.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def _start_step(job_id: str, step_key: str):
    """Mark a step as actively running."""
    job = yt_spy_jobs.get(job_id)
    if not job:
        return
    job["step"] = step_key
    # Set status dict entry to 'running'
    if "step_statuses" not in job:
        job["step_statuses"] = {}
    job["step_statuses"][step_key] = {"status": "running", "error": None}
    import time as _time
    print(f"[SPY {job_id[:8]}] START {step_key} @ {_time.strftime('%H:%M:%S')}")


def _mark_step(job_id: str, step_key: str, error=None):
    """Mark a step completed (or failed). Advance job['step'] to the next."""
    import time as _time
    job = yt_spy_jobs.get(job_id)
    if not job:
        return
    if "step_statuses" not in job:
        job["step_statuses"] = {}
    status = "failed" if error else "completed"
    job["step_statuses"][step_key] = {"status": status, "error": error}
    if step_key not in job["steps_complete"]:
        job["steps_complete"].append(step_key)
    # Advance current step pointer to next
    work_steps = [s for s in _YT_STEPS if s != "complete"]
    idx = work_steps.index(step_key) if step_key in work_steps else -1
    if idx >= 0 and idx + 1 < len(work_steps):
        job["step"] = work_steps[idx + 1]
    print(f"[SPY {job_id[:8]}] {'FAIL' if error else 'DONE'} {step_key} @ {_time.strftime('%H:%M:%S')}"
          + (f" — {error}" if error else ""))


async def _run_competitor_spy(
    job_id: str,
    city: str,
    audience: str,
    channels_raw: str,
    yt_api_key: str,
):
    """Run the full 7-step Competitor Spy analysis as an asyncio background task."""
    job = yt_spy_jobs.get(job_id)
    if not job:
        return

    BASE = "https://www.googleapis.com/youtube/v3"

    from urllib.parse import quote_plus
    import re as _re

    # ── Helpers (defined once, used across steps) ─────────────────────────────
    def _thumb(video_id: str, snippet: dict) -> str:
        thumbs  = snippet.get("thumbnails", {})
        api_url = (
            thumbs.get("maxres", {}).get("url")
            or thumbs.get("high",   {}).get("url")
            or thumbs.get("medium", {}).get("url")
            or thumbs.get("default",{}).get("url")
            or ""
        )
        return api_url or (f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else "")

    def _dur(iso: str) -> str:
        m = _re.search(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
        if not m:
            return ""
        h, mins, s = (int(x or 0) for x in m.groups())
        total = h * 60 + mins
        return f"{total}:{s:02d}" if not h else f"{h}:{mins:02d}:{s:02d}"

    gathered: dict = {
        "city": city, "audience": audience,
        "top_videos": [], "channels": [], "viral_videos": [],
        "outliers": [], "categories": {}, "hot_queries": [],
        "top_videos_ranked": [],
    }
    channel_list: list[dict] = []
    video_items:  list[dict] = []
    categories:   dict       = {}
    hot_data:     list[dict] = []

    async with _httpx.AsyncClient(headers={"Referer": YT_API_REFERER}, timeout=30) as client:

        # ── Step 1: Scan YouTube Market ───────────────────────────────────────
        _start_step(job_id, "scanning_market")
        try:
            queries = [
                f"moving to {city}", f"{city} real estate",
                f"living in {city}", f"{city} neighborhood guide",
            ]
            for q in queries:
                params = f"part=snippet&q={quote_plus(q)}&type=video&maxResults=10&key={yt_api_key}"
                data   = await _yt_get(client, f"{BASE}/search?{params}")
                video_items.extend(data.get("items", []))
            gathered["top_videos"] = video_items[:20]
            _mark_step(job_id, "scanning_market")
        except Exception as exc:
            _mark_step(job_id, "scanning_market", error=str(exc))

        # ── Step 2: Identify Competitors ──────────────────────────────────────
        _start_step(job_id, "identifying_competitors")
        try:
            channel_ids: set[str] = set()
            if channels_raw:
                for line in channels_raw.splitlines():
                    line   = line.strip()
                    handle = line.split("@")[-1].split("/")[0] if "@" in line else None
                    if handle:
                        params = f"part=snippet&q={quote_plus(handle)}&type=channel&maxResults=1&key={yt_api_key}"
                        d = await _yt_get(client, f"{BASE}/search?{params}")
                        for item in d.get("items", []):
                            cid = item.get("snippet", {}).get("channelId") or item.get("id", {}).get("channelId")
                            if cid:
                                channel_ids.add(cid)
            for v in video_items:
                cid = v.get("snippet", {}).get("channelId")
                if cid:
                    channel_ids.add(cid)
            if channel_ids:
                ids_str = ",".join(list(channel_ids)[:15])
                params  = f"part=statistics,snippet&id={ids_str}&key={yt_api_key}"
                data    = await _yt_get(client, f"{BASE}/channels?{params}")
                channel_list = data.get("items", [])
            gathered["channels"] = channel_list
            _mark_step(job_id, "identifying_competitors")
        except Exception as exc:
            _mark_step(job_id, "identifying_competitors", error=str(exc))

        # ── Step 3: Analyze Viral Content ─────────────────────────────────────
        _start_step(job_id, "analyzing_viral")
        try:
            viral_search: list[dict] = []
            top_channels = sorted(
                channel_list,
                key=lambda c: int(c.get("statistics", {}).get("subscriberCount", 0)),
                reverse=True,
            )[:6]
            for ch in top_channels:
                cid = ch.get("id", "")
                if not cid:
                    continue
                params = f"part=snippet&channelId={cid}&order=viewCount&maxResults=20&type=video&key={yt_api_key}"
                data   = await _yt_get(client, f"{BASE}/search?{params}")
                viral_search.extend(data.get("items", []))
            if viral_search:
                vid_ids = ",".join(
                    [v.get("id", {}).get("videoId", "") for v in viral_search
                     if v.get("id", {}).get("videoId")][:50]
                )
                if vid_ids:
                    params     = f"part=statistics,snippet,contentDetails&id={vid_ids}&key={yt_api_key}"
                    stats_data = await _yt_get(client, f"{BASE}/videos?{params}")
                    gathered["viral_videos"] = stats_data.get("items", [])
            _mark_step(job_id, "analyzing_viral")
        except Exception as exc:
            _mark_step(job_id, "analyzing_viral", error=str(exc))

        # ── Step 4: Find Outlier Videos ───────────────────────────────────────
        _start_step(job_id, "finding_outliers")
        try:
            outliers: list[dict] = []
            for vid in gathered["viral_videos"]:
                stats      = vid.get("statistics", {})
                view_count = int(stats.get("viewCount", 0))
                snippet    = vid.get("snippet", {})
                channel_id = snippet.get("channelId", "")
                video_id   = vid.get("id", "")
                thumbnail  = _thumb(video_id, snippet)
                subs = 0
                for ch in channel_list:
                    if ch.get("id") == channel_id:
                        subs = int(ch.get("statistics", {}).get("subscriberCount", 0))
                        break
                row = {
                    "title":        snippet.get("title", ""),
                    "channel":      snippet.get("channelTitle", ""),
                    "views":        view_count,
                    "subs":         subs,
                    "likes":        int(stats.get("likeCount", 0)),
                    "comments":     int(stats.get("commentCount", 0)),
                    "video_id":     video_id,
                    "thumbnail":    thumbnail,
                    "url":          f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
                    "published_at": snippet.get("publishedAt", ""),
                    "duration":     _dur(vid.get("contentDetails", {}).get("duration", "")),
                }
                if view_count > subs > 0:
                    outliers.append(row)
            gathered["outliers"] = outliers[:20]

            all_sorted = sorted(
                gathered["viral_videos"],
                key=lambda v: int(v.get("statistics", {}).get("viewCount", 0)),
                reverse=True,
            )
            top_videos_list: list[dict] = []
            for vid in all_sorted[:50]:
                stats      = vid.get("statistics", {})
                snippet    = vid.get("snippet", {})
                video_id   = vid.get("id", "")
                thumbnail  = _thumb(video_id, snippet)
                channel_id = snippet.get("channelId", "")
                subs = 0
                for ch in channel_list:
                    if ch.get("id") == channel_id:
                        subs = int(ch.get("statistics", {}).get("subscriberCount", 0))
                        break
                views = int(stats.get("viewCount", 0))
                # Resolve channel thumbnail from channel_list lookup
                ch_thumb = next(
                    (ch.get("snippet", {}).get("thumbnails", {}).get("medium", {}).get("url", "")
                     for ch in channel_list if ch.get("id") == channel_id),
                    ""
                )
                top_videos_list.append({
                    # ── CompetitorCard contract ──────────────────────────────
                    # Video identity
                    "video_id":          video_id,
                    "title":             snippet.get("title", ""),
                    "thumbnail":         thumbnail,
                    "url":               f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
                    # Video metrics
                    "views":             views,
                    "likes":             int(stats.get("likeCount", 0)),
                    "comments":          int(stats.get("commentCount", 0)),
                    "published_at":      snippet.get("publishedAt", ""),
                    "duration":          _dur(vid.get("contentDetails", {}).get("duration", "")),
                    # Channel context
                    "channel_id":        channel_id,
                    "channel":           snippet.get("channelTitle", ""),   # kept for back-compat
                    "channel_thumbnail": ch_thumb,
                    "channel_url":       f"https://www.youtube.com/channel/{channel_id}" if channel_id else "",
                    "subs":              subs,
                    # Ranking / virality (deterministic math — zero LLM)
                    "viral_multiplier":  round(views / max(subs, 1), 1) if subs > 0 else None,
                    "score":             round(views / max(subs, 1), 2) if subs > 0 else None,
                    "popular":           (views / max(subs, 1)) >= VIRALITY_POPULAR_THRESHOLD if subs > 0 else False,
                })
            gathered["top_videos_ranked"] = top_videos_list
            _mark_step(job_id, "finding_outliers")
        except Exception as exc:
            _mark_step(job_id, "finding_outliers", error=str(exc))

        # ── Step 5: Map Market Standards ──────────────────────────────────────
        _start_step(job_id, "mapping_standards")
        try:
            categories = {"relocation": 0, "market_update": 0, "neighborhood": 0, "home_tour": 0, "lifestyle": 0}
            relocation_kw   = ["moving", "relocat", "living in", "move to"]
            market_kw       = ["market", "price", "rate", "forecast", "update", "trend"]
            neighborhood_kw = ["neighborhood", "area", "district", "suburb", "best place"]
            home_tour_kw    = ["home tour", "house tour", "inside", "walkthrough", "listing"]
            for vid in gathered["viral_videos"]:
                title = (vid.get("snippet", {}).get("title") or "").lower()
                if   any(k in title for k in relocation_kw):   categories["relocation"]    += 1
                elif any(k in title for k in market_kw):       categories["market_update"] += 1
                elif any(k in title for k in neighborhood_kw): categories["neighborhood"]  += 1
                elif any(k in title for k in home_tour_kw):    categories["home_tour"]     += 1
                else:                                          categories["lifestyle"]     += 1
            gathered["categories"] = categories
            _mark_step(job_id, "mapping_standards")
        except Exception as exc:
            _mark_step(job_id, "mapping_standards", error=str(exc))

        # ── Step 6: Discover Hot Searches ─────────────────────────────────────
        _start_step(job_id, "discovering_searches")
        try:
            hot_queries = [
                f"{city} real estate 2025", f"best neighborhoods {city}",
                f"cost of living {city}", f"moving to {city} pros cons", f"{city} housing market",
            ]
            for q in hot_queries[:3]:
                params = f"part=snippet&q={quote_plus(q)}&type=video&maxResults=5&key={yt_api_key}"
                d      = await _yt_get(client, f"{BASE}/search?{params}")
                for item in d.get("items", []):
                    hot_data.append({"query": q, "title": item.get("snippet", {}).get("title", "")})
            gathered["hot_queries"] = hot_data
            _mark_step(job_id, "discovering_searches")
        except Exception as exc:
            _mark_step(job_id, "discovering_searches", error=str(exc))

        # ── Step 7: Compile Intelligence (AI) ─────────────────────────────────
        _start_step(job_id, "compiling_intelligence")
        try:
            from pipeline.content import _get_client
            ai_client = _get_client()
            summary = {
                "city": city, "audience": audience,
                "channels": [
                    {"name": c.get("snippet", {}).get("title"),
                     "subs": c.get("statistics", {}).get("subscriberCount"),
                     "videos": c.get("statistics", {}).get("videoCount")}
                    for c in channel_list[:8]
                ],
                "top_video_titles": [v.get("snippet", {}).get("title") for v in gathered["viral_videos"][:15]],
                "outliers":   gathered["outliers"][:5],
                "categories": categories,
                "hot_queries": [h["query"] for h in hot_data],
            }
            ai_prompt = (
                f"Analyze this YouTube market data for {city}, targeting {audience}.\n"
                f"Data: {json.dumps(summary)}\n\n"
                "Return a JSON object with EXACTLY this structure:\n"
                '{\n'
                '  "market_demand": {"trend": "Trending Up|Stable|Declining", "bullets": ["...", "..."]},\n'
                '  "best_format": {"bullets": ["...", "..."]},\n'
                '  "opportunity_gap": {"bullets": ["...", "..."]},\n'
                '  "first_video": {"title": "...", "reasoning": "..."},\n'
                '  "content_ideas": [\n'
                '    {"rank": 1, "title": "...", "view_range": "5,000-15,000", "upload_timing": "...", '
                '"why_it_works": "...", "your_angle": "...", "pillar": "relocation|market_update|neighborhood|home_tour|lifestyle"}\n'
                '  ],\n'
                '  "viral_outliers": [{"title": "...", "channel": "...", "views": 0, "subs": 0, "video_id": "", "thumbnail": "", "url": ""}],\n'
                '  "market_standards": [\n'
                '    {"format_name": "...", "competition": "High|Medium|Low", "view_range": "...", '
                '"posting_schedule": "...", "why_it_works": "...", "your_angle": "..."}\n'
                '  ],\n'
                '  "hot_searches": [\n'
                '    {"keyword": "...", "trend": "Rising|Stable", "trend_percentage": "15", '
                '"audience": "...", "video_idea": "..."}\n'
                '  ],\n'
                '  "channels": [\n'
                '    {"channel_name": "...", "subscriber_count": "...", "avg_views": "...", "top_format": "..."}\n'
                '  ]\n'
                "}\n\nGenerate 6-8 content_ideas, 3-5 market_standards, 5-6 hot_searches. Make titles punchy and clickable."
            )
            completion = ai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": ai_prompt}],
                response_format={"type": "json_object"},
                temperature=0.7,
            )
            result = json.loads(completion.choices[0].message.content)

            if channel_list:
                result["channels"] = [
                    {
                        # ── ChannelCard contract ─────────────────────────────
                        "channel_id":       c.get("id", ""),
                        "channel_name":     c.get("snippet", {}).get("title", "Unknown"),
                        "channel_thumbnail": c.get("snippet", {}).get("thumbnails", {}).get("medium", {}).get("url", ""),
                        "channel_url":      f"https://www.youtube.com/channel/{c.get('id', '')}",
                        "subscriber_count": f"{int(c.get('statistics', {}).get('subscriberCount', 0)):,}",
                        "subs":             int(c.get("statistics", {}).get("subscriberCount", 0)),
                        "avg_views":        f"{int(c.get('statistics', {}).get('viewCount', 0)) // max(int(c.get('statistics', {}).get('videoCount', 1)), 1):,}" if int(c.get('statistics', {}).get('videoCount', 0)) > 0 else "N/A",
                        "top_format":       "Real Estate",
                    }
                    for c in sorted(channel_list, key=lambda x: int(x.get("statistics", {}).get("subscriberCount", 0)), reverse=True)[:10]
                ]
            if gathered.get("outliers"):
                result["viral_outliers"] = [
                    {
                        "title":            o["title"],
                        "channel_name":     o["channel"],
                        "subscriber_count": f"{o['subs']:,}",
                        "viral_multiplier": f"{o['views'] / max(o['subs'], 1):.1f}",
                        "views":            o["views"],
                        "subs":             o["subs"],
                        "video_id":         o.get("video_id", ""),
                        "thumbnail":        o.get("thumbnail", ""),
                        "url":              o.get("url", ""),
                    }
                    for o in gathered["outliers"][:20]
                ]
            if gathered.get("top_videos_ranked"):
                result["top_videos_ranked"] = gathered["top_videos_ranked"]

            result["step_statuses"] = job.get("step_statuses", {})
            _mark_step(job_id, "compiling_intelligence")
            job["status"] = "complete"
            job["result"] = result

        except Exception as exc:
            _mark_step(job_id, "compiling_intelligence", error=str(exc))
            # Still mark complete so the UI exits the poll loop
            job["status"] = "complete"
            job["result"] = {
                "step_statuses":  job.get("step_statuses", {}),
                "top_videos_ranked": gathered.get("top_videos_ranked", []),
                "viral_outliers": gathered.get("outliers", []),
                "channels":       gathered.get("channels", []),
                "market_demand":  {"trend": "Unknown", "bullets": ["AI analysis failed — showing raw data only"]},
                "best_format":    {"bullets": []},
                "opportunity_gap":{"bullets": []},
                "first_video":    {"title": "", "reasoning": ""},
                "content_ideas":  [],
                "market_standards": [],
                "hot_searches":   [],
            }


# ── YouTube Studio — Adapt Concept ───────────────────────────────────────────

@app.post("/api/yt/adapt-concept")
async def yt_adapt_concept(req: Request):
    """Adapt a competitor concept for the agent's own market and audience."""
    import openai as _openai
    body     = await req.json()
    concept  = body.get("original_concept", "")
    market   = body.get("market", "")
    audience = body.get("audience", "Relocation Buyers")
    angle    = body.get("angle", "")

    client = _openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = (
        f"You are a YouTube content strategist for real estate agents.\n"
        f"A real estate agent in {market} wants to adapt this concept: \"{concept}\"\n"
        f"Target audience: {audience}\n"
        f"Their unique angle: {angle or 'Empathy-first, local expert'}\n\n"
        "Return JSON with:\n"
        "- adapted_title (under 70 chars, front-load the local keyword)\n"
        "- hook (2-3 compelling sentences that stop the viewer)\n"
        "- outline (list of 5 content section titles)\n\n"
        "Return ONLY valid JSON."
    )
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        return JSONResponse(json.loads(resp.choices[0].message.content))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── YouTube Studio — Video Advisor ───────────────────────────────────────────

@app.post("/api/yt/video-advisor")
async def yt_video_advisor(req: Request):
    """Score a YouTube video on Hook, Pacing, SEO, and Thumbnail quality."""
    import openai as _openai
    import re as _re
    body = await req.json()
    url  = body.get("url", "")

    vid_match = _re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', url)
    if not vid_match:
        return JSONResponse({"error": "Invalid YouTube URL — must contain a video ID"}, status_code=400)

    video_id = vid_match.group(1)
    yt_key   = os.getenv("YOUTUBE_DATA_API_KEY", "")
    meta: dict = {}

    if yt_key:
        # Fetch video metadata via the YouTube Data API (httpx already available)
        try:
            async with _httpx.AsyncClient(headers={"Referer": YT_API_REFERER}, timeout=10) as hx:
                r = await hx.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={"part": "snippet,statistics", "id": video_id, "key": yt_key},
                )
                r.raise_for_status()
                data = r.json()
                items = data.get("items", [])
                if items:
                    meta = items[0]
        except Exception:
            pass  # proceed without metadata — AI will score based on URL alone

    title       = meta.get("snippet", {}).get("title", url)
    description = meta.get("snippet", {}).get("description", "")[:400]
    tags        = meta.get("snippet", {}).get("tags", [])
    views       = meta.get("statistics", {}).get("viewCount", "unknown")

    client = _openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = (
        "Analyze this YouTube video and score each dimension 0-100 for a real estate content creator.\n\n"
        f"Title: {title}\n"
        f"Description: {description}\n"
        f"Tags: {', '.join(tags[:10])}\n"
        f"Views: {views}\n\n"
        "Return JSON with:\n"
        "- hook_score (0-100)\n"
        "- pacing_score (0-100)\n"
        "- seo_score (0-100)\n"
        "- thumbnail_score (0-100)\n"
        "- tips (object with keys hook, pacing, seo, thumbnail — each a 1-2 sentence actionable tip)\n"
        "- overall_verdict (one clear sentence)\n\n"
        "Return ONLY valid JSON."
    )
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        return JSONResponse(json.loads(resp.choices[0].message.content))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── YouTube Studio — Content Scheduler ───────────────────────────────────────

@app.post("/api/yt/scheduler/save")
async def yt_scheduler_save(req: Request):
    """Persist the shoot schedule to data/scheduler.json."""
    body          = await req.json()
    schedule_file = DATA_DIR / "scheduler.json"
    schedule_file.write_text(json.dumps(body, indent=2))
    return JSONResponse({"ok": True})


@app.get("/api/yt/scheduler")
async def yt_scheduler_get():
    """Return the saved shoot schedule (empty list if not yet set)."""
    schedule_file = DATA_DIR / "scheduler.json"
    if not schedule_file.exists():
        return JSONResponse({"schedule": []})
    return JSONResponse(json.loads(schedule_file.read_text()))


# ── YouTube Studio — Script Formula ──────────────────────────────────────────

@app.post("/api/yt/script-formula")
async def yt_script_formula(req: Request):
    """
    Generate a 4-part proven YouTube script formula:
    hook, early CTA, full teleprompter script, body outline, end screen + next video ideas.
    """
    import json as _json
    import re as _re
    import anthropic as _anthropic
    from db.engine import async_session as _async_session
    from config import get_current_location_id as _get_loc, settings as _settings
    from services.foundation import (
        assert_foundation_ready as _assert_ready,
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandContextError,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType

    body        = await req.json()
    topic       = body.get("topic", "")
    city        = body.get("city", "")
    pillar      = body.get("pillar", "Relocation")
    audience    = body.get("audience", "Relocation Buyers")
    agent_name  = body.get("agent_name", "")
    contact_cta = body.get("contact_cta", "reach out to me")
    angle       = body.get("angle", "")

    # ── Foundation gate ──────────────────────────────────────────────────────
    location_id = _get_loc()
    async with _async_session() as session:
        try:
            await _assert_ready(session=session, location_id=location_id)
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc), "foundation_not_ready": True}, status_code=422)
        try:
            ctx = await _get_brand_ctx(
                session=session,
                location_id=location_id,
                task_type=_TaskType.podcast_script_outline,
                topic=topic or None,
                platform=None,
                audience=None,
            )
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    # ── Foundation voice preamble ────────────────────────────────────────────
    bp = ctx.brand_profile
    vp = ctx.voice_profile
    vocab = ctx.vocabulary
    tone_str = ", ".join(vp.tone if isinstance(vp.tone, list) else [vp.tone]) if vp.tone else "direct, genuine"
    cadence_str = vp.cadence or "natural, punchy"
    vocab_yes = "Use naturally: " + ", ".join(vocab.use if isinstance(vocab.use, list) else [vocab.use]) if vocab.use else ""
    vocab_no  = "Avoid entirely: " + ", ".join(vocab.avoid if isinstance(vocab.avoid, list) else [vocab.avoid]) if vocab.avoid else ""
    voice_examples = ""
    if ctx.voice_samples:
        examples = [f'  — "{s.text[:300]}"' for s in ctx.voice_samples[:3]]
        voice_examples = "Voice samples (match this style):\n" + "\n".join(examples)
    voice_preamble = (
        f"VOICE PROFILE:\n- Tone: {tone_str}\n- Cadence: {cadence_str}\n- POV: {vp.pov or 'first-person'}\n"
    )
    if vocab_yes:      voice_preamble += f"- {vocab_yes}\n"
    if vocab_no:       voice_preamble += f"- {vocab_no}\n"
    if voice_examples: voice_preamble += f"\n{voice_examples}\n"

    # Pillar-specific body structure guidance
    pillar_structures = {
        "Relocation": (
            "1. Cost of living overview\n"
            "2. Best neighborhoods for their lifestyle\n"
            "3. Job market & economy\n"
            "4. Schools & family life\n"
            "5. What they WON'T find on Google (insider tip)\n"
            "6. Pros & cons summary\n"
            "7. What to do next"
        ),
        "Market Updates": (
            "1. Current market stats (median price, days on market, inventory)\n"
            "2. What the numbers actually mean (plain English)\n"
            "3. What it means for buyers right now\n"
            "4. What it means for sellers right now\n"
            "5. My prediction for the next 90 days\n"
            "6. The one move buyers or sellers should make right now given these conditions"
        ),
        "Neighborhood Deep Dive": (
            "1. Quick neighborhood overview & vibe\n"
            "2. What makes this area unique vs others in the city\n"
            "3. Price range & what you get for your money\n"
            "4. Top 3 things to love\n"
            "5. Top 2 honest cons — don't soften these; viewers trust you more when you name real drawbacks without spin\n"
            "6. Who this neighborhood is PERFECT for\n"
            "7. How to get more info"
        ),
        "Home Tour": (
            "1. Property intro — location, price, quick stats\n"
            "2. Curb appeal & first impression walk\n"
            "3. Main living areas tour\n"
            "4. Kitchen & bathrooms highlight\n"
            "5. Bedrooms & storage\n"
            "6. Backyard / outdoor space\n"
            "7. Honest pros & cons\n"
            "8. Who this home is perfect for + contact info"
        ),
        "Lifestyle & Community": (
            "1. What makes this city/area special\n"
            "2. Best local spots (restaurants, parks, things to do)\n"
            "3. Community feel & events\n"
            "4. Hidden gems most people don't know\n"
            "5. What daily life actually looks like here\n"
            "6. Connect with me if you want to explore"
        ),
    }

    body_structure = pillar_structures.get(pillar, pillar_structures["Relocation"])

    system_prompt = voice_preamble + "\n" + (
        "You are an expert YouTube content strategist for real estate agents who generate leads through video.\n\n"
        "YOUTUBE ALGORITHM PRINCIPLES (apply these to every section you write):\n"
        "- The hook (first 30 seconds) determines 80% of watch time. Weak hooks = dead videos.\n"
        "- A CTA placed IMMEDIATELY after the hook (when ~70% of viewers are still watching) converts "
        "2.5x more leads than a CTA at the end of the video.\n"
        "- The end screen must tease the NEXT video to keep viewers on the channel.\n"
        "- Curiosity gap openings (tease what they'll learn + hint at a painful consequence of NOT knowing) "
        "dramatically improve retention.\n\n"
        "PROVEN EARLY CTA TEMPLATE:\n"
        "'By the way, if you're thinking about [action] in [city], I have people just like you reaching out "
        "to me every single week. I'd love to have a private conversation with you to see how I can help "
        "you [benefit]. Use the contact CTA provided for this video to close this section.'\n\n"
        "HOOK FORMULA (three beats, in order):\n"
        "1. STATEMENT — Open with a specific number: 'There are X things about [topic] that most [audience] never get told.'\n"
        "2. CURIOSITY GAP — Before revealing anything, drop the gap: 'But I'm also going to share the #1 thing "
        "most [audience] get completely wrong — and if you miss this one, [painful consequence].'\n"
        "3. PROMISE — Seal the contract: 'By the end of this video, you'll know exactly [specific outcome] "
        "so you can [action they can now take].'\n"
        "Do not merge these three beats or skip the promise — it's what makes the viewer decide to stay.\n\n"
        "END SCREEN FORMULA (four beats, in order — do not merge them):\n"
        "1. RECAP — Summarize the 2-3 most useful things they just learned, one sentence each.\n"
        "2. QUESTION — Ask one genuine engagement question: 'Drop in the comments: [specific question about the video topic].'\n"
        "3. SOFT CTA — 'If you're thinking about [city/topic], DM me or drop your questions below — I answer every one.'\n"
        "4. TEASE — 'Now that you know [what they just learned], the next thing you need to understand is "
        "[next video topic]. I've got that one coming up — make sure you're subscribed so you don't miss it.'\n\n"
        "FAIR HOUSING COMPLIANCE: All content must comply with the Fair Housing Act. Never reference protected classes, "
        "neighborhood demographics, school quality, or any language that implies who should or should not live somewhere. "
        "Focus only on property features, agent expertise, market conditions, and client goals."
    )

    user_prompt = (
        f"Write a complete 4-part YouTube video script using the hook formula, CTA template, and end screen formula from your instructions.\n\n"
        f"- Topic: {topic}\n"
        f"- City/Market: {city}\n"
        f"- Content Pillar: {pillar}\n"
        f"- Target Audience: {audience}\n"
        f"- Agent Name: {agent_name or 'the agent'}\n"
        f"- Contact CTA: {contact_cta}\n"
        f"- Unique Angle: {angle or 'Local expert, empathy-first approach'}\n\n"
        f"Body outline structure to follow for the {pillar} pillar:\n{body_structure}\n\n"
        "Return JSON with exactly these keys:\n"
        "- hook (str): The full 30-second opening hook using all three beats of the HOOK FORMULA\n"
        "- early_cta (str): Personalized early CTA using the PROVEN EARLY CTA TEMPLATE — warm, conversational, "
        f"closing with: {contact_cta}\n"
        "- body_outline (list[str]): 5-7 concise body section summaries for the outline view\n"
        "- body_sections (list[str]): 5-7 complete, word-for-word body sections. Each item must start with "
        "a bold section heading and include 2-4 spoken paragraphs of 3-5 sentences each — short enough to read "
        "in one breath. Include specific local references for this market. No placeholder bullets.\n"
        "- end_screen (str): Full end screen using all four beats of the END SCREEN FORMULA in order\n"
        "- full_script (str): The complete teleprompter-ready script assembled from hook, early_cta, "
        "body_sections, and end_screen in sequence. Word-for-word narration only — no talking points, no section "
        f"meta-labels. Use {agent_name or 'the agent'}'s name naturally: once in the early CTA and once in the end screen soft CTA.\n"
        "- next_video_ideas (list[str]): Exactly 2 related video titles to tease from the end screen\n"
    )

    try:
        client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            temperature=0.75,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text if message.content else "{}"
        raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.IGNORECASE)
        raw = _re.sub(r"\s*```$", "", raw.strip())
        result = _json.loads(raw.strip())

        # Audit log (non-blocking)
        try:
            import uuid as _uuid
            async with _async_session() as _audit:
                await _audit.execute(
                    __import__("sqlalchemy").text("""
                        INSERT INTO audit_log (id, location_id, action, payload, created_at)
                        VALUES (:id, :loc_id, 'script_formula', CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {"id": str(_uuid.uuid4()), "loc_id": location_id,
                     "payload": _json.dumps({"topic": topic, "model": "claude-sonnet-4-5",
                                             "sample_count": ctx.metadata.sample_count})},
                )
                await _audit.commit()
        except Exception:
            pass

        result["_foundation_thin"] = ctx.metadata.sample_count < 15
        result["_sample_count"] = ctx.metadata.sample_count
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── YouTube Studio — Pillar Plan ─────────────────────────────────────────────

@app.post("/api/yt/pillar-plan")
async def yt_pillar_plan(req: Request):
    """
    Generate a 90-day content plan across all 5 content pillars,
    with market-specific video ideas.
    """
    import openai as _openai

    body             = await req.json()
    market           = body.get("market", "")
    agent_name       = body.get("agent_name", "")
    months_in_market = body.get("months_in_market", "Just Starting")

    system_prompt = (
        "You are a YouTube content strategist specializing in real estate lead generation through video.\n\n"
        "THE 5 CONTENT PILLARS AND THEIR LEAD PURPOSE:\n"
        "1. Relocation — FASTEST leads. People actively planning to move. High buyer intent.\n"
        "2. Market Updates — Seller leads + serious buyers watching trends.\n"
        "3. Neighborhood Deep Dive — Hyper-local, evergreen content. Builds long-term search traffic.\n"
        "4. Home Tour — Portfolio building + seller leads (sellers see you marketing homes).\n"
        "5. Lifestyle & Community — 99% of your audience. Low conversion but highest volume. Builds trust.\n\n"
        "HYPER-LOCAL CONTENT STRATEGY:\n"
        "The more specific to the market, the better the SEO and the stronger the trust signal. "
        "A video titled 'Moving to Springfield Missouri in 2026' beats 'Moving to a Midwest City' every time.\n\n"
        "REPETITION WINS:\n"
        "Consistency beats perfection. One video per week on the same pillars builds algorithmic momentum. "
        "Each pillar should have a posting rhythm so the channel covers all angles.\n\n"
        "Generate video ideas that are SPECIFIC to the market — use real neighborhood names, local landmarks, "
        "actual cost of living comparisons, real employer names, local school districts, etc. when relevant.\n\n"
        "FAIR HOUSING COMPLIANCE: All content must comply with the Fair Housing Act. Never reference protected classes, "
        "neighborhood demographics, school quality, or any language that implies who should or should not live somewhere. "
        "Focus only on property features, agent expertise, market conditions, and client goals."
    )

    user_prompt = (
        f"Create a 90-day content plan for:\n"
        f"- Market: {market}\n"
        f"- Agent/Brand: {agent_name or 'a real estate agent'}\n"
        f"- Time in Market: {months_in_market}\n\n"
        "Return JSON with this exact structure:\n"
        "{\n"
        '  "pillars": [\n'
        "    {\n"
        '      "name": "Relocation",\n'
        '      "lead_type": "Relocation Buyers — fastest leads",\n'
        '      "frequency": "1-2x per month",\n'
        '      "video_ideas": ["...", "...", "...", "..."]\n'
        "    },\n"
        '    {"name": "Market Updates", "lead_type": "...", "frequency": "...", "video_ideas": [...]},\n'
        '    {"name": "Neighborhood Deep Dive", "lead_type": "...", "frequency": "...", "video_ideas": [...]},\n'
        '    {"name": "Home Tour", "lead_type": "...", "frequency": "...", "video_ideas": [...]},\n'
        '    {"name": "Lifestyle & Community", "lead_type": "...", "frequency": "...", "video_ideas": [...]}\n'
        "  ]\n"
        "}\n\n"
        f"Each pillar should have 3-5 video ideas specific to {market}. "
        "Titles should be clickable, front-loaded with the local keyword, and include the year (2026) where natural. "
        f"Adjust difficulty of ideas to the agent's experience level: {months_in_market}. "
        "Make the full set work as a 90-day test plan: enough repetition across pillars to learn what the market rewards, "
        "with relocation as the fastest buyer-lead pillar, market updates for seller leads, neighborhood deep dives for evergreen search, "
        "home tours as portfolio proof, and lifestyle/community for the 99% not ready to transact yet. "
        "Return ONLY valid JSON."
    )

    client = _openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.8,
        )
        return JSONResponse(json.loads(resp.choices[0].message.content))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── YouTube Studio — Cover Forge ─────────────────────────────────────────────

@app.post("/api/yt/cover-forge")
async def yt_cover_forge(req: Request):
    """Generate 3 thumbnail concept variants + optimized title options."""
    import json as _json
    import re as _re
    import anthropic as _anthropic
    from db.engine import async_session as _async_session
    from config import get_current_location_id as _get_loc, settings as _settings
    from services.foundation import (
        assert_foundation_ready as _assert_ready,
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandContextError,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType

    body = await req.json()
    topic   = body.get("topic", "")
    market  = body.get("market", "")
    pillar  = body.get("pillar", "Relocation")
    emotion = body.get("emotion", "Curious / Surprised")
    reference = body.get("reference", "")
    home_tour_mode = bool(body.get("home_tour_mode", False))
    persona_photos = body.get("persona_photos") or []
    selected_persona_photo_id = body.get("selected_persona_photo_id", "")

    # ── Foundation gate ─────────────────────────────────────────────────────
    location_id = _get_loc()
    async with _async_session() as session:
        try:
            await _assert_ready(session=session, location_id=location_id)
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc), "foundation_not_ready": True}, status_code=422)
        try:
            ctx = await _get_brand_ctx(
                session=session,
                location_id=location_id,
                task_type=_TaskType.thumbnail_text,
                topic=topic,
                platform=None,
                audience=None,
            )
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    # ── Build voice preamble from Foundation ────────────────────────────────
    bp = ctx.brand_profile
    vp = ctx.voice_profile
    vocab = ctx.vocabulary
    tone_str = ", ".join(vp.tone if isinstance(vp.tone, list) else [vp.tone]) if vp.tone else "direct, genuine"
    cadence_str = vp.cadence or "natural, punchy"
    vocab_yes = "Use naturally: " + ", ".join(vocab.use if isinstance(vocab.use, list) else [vocab.use]) if vocab.use else ""
    vocab_no  = "Avoid entirely: " + ", ".join(vocab.avoid if isinstance(vocab.avoid, list) else [vocab.avoid]) if vocab.avoid else ""
    voice_examples = ""
    if ctx.voice_samples:
        examples = [f'  — "{s.text[:300]}"' for s in ctx.voice_samples[:3]]
        voice_examples = "Voice samples (match this style):\n" + "\n".join(examples)
    voice_preamble = f"VOICE PROFILE:\n- Tone: {tone_str}\n- Cadence: {cadence_str}\n- POV: {vp.pov or 'first-person'}\n"
    if vocab_yes: voice_preamble += f"- {vocab_yes}\n"
    if vocab_no:  voice_preamble += f"- {vocab_no}\n"
    if voice_examples: voice_preamble += f"\n{voice_examples}\n"

    # ── Prompts ──────────────────────────────────────────────────────────────
    system_prompt = voice_preamble + "\nYou are a YouTube thumbnail strategist for real estate agents. Generate 3 thumbnail concept variants and optimized title options."

    user_prompt = f"""THUMBNAIL RULES (non-negotiable):
- Thumbnail text: 3-5 words MAX, high contrast, mobile-readable, DIFFERENT from the title
- Face takes up 33% of the frame (agent face is prominent)
- Text must be BOLD, UPPERCASE or title case, easy to read on mobile
- Emotion on face should match the video topic and drive curiosity or urgency
- Each variant should look visually distinct (different layout, text angle, color emphasis)

Video topic: {topic}
Market: {market}
Content pillar: {pillar}
Desired emotion: {emotion}
Reference thumbnail/video URL: {reference or "none"}
Home tour mode: {"yes" if home_tour_mode else "no"}
AI persona photos available: {len(persona_photos)}
Selected persona photo id: {selected_persona_photo_id or "none"}
Persona photo types: {", ".join(sorted({str(p.get("shot_type", "photo")) for p in persona_photos if isinstance(p, dict)})) or "none"}

Generate:
1. Three thumbnail VARIANTS (visually distinct) - for each variant provide:
   - thumbnail_text: 3-5 bold words for the thumbnail (NOT the full title)
   - layout_description: brief description of the visual layout (e.g. "Agent left 1/3, bold text right, city skyline background")
   - background_color: a dark hex color that contrasts well (e.g. "#1a1a2e", "#0d1b2a", "#1a0a00")
   - text_color: contrasting text hex color
   - text_size: CSS font size suggestion ("14px", "16px", "18px")
   - image_prompt: a concise image-generation prompt for the thumbnail, including AI persona pose, whether to use a headshot or body shot, background, lighting, and text placement
   - quality_checks: object with mobile, contrast, text values summarizing why the variant passes

2. Three YouTube TITLE options:
   - Under 60 characters each
   - Keyword-rich with year (2026)
   - Curiosity gap or specific benefit
   - Different angles (e.g. question, statement, list)

Return ONLY valid JSON:
{{
  "variants": [
    {{"thumbnail_text": "...", "layout_description": "...", "background_color": "#...", "text_color": "#...", "text_size": "14px", "image_prompt": "...", "quality_checks": {{"mobile": "Pass", "contrast": "High", "text": "3-5 words"}}}},
    {{"thumbnail_text": "...", "layout_description": "...", "background_color": "#...", "text_color": "#...", "text_size": "14px", "image_prompt": "...", "quality_checks": {{"mobile": "Pass", "contrast": "High", "text": "3-5 words"}}}},
    {{"thumbnail_text": "...", "layout_description": "...", "background_color": "#...", "text_color": "#...", "text_size": "14px", "image_prompt": "...", "quality_checks": {{"mobile": "Pass", "contrast": "High", "text": "3-5 words"}}}}
  ],
  "titles": ["title 1", "title 2", "title 3"]
}}"""

    try:
        client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2000,
            temperature=0.75,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text if message.content else "{}"
        raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.IGNORECASE)
        raw = _re.sub(r"\s*```$", "", raw.strip())
        result = _json.loads(raw.strip())

        # Audit log (non-blocking)
        try:
            import uuid as _uuid
            async with _async_session() as _audit:
                await _audit.execute(
                    __import__("sqlalchemy").text("""
                        INSERT INTO audit_log (id, location_id, action, payload, created_at)
                        VALUES (:id, :loc_id, 'cover_forge', CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": str(_uuid.uuid4()),
                        "loc_id": location_id,
                        "payload": _json.dumps({
                            "topic": topic, "model": "claude-sonnet-4-5",
                            "sample_count": ctx.metadata.sample_count,
                        }),
                    },
                )
                await _audit.commit()
        except Exception:
            pass

        result["_foundation_thin"] = ctx.metadata.sample_count < 15
        result["_sample_count"] = ctx.metadata.sample_count
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── YouTube Studio — Repurpose Engine ────────────────────────────────────────

@app.post("/api/yt/repurpose")
async def yt_repurpose(req: Request):
    """Repurpose a video script into Shorts, social captions, and blog outline."""
    import json as _json
    import re as _re
    import anthropic as _anthropic
    from db.engine import async_session as _async_session
    from config import get_current_location_id as _get_loc, settings as _settings
    from services.foundation import (
        assert_foundation_ready as _assert_ready,
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandContextError,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType

    body = await req.json()
    script  = body.get("script", "")[:4000]  # cap at 4000 chars
    topic   = body.get("topic", "")
    market  = body.get("market", "")
    outputs = body.get("outputs", ["shorts", "instagram", "tiktok", "blog"])

    # ── Foundation gate ─────────────────────────────────────────────────────
    location_id = _get_loc()
    async with _async_session() as session:
        try:
            await _assert_ready(session=session, location_id=location_id)
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc), "foundation_not_ready": True}, status_code=422)
        try:
            ctx = await _get_brand_ctx(
                session=session,
                location_id=location_id,
                task_type=_TaskType.clip_caption,
                topic=topic,
                platform=None,
                audience=None,
            )
        except _BrandContextError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    bp = ctx.brand_profile
    vp = ctx.voice_profile
    vocab = ctx.vocabulary
    tone_str = ", ".join(vp.tone if isinstance(vp.tone, list) else [vp.tone]) if vp.tone else "direct, genuine"
    cadence_str = vp.cadence or "natural, punchy"
    vocab_yes = "Use naturally: " + ", ".join(vocab.use if isinstance(vocab.use, list) else [vocab.use]) if vocab.use else ""
    vocab_no  = "Avoid entirely: " + ", ".join(vocab.avoid if isinstance(vocab.avoid, list) else [vocab.avoid]) if vocab.avoid else ""
    voice_examples = ""
    if ctx.voice_samples:
        examples = [f'  — "{s.text[:300]}"' for s in ctx.voice_samples[:3]]
        voice_examples = "Voice samples (match this style):\n" + "\n".join(examples)
    voice_preamble = f"VOICE PROFILE:\n- Tone: {tone_str}\n- Cadence: {cadence_str}\n- POV: {vp.pov or 'first-person'}\n"
    if vocab_yes: voice_preamble += f"- {vocab_yes}\n"
    if vocab_no:  voice_preamble += f"- {vocab_no}\n"
    if voice_examples: voice_preamble += f"\n{voice_examples}\n"

    system_prompt = voice_preamble + "\nYou are a social media content strategist for a real estate agent. Repurpose video scripts into multiple content formats that sound authentic to the creator's voice."

    user_prompt = f"""Repurpose the following video script into multiple content formats.

Video topic: {topic}
Market: {market}

Script:
{script}

Generate ONLY the formats requested: {', '.join(outputs)}

Return JSON with these keys (only include requested outputs):
- shorts: list of 3 objects, each with: hook (3-second opening), body (50-second content), cta (7-second close)
- instagram: object with: hook_line, body (string with \\n\\n paragraph breaks), cta_line, hashtags (list of 15-20 strings without #)
- tiktok_hooks: list of 5 short bold hook strings (each under 15 words, pattern-interrupt style)
- blog: object with: h1_title, meta_description (under 160 chars), sections (list of objects with h2 and bullets list), internal_links (list of 2-3 suggested related article titles)

Return ONLY valid JSON."""

    try:
        client = _anthropic.Anthropic(api_key=_settings.anthropic_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=3000,
            temperature=0.75,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text if message.content else "{}"
        raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.IGNORECASE)
        raw = _re.sub(r"\s*```$", "", raw.strip())
        result = _json.loads(raw.strip())

        # Audit log (non-blocking)
        try:
            import uuid as _uuid
            async with _async_session() as _audit:
                await _audit.execute(
                    __import__("sqlalchemy").text("""
                        INSERT INTO audit_log (id, location_id, action, payload, created_at)
                        VALUES (:id, :loc_id, 'repurpose_yt', CAST(:payload AS jsonb), now())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": str(_uuid.uuid4()),
                        "loc_id": location_id,
                        "payload": _json.dumps({
                            "topic": topic, "model": "claude-sonnet-4-5",
                            "sample_count": ctx.metadata.sample_count,
                        }),
                    },
                )
                await _audit.commit()
        except Exception:
            pass

        result["_foundation_thin"] = ctx.metadata.sample_count < 15
        result["_sample_count"] = ctx.metadata.sample_count
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── YouTube Studio — Lead Page Generator ─────────────────────────────────────

@app.post("/api/yt/lead-page")
async def yt_lead_page(req: Request):
    """Generate landing page copy and video description CTA for lead capture."""
    import openai as _openai

    body         = await req.json()
    topic        = body.get("topic", "")
    market       = body.get("market", "")
    lead_magnet  = body.get("lead_magnet", "Free Relocation Guide")
    agent_name   = body.get("agent_name", "")
    webhook_url  = body.get("webhook_url", "")
    brand_voice  = body.get("brand_voice", "")
    crm          = body.get("crm", "Follow Up Boss")

    client = _openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    prompt = f"""You are a conversion copywriter for real estate agents. Create landing page copy and a YouTube video description CTA.

Video topic: {topic}
Market: {market}
Lead magnet: {lead_magnet}
Agent name: {agent_name or 'the agent'}
Brand voice: {brand_voice or 'Warm, helpful, local expert. Clear and conversational.'}
CRM destination: {crm}

Important product constraint:
- PodClickAI does NOT send emails.
- The email sequence is copy/export content for the agent's CRM or email platform.
- The CRM remains the sender of record.
- Do not include instructions for PodClickAI to send mail directly.

Generate:
1. landing_page: object with:
   - headline (str): Compelling H1 that speaks directly to the viewer's situation and mentions the market
   - sub_headline (str): One sentence that explains what they get and why it matters
   - benefits (list of 3-5 strings): Specific, concrete benefits they will receive — no fluff
   - form_label (str): Short label above the form fields (e.g. "Get Your Free Guide")
   - button_text (str): High-converting button CTA (e.g. "Send Me the Guide")
   - thank_you_message (str): Warm 1-2 sentence confirmation shown after form submit
2. description_cta (str): The exact YouTube description text to drive leads. Start with a hook line referencing the video topic, list 3-4 bullet points of what they get, include [YOUR LANDING PAGE URL] placeholder, and end with [YOUR PHONE/EMAIL] placeholder. 150-200 words total. Natural, conversational tone — not salesy.
3. email_sequence (list of 5 objects): CRM-ready nurture sequence. Each object has:
   - day (str): e.g. "Day 0", "Day 2", "Day 4", "Day 7", "Day 10"
   - purpose (str): Welcome, Value, Story, Objection, CTA
   - subject (str): clear, human, not spammy
   - preview_text (str): inbox preview line
   - body (str): 130-220 words, in the brand voice, with [FIRST NAME] placeholder where useful
4. crm_handoff: object with:
   - strategy (str): Explain that PodClick generates copy and the CRM sends it
   - recommended_destination (str): The selected CRM/platform
   - tags (list[str]): CRM tags to apply, including "YouTube Lead" and a topic-specific tag
   - fields (list[str]): Contact fields to capture
   - automation_steps (list[str]): High-level setup steps for {crm}; do not require PodClickAI as email sender
   - export_notes (list[str]): How to use copy/paste, CSV export, or CRM campaign import
5. ghl_steps (list of 6-8 strings): Clear step-by-step setup instructions for GoHighLevel if GHL is used as the capture layer, covering: creating the funnel, naming it (use {topic}-lead-capture as the slug), adding form fields (Name, Email, Phone), setting up the webhook on form submission, tagging new contacts as "YouTube Lead", and handing off the email sequence to {crm}.

Return ONLY valid JSON with keys: landing_page, description_cta, email_sequence, crm_handoff, ghl_steps"""

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.6,
        )
        return JSONResponse(json.loads(resp.choices[0].message.content))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Studio — Today's Topic (from Content Scheduler) ──────────────────────────

@app.get("/api/studio/today-topic")
async def studio_today_topic():
    """
    Return today's scheduled topic from data/scheduler.json.

    Rotation logic: each topic carries an optional `last_served` date string
    (YYYY-MM-DD). The endpoint returns the first topic that has NOT been served
    today, stamps it with today's date, and saves the file. Calling the endpoint
    again on the same day returns the same topic (idempotent). The queue
    advances automatically the next morning.

    If all topics have already been served today, returns the earliest-served
    topic so Studio is never left empty.
    """
    import calendar
    from datetime import date

    schedule_file = DATA_DIR / "scheduler.json"
    if not schedule_file.exists():
        return JSONResponse({"topic": None, "pillar": None, "message": "No schedule saved yet. Set one in Click Studio → Content Scheduler."})

    sched      = json.loads(schedule_file.read_text())
    today_str  = date.today().isoformat()                       # "2026-05-21"
    today_name = calendar.day_name[date.today().weekday()]      # "Wednesday"

    shoot_days = sched.get("shoot_days", [])
    topics     = sched.get("topics", [])    # list of {title, pillar, notes, last_served?}
    market     = sched.get("market", "")
    is_shoot_day = today_name in shoot_days

    if not topics:
        return JSONResponse({"topic": None, "pillar": None, "message": "No topics saved in your schedule yet.", "market": market})

    # Find the first topic not yet served today
    chosen_idx = next(
        (i for i, t in enumerate(topics) if t.get("last_served") != today_str),
        None,
    )

    if chosen_idx is None:
        # All topics served today — return the one served longest ago (first in list)
        # so Studio is never empty. Don't re-stamp; it was already stamped today.
        chosen_idx = 0

    chosen = topics[chosen_idx]

    # Stamp and persist only if this topic hasn't been stamped today yet
    if chosen.get("last_served") != today_str:
        topics[chosen_idx]["last_served"] = today_str
        sched["topics"] = topics
        schedule_file.write_text(json.dumps(sched, indent=2))

    return JSONResponse({
        "topic":        chosen.get("title", ""),
        "pillar":       chosen.get("pillar", ""),
        "notes":        chosen.get("notes", ""),
        "market":       market,
        "is_shoot_day": is_shoot_day,
        "today":        today_name,
        "shoot_days":   shoot_days,
        "queue_position": chosen_idx + 1,
        "queue_total":    len(topics),
        "message":      None,
    })


# ── Studio — Generate Script from Topic ──────────────────────────────────────

@app.post("/api/studio/generate-script")
async def studio_generate_script(req: Request):
    """
    Generate a full teleprompter-ready script using the 4-part formula.
    Input: topic, pillar, market, notes, agent_name, audience
    Output: { script: "...", title: "...", hook_line: "..." }
    """
    import openai as _openai

    body        = await req.json()
    topic       = body.get("topic", "")
    pillar      = body.get("pillar", "Relocation")
    market      = body.get("market", "")
    notes       = body.get("notes", "")
    agent_name  = body.get("agent_name", "JP")
    audience    = body.get("audience", "Relocation Buyers")

    client = _openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    prompt = f"""You are an expert YouTube scriptwriter for real estate agents. Write a complete, teleprompter-ready script for a YouTube video.

TOPIC: {topic}
CONTENT PILLAR: {pillar}
MARKET: {market or "not specified"}
TARGET AUDIENCE: {audience}
AGENT NAME: {agent_name}
ADDITIONAL NOTES: {notes or "none"}

Follow this exact 4-part formula:

PART 1 — HOOK (first 30 seconds, spoken directly to camera):
- Open with a bold, curiosity-driven statement or question
- Name the specific problem or desire the viewer has
- Promise the payoff: what they'll know by the end
- Keep it punchy — 3-5 sentences max

PART 2 — EARLY CTA (15 seconds, after hook):
- Ask them to subscribe / like / comment with their city
- One smooth sentence, not salesy

PART 3 — BODY (the main content, 5-8 minutes when spoken aloud):
- Deliver the value with 5-7 clear sections
- Each section starts with a bold subheading (surround with **asterisks**)
- Use conversational, natural language — write how people talk
- Include 1-2 specific local data points or examples per section
- Add "(pause)" notes where natural breathing moments help

PART 4 — END SCREEN (final 45 seconds):
- Recap the top 3 takeaways in one sentence each
- Ask a direct engagement question ("comment below: which of these surprised you most?")
- Soft CTA: "If you're thinking about {market}, DM me or drop your questions below — I answer every one."
- Tease the next video topic

Format the full script with clear section labels. Write it ready to read aloud — natural contractions, conversational flow, no stiff corporate language.

Return JSON with:
- script (str): the complete teleprompter-ready script, plain text with **bold** for section headers
- title (str): a compelling YouTube title (under 60 chars)
- hook_line (str): just the first sentence of the hook — the attention-grabber"""

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.75,
        )
        return JSONResponse(json.loads(resp.choices[0].message.content))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ════════════════════════════════════════════════════════════════════════════════
# PHASE 2B — 30-Day Calendar (Content Board)
# ════════════════════════════════════════════════════════════════════════════════

import logging as _cal_logging
_cal_logger = _cal_logging.getLogger("podclick.calendar")


@app.get("/calendar")
async def serve_calendar():
    """Serve the 30-Day Content Board page."""
    from fastapi.responses import FileResponse
    return FileResponse("frontend/calendar.html")


# ── Helper: anti-clumping bucket distribution ────────────────────────────────────
def _distribute_buckets(vyral_mix: dict, slot_count: int) -> list:
    """Distribute buckets across slots with anti-clumping (no 3 consecutive same)."""
    from collections import Counter
    pool = []
    for bucket, weight in vyral_mix.items():
        pool.extend([bucket] * round(slot_count * float(weight)))
    while len(pool) > slot_count:
        pool.pop()
    while len(pool) < slot_count:
        # pad with the heaviest weighted bucket
        heaviest = max(vyral_mix, key=lambda k: vyral_mix[k])
        pool.append(heaviest)
    result = []
    counts = Counter(pool)
    prev1 = None
    prev2 = None
    for _ in range(slot_count):
        avail = {b: c for b, c in counts.items() if c > 0}
        if not avail:
            break
        if prev1 == prev2 and prev1 is not None:
            filtered = {b: c for b, c in avail.items() if b != prev1}
            if filtered:
                avail = filtered
        chosen = max(avail, key=lambda k: avail[k])
        result.append(chosen)
        counts[chosen] -= 1
        prev2, prev1 = prev1, chosen
    return result


# ── Route: GET /api/calendar ─────────────────────────────────────────────────────
@app.get("/api/calendar")
async def calendar_list(from_date: Optional[str] = None, to_date: Optional[str] = None):
    """Return all posts for the location within the given date range."""
    import uuid as _uuidmod
    from datetime import date, datetime, timedelta, timezone
    from config import get_current_location_id as _gcl
    from db.engine import async_session as _async_session
    from db.models import Post as _Post, PostVariant as _PostVariant
    from sqlalchemy import select, and_

    loc = _gcl()
    loc_uuid = _uuidmod.UUID(loc)

    today = datetime.now(timezone.utc).date()
    try:
        d_from = date.fromisoformat(from_date) if from_date else today
        d_to = date.fromisoformat(to_date) if to_date else (today + timedelta(days=30))
    except ValueError:
        return JSONResponse({"error": "invalid date format (expected YYYY-MM-DD)"}, status_code=400)

    dt_from = datetime.combine(d_from, datetime.min.time(), tzinfo=timezone.utc)
    dt_to = datetime.combine(d_to, datetime.max.time(), tzinfo=timezone.utc)

    async with _async_session() as session:
        # Get posts in range (include drafts with no scheduled_at if within window via created_at)
        stmt = (
            select(_Post)
            .where(_Post.location_id == loc_uuid)
            .where(
                and_(
                    (_Post.scheduled_at >= dt_from) | (_Post.scheduled_at.is_(None)),
                    (_Post.scheduled_at <= dt_to) | (_Post.scheduled_at.is_(None)),
                )
            )
            .order_by(_Post.scheduled_at.asc().nulls_last(), _Post.created_at.desc())
        )
        result = await session.execute(stmt)
        posts = result.scalars().all()

        post_ids = [p.id for p in posts]
        variants_by_post = {}
        if post_ids:
            v_stmt = select(_PostVariant).where(_PostVariant.post_id.in_(post_ids))
            v_result = await session.execute(v_stmt)
            for v in v_result.scalars().all():
                variants_by_post.setdefault(v.post_id, []).append(v)

        out_posts = []
        for p in posts:
            variants = variants_by_post.get(p.id, [])
            # caption preview from base_caption on Post first, else first variant
            preview_src = (p.base_caption or (variants[0].caption if variants else "") or "")
            out_posts.append({
                "id": str(p.id),
                "bucket": p.bucket,
                "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
                "status": p.status,
                "source": p.source,
                "caption_preview": preview_src[:80],
                "platforms_with_variants": [v.platform for v in variants],
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })

    return JSONResponse({
        "posts": out_posts,
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
    })


# ── Route: POST /api/calendar/auto-plan ──────────────────────────────────────────
@app.post("/api/calendar/auto-plan")
async def calendar_auto_plan(request: Request):
    """Auto-generate a 30-day plan from the user's Blueprint + Foundation."""
    import uuid as _uuidmod
    import asyncio as _asyncio
    from datetime import date, datetime, timedelta, timezone
    from config import get_current_location_id as _gcl, settings as _settings
    from db.engine import async_session as _async_session
    from db.models import Post as _Post, PostVariant as _PostVariant
    from sqlalchemy import text as _sql_text
    from services.foundation import (
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandCtxErr,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType
    import openai as _openai

    body = await request.json() if (await request.body()) else {}
    slot_count = int(body.get("slot_count") or 30)
    start_date_s = body.get("start_date")

    if slot_count < 1 or slot_count > 60:
        return JSONResponse({"error": "slot_count must be 1-60"}, status_code=400)

    today = datetime.now(timezone.utc).date()
    try:
        start_date = date.fromisoformat(start_date_s) if start_date_s else today
    except ValueError:
        return JSONResponse({"error": "invalid start_date"}, status_code=400)

    loc = _gcl()
    loc_uuid = _uuidmod.UUID(loc)

    # 1. Load Blueprint
    default_pillars = [
        {"name": "Market intelligence", "weight": 0.3},
        {"name": "Client wins", "weight": 0.3},
        {"name": "Local lifestyle", "weight": 0.2},
        {"name": "Real estate tips", "weight": 0.2},
    ]
    default_vyral_mix = {"viral": 0.4, "brand": 0.3, "personal": 0.2, "conversion": 0.1}

    async with _async_session() as session:
        bp_result = await session.execute(
            _sql_text("SELECT pillars, vyral_mix FROM blueprints WHERE location_id = :loc_id"),
            {"loc_id": loc},
        )
        bp_row = bp_result.mappings().first()
        if bp_row is not None:
            pillars = bp_row.get("pillars") or default_pillars
            vyral_mix = bp_row.get("vyral_mix") or default_vyral_mix
        else:
            pillars = default_pillars
            vyral_mix = default_vyral_mix

        if not pillars:
            pillars = default_pillars
        if not vyral_mix:
            vyral_mix = default_vyral_mix

    # 2. Bucket distribution (anti-clumping)
    bucket_sequence = _distribute_buckets(vyral_mix, slot_count)

    # 3. Pillar weighted round-robin assignment
    sorted_pillars = sorted(pillars, key=lambda p: -float(p.get("weight", 0)))
    pillar_pool = []
    for p in sorted_pillars:
        count = max(1, round(slot_count * float(p.get("weight", 1.0 / len(sorted_pillars)))))
        pillar_pool.extend([p.get("name", "Content")] * count)
    while len(pillar_pool) < slot_count:
        pillar_pool.append(sorted_pillars[0].get("name", "Content"))
    pillar_pool = pillar_pool[:slot_count]

    # 4. Generate captions concurrently with semaphore (max 8)
    sem = _asyncio.Semaphore(8)
    client = _openai.AsyncOpenAI(api_key=_settings.openai_api_key)

    async def _gen_caption(idx: int, bucket: str, pillar: str) -> str:
        """Generate caption via Foundation + OpenAI."""
        async with sem:
            try:
                async with _async_session() as gen_session:
                    try:
                        ctx = await _get_brand_ctx(
                            session=gen_session,
                            location_id=loc,
                            task_type=_TaskType.linkedin_post,
                            topic=pillar,
                            platform=None,
                            audience=None,
                        )
                    except _BrandCtxErr:
                        ctx = None

                bp_obj = ctx.brand_profile if ctx else None
                vp_obj = ctx.voice_profile if ctx else None
                vocab = ctx.vocabulary if ctx else None

                full_name = (bp_obj.full_name if bp_obj else None) or "a real estate professional"
                voice_tone = (vp_obj.tone if vp_obj else None) or []
                vocab_yes = (vocab.use if vocab else None) or []
                vocab_no = (vocab.avoid if vocab else None) or []

                voice_samples_block = ""
                if ctx and ctx.voice_samples:
                    samples = "\n---\n".join(s.text[:300] for s in ctx.voice_samples[:3])
                    voice_samples_block = f"\n\nVoice samples for guidance:\n{samples}"

                system_prompt = (
                    f"You are writing social media content for {full_name}. "
                    f"Voice tone: {', '.join(voice_tone) if voice_tone else 'direct, professional'}. "
                    f"Use these words/phrases naturally: {', '.join(vocab_yes[:10]) if vocab_yes else '(none specified)'}. "
                    f"Avoid: {', '.join(vocab_no[:10]) if vocab_no else '(none specified)'}."
                    f"{voice_samples_block}"
                )
                user_prompt = (
                    f"Write a {bucket} social media post (2-3 sentences, no hashtags) "
                    f"about: {pillar}. Use the examples as voice guidance. "
                    f"Return only the post text, no preamble."
                )

                resp = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.8,
                    max_tokens=300,
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                _cal_logger.warning("[auto_plan.gen_error] idx=%d error=%s", idx, exc)
                return f"({pillar}) — content draft pending."

    tasks = [
        _gen_caption(i, bucket_sequence[i], pillar_pool[i])
        for i in range(slot_count)
    ]
    captions = await _asyncio.gather(*tasks)

    # 5. Create Post + base PostVariant for each slot
    posts_created = []
    mix_actual = {"viral": 0, "brand": 0, "personal": 0, "conversion": 0, "podcast": 0}

    async with _async_session() as session:
        for i in range(slot_count):
            slot_date = start_date + timedelta(days=i)
            # 10am Chicago = 15:00 UTC (CDT approximation)
            scheduled_at = datetime(
                slot_date.year, slot_date.month, slot_date.day,
                15, 0, 0, tzinfo=timezone.utc,
            )
            bucket = bucket_sequence[i]
            pillar = pillar_pool[i]
            caption = captions[i]

            post_obj = _Post(
                location_id=loc_uuid,
                bucket=bucket,
                base_caption=caption,
                scheduled_at=scheduled_at,
                status="draft",
                source="auto_plan",
            )
            session.add(post_obj)
            await session.flush()

            mix_actual[bucket] = mix_actual.get(bucket, 0) + 1
            posts_created.append({
                "id": str(post_obj.id),
                "bucket": bucket,
                "pillar": pillar,
                "scheduled_at": scheduled_at.isoformat(),
                "caption_preview": caption[:80],
            })

            _cal_logger.info(
                "[auto_plan.generated] post_id=%s bucket=%s pillar=%s",
                str(post_obj.id), bucket, pillar,
            )

        await session.commit()

    return JSONResponse({
        "ok": True,
        "posts_created": len(posts_created),
        "mix_actual": mix_actual,
        "posts": posts_created,
    })


# ── Route: GET /api/calendar/posts/{post_id} ────────────────────────────────────
@app.get("/api/calendar/posts/{post_id}")
async def calendar_get_post(post_id: str):
    """Return a full post with all variants."""
    import uuid as _uuidmod
    from db.engine import async_session as _async_session
    from db.models import Post as _Post, PostVariant as _PostVariant
    from sqlalchemy import select

    try:
        pid = _uuidmod.UUID(post_id)
    except ValueError:
        return JSONResponse({"error": "invalid post_id"}, status_code=400)

    async with _async_session() as session:
        post = (await session.execute(select(_Post).where(_Post.id == pid))).scalar_one_or_none()
        if post is None:
            return JSONResponse({"error": "post not found"}, status_code=404)

        v_result = await session.execute(
            select(_PostVariant).where(_PostVariant.post_id == pid)
        )
        variants = v_result.scalars().all()

    return JSONResponse({
        "id": str(post.id),
        "bucket": post.bucket,
        "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None,
        "status": post.status,
        "source": post.source,
        "base_caption": post.base_caption or "",
        "variants": [
            {
                "id": str(v.id),
                "platform": v.platform,
                "caption": v.caption,
                "first_comment": v.first_comment,
                "media_urls": v.media_urls or [],
                "platform_specific": v.platform_specific or {},
            }
            for v in variants
            if v.platform != "base"   # base content now lives on Post.base_caption
        ],
    })


# ── Route: PATCH /api/calendar/posts/{post_id} ──────────────────────────────────
@app.patch("/api/calendar/posts/{post_id}")
async def calendar_patch_post(post_id: str, request: Request):
    """Update post scheduled_at. Only allowed if status in (draft, scheduled)."""
    import uuid as _uuidmod
    from datetime import datetime
    from db.engine import async_session as _async_session
    from db.models import Post as _Post
    from sqlalchemy import select

    try:
        pid = _uuidmod.UUID(post_id)
    except ValueError:
        return JSONResponse({"error": "invalid post_id"}, status_code=400)

    body = await request.json()
    new_sched = body.get("scheduled_at")
    if not new_sched:
        return JSONResponse({"error": "scheduled_at required"}, status_code=400)
    try:
        new_dt = datetime.fromisoformat(new_sched.replace("Z", "+00:00"))
    except ValueError:
        return JSONResponse({"error": "invalid scheduled_at"}, status_code=400)

    async with _async_session() as session:
        post = (await session.execute(select(_Post).where(_Post.id == pid))).scalar_one_or_none()
        if post is None:
            return JSONResponse({"error": "post not found"}, status_code=404)
        if post.status not in ("draft", "scheduled"):
            return JSONResponse(
                {"error": f"cannot reschedule post with status '{post.status}'"},
                status_code=400,
            )
        post.scheduled_at = new_dt
        await session.commit()

        return JSONResponse({
            "id": str(post.id),
            "bucket": post.bucket,
            "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None,
            "status": post.status,
        })


# ── Route: POST /api/calendar/posts/{post_id}/variants/generate ─────────────────
@app.post("/api/calendar/posts/{post_id}/variants/generate")
async def calendar_generate_variants(post_id: str, request: Request):
    """Generate platform-specific variants for a post."""
    import uuid as _uuidmod
    import asyncio as _asyncio
    from config import get_current_location_id as _gcl, settings as _settings
    from db.engine import async_session as _async_session
    from db.models import Post as _Post, PostVariant as _PostVariant
    from sqlalchemy import select
    from services.foundation import (
        get_brand_context as _get_brand_ctx,
        BrandContextError as _BrandCtxErr,
    )
    from schemas.foundation import BrandContextTaskType as _TaskType
    import openai as _openai

    try:
        pid = _uuidmod.UUID(post_id)
    except ValueError:
        return JSONResponse({"error": "invalid post_id"}, status_code=400)

    body = await request.json() if (await request.body()) else {}
    platforms = body.get("platforms") or ["linkedin", "facebook", "instagram"]

    loc = _gcl()
    client = _openai.AsyncOpenAI(api_key=_settings.openai_api_key)

    async with _async_session() as session:
        post = (await session.execute(select(_Post).where(_Post.id == pid))).scalar_one_or_none()
        if post is None:
            return JSONResponse({"error": "post not found"}, status_code=404)

        existing_v = (
            await session.execute(select(_PostVariant).where(_PostVariant.post_id == pid))
        ).scalars().all()
        existing_platforms = {v.platform for v in existing_v}
        base_caption = (post.base_caption or "") or ""

        if not base_caption:
            return JSONResponse(
                {"error": "no base caption found — cannot generate platform variants"},
                status_code=400,
            )

        to_generate = [p for p in platforms if p not in existing_platforms]
        if not to_generate:
            return JSONResponse({"ok": True, "generated": [], "skipped": list(existing_platforms)})

    task_map = {
        "linkedin": _TaskType.linkedin_post,
        "facebook": _TaskType.facebook_post,
        "instagram": _TaskType.instagram_caption,
        "x": _TaskType.x_post,
        "tiktok": _TaskType.tiktok_caption,
        "youtube": _TaskType.youtube_short_caption,
    }

    sem = _asyncio.Semaphore(3)

    async def _gen_variant(platform: str):
        async with sem:
            task_type = task_map.get(platform, _TaskType.linkedin_post)
            try:
                async with _async_session() as gen_session:
                    try:
                        ctx = await _get_brand_ctx(
                            session=gen_session,
                            location_id=loc,
                            task_type=task_type,
                            topic="",
                            platform=platform,
                            audience=None,
                        )
                    except _BrandCtxErr:
                        ctx = None

                bp_obj = ctx.brand_profile if ctx else None
                vp_obj = ctx.voice_profile if ctx else None
                vocab = ctx.vocabulary if ctx else None
                full_name = (bp_obj.full_name if bp_obj else None) or "a real estate professional"
                voice_tone = (vp_obj.tone if vp_obj else None) or []
                vocab_yes = (vocab.use if vocab else None) or []

                # Platform-specific style guidance
                if platform == "linkedin":
                    style = "Professional tone. 3-4 sentences. No hashtags."
                elif platform == "instagram":
                    style = "Conversational, with line breaks. 5-8 sentences. No hashtags in caption."
                elif platform == "facebook":
                    style = "Friendly, community tone. 2-3 sentences. No hashtags."
                elif platform == "x":
                    style = "Under 280 characters. Punchy. No hashtags."
                else:
                    style = "Use the base content with minor platform-appropriate tweaks."

                system_prompt = (
                    f"You rewrite social posts for {full_name}. "
                    f"Voice tone: {', '.join(voice_tone) if voice_tone else 'direct, professional'}. "
                    f"Use these words naturally: {', '.join(vocab_yes[:8]) if vocab_yes else '(none specified)'}."
                )
                user_prompt = (
                    f"Rewrite this base post for {platform}. {style}\n\n"
                    f"Base post:\n{base_caption}\n\n"
                    "Return only the rewritten post text. No preamble."
                )

                resp = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.7,
                    max_tokens=500,
                )
                caption = (resp.choices[0].message.content or "").strip()

                first_comment = None
                if platform == "instagram":
                    fc_resp = await client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": (
                                f"Generate exactly 10 relevant hashtags for this Instagram post. "
                                f"Return them as a single line, space-separated, each starting with #. "
                                f"No other text.\n\nPost:\n{caption}"
                            )},
                        ],
                        temperature=0.7,
                        max_tokens=200,
                    )
                    first_comment = (fc_resp.choices[0].message.content or "").strip()

                return platform, caption, first_comment
            except Exception as exc:
                _cal_logger.warning("[variant.gen_error] platform=%s error=%s", platform, exc)
                return platform, None, None

    results = await _asyncio.gather(*[_gen_variant(p) for p in to_generate])

    generated = []
    async with _async_session() as session:
        for platform, caption, first_comment in results:
            if not caption:
                continue
            variant_obj = _PostVariant(
                post_id=pid,
                platform=platform,
                caption=caption,
                first_comment=first_comment,
            )
            session.add(variant_obj)
            await session.flush()
            generated.append({
                "id": str(variant_obj.id),
                "platform": platform,
                "caption": caption,
                "first_comment": first_comment,
            })
            _cal_logger.info("[variant.generated] post_id=%s platform=%s", post_id, platform)
        await session.commit()

    return JSONResponse({
        "ok": True,
        "generated": generated,
        "skipped": list(existing_platforms),
    })


# ── Route: POST /api/calendar/posts/{post_id}/publish ───────────────────────────
@app.post("/api/calendar/posts/{post_id}/publish")
async def calendar_publish_post(post_id: str):
    """Publish all variants of a post immediately via the Arq queue."""
    import uuid as _uuidmod
    from datetime import datetime, timedelta, timezone
    from config import get_current_location_id as _gcl
    from db.engine import async_session as _async_session
    from db.models import Post as _Post, PostVariant as _PostVariant, PostAttempt as _PostAttempt
    from sqlalchemy import select, update as _upd
    from services.ghl_adapter import ghl_adapter as _ghl

    PLATFORM_STAGGER = {
        "linkedin": 0,
        "x": 60,
        "facebook": 120,
        "instagram": 180,
        "tiktok": 240,
        "youtube": 300,
        "gmb": 360,
    }

    try:
        pid = _uuidmod.UUID(post_id)
    except ValueError:
        return JSONResponse({"error": "invalid post_id"}, status_code=400)

    loc = _gcl()

    # 1. Load GHL accounts
    try:
        accounts = await _ghl.list_accounts(loc)
    except Exception as exc:
        return JSONResponse({"error": f"failed to load GHL accounts: {exc}"}, status_code=500)

    # Map platform → account_id (first non-expired match)
    platform_to_account = {}
    for acct in accounts:
        plat = (acct.get("platform") or "").lower()
        if acct.get("expired"):
            continue
        if plat and plat not in platform_to_account:
            platform_to_account[plat] = acct.get("id") or acct.get("_id")

    async with _async_session() as session:
        post = (await session.execute(select(_Post).where(_Post.id == pid))).scalar_one_or_none()
        if post is None:
            return JSONResponse({"error": "post not found"}, status_code=404)

        variants = (
            await session.execute(
                select(_PostVariant).where(
                    _PostVariant.post_id == pid,
                    _PostVariant.platform != "base",
                )
            )
        ).scalars().all()

        if not variants:
            return JSONResponse(
                {"error": "no platform variants to publish — generate variants first"},
                status_code=400,
            )

        enqueued = []
        skipped = []

        for v in variants:
            platform = v.platform
            account_id = platform_to_account.get(platform)
            if not account_id:
                skipped.append({"platform": platform, "reason": "no_connected_account"})
                continue

            offset_s = PLATFORM_STAGGER.get(platform, 0)

            # Update variant platform_specific with ghl_account_id (preserve existing)
            ps = dict(v.platform_specific or {})
            ps["ghl_account_id"] = account_id
            ps["stagger_offset_s"] = offset_s
            v.platform_specific = ps

            attempt_obj = _PostAttempt(
                post_id=pid,
                variant_id=v.id,
                platform=platform,
                provider="ghl",
                status="queued",
            )
            session.add(attempt_obj)
            await session.flush()

            enqueued.append({
                "platform": platform,
                "attempt_id": str(attempt_obj.id),
                "stagger_s": offset_s,
            })
            _ghl_logger.info(
                "[publish.requested] attempt_id=%s platform=%s stagger_offset_s=%d location=%s",
                str(attempt_obj.id), platform, offset_s, loc,
            )

        # Flip post status to publishing
        post.status = "publishing"
        await session.commit()

    # 2. Enqueue arq jobs
    if enqueued:
        try:
            from arq import create_pool
            from workers.publish_worker import WorkerSettings as _WS
            redis = await create_pool(_WS.redis_settings)
            for item in enqueued:
                await redis.enqueue_job(
                    "publish_variant",
                    item["attempt_id"],
                    _defer_by=timedelta(seconds=item["stagger_s"]),
                )
            await redis.close()
        except Exception as exc:
            _ghl_logger.warning("Failed to enqueue arq jobs: %s — variants queued in DB only", exc)

    return JSONResponse({
        "ok": True,
        "enqueued": enqueued,
        "skipped": skipped,
    })


# ── Route: DELETE /api/calendar/posts/{post_id} ─────────────────────────────────
@app.delete("/api/calendar/posts/{post_id}")
async def calendar_delete_post(post_id: str):
    """Delete a draft post (cascades to variants and attempts)."""
    import uuid as _uuidmod
    from db.engine import async_session as _async_session
    from db.models import Post as _Post
    from sqlalchemy import select, delete as _del

    try:
        pid = _uuidmod.UUID(post_id)
    except ValueError:
        return JSONResponse({"error": "invalid post_id"}, status_code=400)

    async with _async_session() as session:
        post = (await session.execute(select(_Post).where(_Post.id == pid))).scalar_one_or_none()
        if post is None:
            return JSONResponse({"error": "post not found"}, status_code=404)
        if post.status != "draft":
            return JSONResponse(
                {"error": f"can only delete drafts (status='{post.status}')"},
                status_code=400,
            )
        await session.execute(_del(_Post).where(_Post.id == pid))
        await session.commit()

    return JSONResponse({"ok": True, "deleted": post_id})


# ══════════════════════════════════════════════════════════════════════════════
# ── BRICK THE FOREMAN (Phase 3A) ──────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

# ── Page routes ───────────────────────────────────────────────────────────────

@app.get("/walkthrough")
async def walkthrough_page():
    """Serve Brick's morning walk-through dashboard."""
    from fastapi.responses import FileResponse
    return FileResponse(BASE_DIR / "frontend" / "walkthrough.html")


@app.get("/permit")
async def permit_page():
    """Serve Brick's permit tier screen."""
    from fastapi.responses import FileResponse
    return FileResponse(BASE_DIR / "frontend" / "permit.html")


@app.get("/onboarding")
async def onboarding_page():
    """Serve the 6-step onboarding flow."""
    from fastapi.responses import FileResponse
    return FileResponse(BASE_DIR / "frontend" / "onboarding.html")


# ── Onboarding state ──────────────────────────────────────────────────────────

ONBOARDING_FILE = DATA_DIR / "onboarding.json"

def _read_onboarding():
    if ONBOARDING_FILE.exists():
        try:
            return json.loads(ONBOARDING_FILE.read_text())
        except Exception:
            pass
    return {"step": 1, "completed_at": None}

def _write_onboarding(state):
    ONBOARDING_FILE.write_text(json.dumps(state, indent=2))


@app.get("/api/onboarding/state")
async def onboarding_state():
    """Return current onboarding step and completion timestamp."""
    return _read_onboarding()


@app.post("/api/onboarding/advance")
async def onboarding_advance(req: Request):
    """Advance to the next onboarding step or mark as complete."""
    body = await req.json()
    step = body.get("step", 1)
    completed = body.get("completed", False)
    state = _read_onboarding()
    state["step"] = step
    if completed:
        from datetime import datetime
        state["completed_at"] = datetime.utcnow().isoformat() + "Z"
    _write_onboarding(state)
    return state


@app.get("/api/onboarding/ghl-check")
async def onboarding_ghl_check():
    """Check whether a valid GHL location is already configured."""
    location_id = os.getenv("TITAN_LOCATION_ID", "")
    token_file  = DATA_DIR / "social_tokens.json"
    has_token   = False
    if token_file.exists():
        try:
            tokens = json.loads(token_file.read_text())
            has_token = bool(tokens.get("ghl", {}).get("access_token"))
        except Exception:
            pass
    return {
        "connected": bool(location_id),
        "has_token": has_token,
        "location_id": location_id[:8] + "…" if location_id else None,
    }


# ── Walk-through data ─────────────────────────────────────────────────────────

@app.get("/api/brick/walkthrough")
async def brick_walkthrough():
    """
    Return walk-through data: greeting, stats, pending punch list, recent actions.
    Upserts permit row if first visit.
    """
    from db.engine import async_session as _async_session
    from db.models import (
        BrickAction as _BA, BrickMessage as _BM,
        BrickPermit as _BP, BrickTrackRecord as _BTR,
    )
    from config import get_current_location_id
    from sqlalchemy import select as _select, and_ as _and, text as _sa_text
    import uuid as _uuid
    from datetime import datetime as _dt

    location_id = get_current_location_id()
    loc_uuid = _uuid.UUID(location_id)

    async with _async_session() as session:
        # Upsert permit
        from services.brick_agent import BrickAgent as _BA_cls
        _agent = _BA_cls()
        permit = await _agent._get_or_create_permit(session, location_id)
        await session.commit()

        # Latest greeting from brick_messages
        greeting_row = (await session.execute(
            _select(_BM)
            .where(_and(_BM.location_id == loc_uuid, _BM.context_screen == "walkthrough"))
            .order_by(_BM.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        greeting = greeting_row.content if greeting_row else "Morning. Walk-through ready."

        # Pending punch list
        pending_rows = (await session.execute(
            _select(_BA)
            .where(_and(_BA.location_id == loc_uuid, _BA.status == "pending"))
            .order_by(_BA.requested_at.desc())
            .limit(20)
        )).scalars().all()

        # Dedup by rationale text — multiple planning runs can produce identical items
        _seen_rationales = set()
        pending_actions = []
        for a in pending_rows:
            rat = (a.rationale or "").strip()
            if rat in _seen_rationales:
                continue
            _seen_rationales.add(rat)
            pending_actions.append({
                "id": str(a.id),
                "action_type": a.action_type,
                "rationale": rat,
                "payload": a.payload or {},
                "requested_at": a.requested_at.isoformat() if a.requested_at else None,
            })

        # Recent actions (last 24 hours only — "Built Overnight" section)
        cutoff_24h = _dt.utcnow() - __import__('datetime').timedelta(hours=24)
        recent_rows = (await session.execute(
            _select(_BTR)
            .where(_and(
                _BTR.location_id == loc_uuid,
                _BTR.executed_at >= cutoff_24h,
            ))
            .order_by(_BTR.executed_at.desc())
            .limit(10)
        )).scalars().all()

        recent_actions = [
            {
                "action_type": r.action_type,
                "outcome": r.outcome,
                "executed_at": r.executed_at.isoformat() if r.executed_at else None,
            }
            for r in recent_rows
        ]

        # Stats
        now = _dt.utcnow()
        posts_mtd_r = await session.execute(_sa_text(
            "SELECT COUNT(*) FROM posts WHERE location_id = :loc "
            "AND created_at >= date_trunc('month', now())"
        ).bindparams(loc=loc_uuid))
        posts_mtd = posts_mtd_r.scalar() or 0

        upcoming_r = await session.execute(_sa_text(
            "SELECT COUNT(*) FROM posts WHERE location_id = :loc "
            "AND status = 'scheduled' AND scheduled_at > now()"
        ).bindparams(loc=loc_uuid))
        upcoming = upcoming_r.scalar() or 0

        score_r = await session.execute(_sa_text(
            "SELECT score FROM foundation_scores WHERE location_id = :loc "
            "ORDER BY computed_at DESC LIMIT 1"
        ).bindparams(loc=loc_uuid))
        score_row = score_r.fetchone()
        # Return None when score has never been computed — not 0% (misleading)
        foundation_score = round(score_row[0] * 100, 1) if (score_row and score_row[0] is not None) else None

        samples_r = await session.execute(_sa_text(
            "SELECT COUNT(*) FROM voice_samples WHERE location_id = :loc AND excluded = false"
        ).bindparams(loc=loc_uuid))
        foundation_samples = samples_r.scalar() or 0

    return JSONResponse({
        "greeting": greeting,
        "permit_tier": permit.current_tier,
        "posts_mtd": posts_mtd,
        "upcoming_posts": upcoming,
        "foundation_score": foundation_score,
        "foundation_samples": foundation_samples,
        "pending_actions": pending_actions,
        "recent_actions": recent_actions,
    })


# ── Punch list approve / reject ───────────────────────────────────────────────

@app.post("/api/brick/actions/{action_id}/approve")
async def brick_approve_action(action_id: str):
    """Approve a punch list item and execute it."""
    from services.brick_agent import BrickAgent as _BA_cls
    from config import get_current_location_id
    import uuid as _uuid

    # Phase 3A: single-tenant — use a sentinel user_id from location_id
    location_id = get_current_location_id()
    agent = _BA_cls()
    try:
        result = await agent.approve_action(action_id, location_id)
        return JSONResponse({"ok": True, "result": result})
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/brick/actions/{action_id}/reject")
async def brick_reject_action(action_id: str, request: Request):
    """Reject a punch list item with optional reason."""
    from services.brick_agent import BrickAgent as _BA_cls
    from config import get_current_location_id

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    location_id = get_current_location_id()
    reason = body.get("reason") or None
    agent = _BA_cls()
    try:
        result = await agent.reject_action(action_id, location_id, reason=reason)
        return JSONResponse(result)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ── Brick actions list ────────────────────────────────────────────────────────

@app.get("/api/brick/actions")
async def brick_list_actions():
    """List pending punch list actions for the current location."""
    from db.engine import async_session as _async_session
    from db.models import BrickAction as _BA
    from config import get_current_location_id
    from sqlalchemy import select as _select, and_ as _and
    import uuid as _uuid

    loc_uuid = _uuid.UUID(get_current_location_id())
    async with _async_session() as session:
        rows = (await session.execute(
            _select(_BA)
            .where(_and(_BA.location_id == loc_uuid, _BA.status == "pending"))
            .order_by(_BA.requested_at.desc())
        )).scalars().all()

    return JSONResponse({
        "actions": [
            {
                "id": str(a.id),
                "action_type": a.action_type,
                "rationale": a.rationale or "",
                "payload": a.payload or {},
                "requested_at": a.requested_at.isoformat() if a.requested_at else None,
                "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            }
            for a in rows
        ]
    })


# ── Permit ────────────────────────────────────────────────────────────────────

@app.get("/api/brick/permit")
async def brick_get_permit():
    """Return current permit tier + track record stats."""
    from db.engine import async_session as _async_session
    from db.models import BrickTrackRecord as _BTR
    from services.brick_agent import BrickAgent as _BA_cls
    from config import get_current_location_id
    from sqlalchemy import select as _select, func as _func, and_ as _and
    import uuid as _uuid

    location_id = get_current_location_id()
    loc_uuid = _uuid.UUID(location_id)
    agent = _BA_cls()

    async with _async_session() as session:
        permit = await agent._get_or_create_permit(session, location_id)
        await session.commit()

        # Track record stats
        total_r = await session.execute(
            _select(_func.count()).select_from(_BTR)
            .where(_BTR.location_id == loc_uuid)
        )
        total = total_r.scalar() or 0

        success_r = await session.execute(
            _select(_func.count()).select_from(_BTR)
            .where(_and(_BTR.location_id == loc_uuid, _BTR.outcome == "success"))
        )
        success_count = success_r.scalar() or 0

        rejected_r = await session.execute(
            _select(_func.count()).select_from(_BTR)
            .where(_and(_BTR.location_id == loc_uuid, _BTR.outcome == "rejected"))
        )
        rejected_count = rejected_r.scalar() or 0

    success_rate = round((success_count / total * 100), 1) if total > 0 else 0.0

    return JSONResponse({
        "current_tier": permit.current_tier,
        "promoted_at": permit.promoted_at.isoformat() if permit.promoted_at else None,
        "track_record": {
            "total_actions": total,
            "success_count": success_count,
            "rejected_count": rejected_count,
            "success_rate": success_rate,
        },
    })


@app.post("/api/brick/permit/promote")
async def brick_promote():
    """Advance Brick's permit tier one step."""
    from services.brick_agent import BrickAgent as _BA_cls
    from config import get_current_location_id

    location_id = get_current_location_id()
    agent = _BA_cls()
    result = await agent.promote(location_id, location_id)
    return JSONResponse(result)


@app.post("/api/brick/permit/demote")
async def brick_demote():
    """Reduce Brick's permit tier one step."""
    from services.brick_agent import BrickAgent as _BA_cls
    from config import get_current_location_id

    location_id = get_current_location_id()
    agent = _BA_cls()
    result = await agent.demote(location_id, location_id)
    return JSONResponse(result)


# ── Memory CRUD ───────────────────────────────────────────────────────────────

@app.post("/api/brick/memory")
async def brick_create_memory(request: Request):
    """Create a standing instruction (brick_memory row)."""
    from services.brick_agent import BrickAgent as _BA_cls
    from config import get_current_location_id

    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "content is required"}, status_code=422)

    category = body.get("category") or None
    location_id = get_current_location_id()
    agent = _BA_cls()
    memory_id = await agent.remember(location_id, content, category=category)
    return JSONResponse({"id": memory_id, "ok": True})


@app.get("/api/brick/memory")
async def brick_list_memory():
    """List all active standing instructions for the current location."""
    from services.brick_agent import BrickAgent as _BA_cls
    from config import get_current_location_id

    location_id = get_current_location_id()
    agent = _BA_cls()
    memories = await agent.get_active_memories(location_id)
    return JSONResponse({"memories": memories})


@app.delete("/api/brick/memory/{memory_id}")
async def brick_delete_memory(memory_id: str):
    """Soft-delete a standing instruction (sets active=False)."""
    from services.brick_agent import BrickAgent as _BA_cls

    agent = _BA_cls()
    found = await agent.forget(memory_id)
    if not found:
        return JSONResponse({"error": "memory not found"}, status_code=404)
    return JSONResponse({"ok": True, "memory_id": memory_id})


# ── Manual planning trigger (Gate 9 verification + debugging) ─────────────────

@app.post("/api/brick/run-planning")
async def brick_run_planning():
    """
    Manually trigger Brick's daily planning run.
    Used for Gate 9 verification and debugging — not called by the cron.
    Returns summary of what was planned.
    """
    from services.brick_agent import run_planning_for_default_location

    result = await run_planning_for_default_location()
    return JSONResponse({"ok": True, **result})


# ── Brick chat — Phase 3B ─────────────────────────────────────────────────────

@app.get("/brick-chat.js")
async def brick_chat_js():
    """Serve the Brick floating chat widget."""
    from fastapi.responses import FileResponse
    return FileResponse(BASE_DIR / "frontend" / "brick-chat.js", media_type="application/javascript")


@app.get("/api/brick/messages")
async def brick_get_messages(limit: int = 50):
    """Return recent conversation messages, oldest first."""
    from config import get_current_location_id
    from services.brick_agent import BrickAgent as _BA

    location_id = get_current_location_id()
    agent = _BA()
    messages = await agent.list_messages(location_id, limit=limit)
    return JSONResponse({"messages": messages})


@app.post("/api/brick/chat")
async def brick_chat(request: Request):
    """
    SSE streaming endpoint for Brick's conversational chat.

    Accepts JSON: {message, context_screen, context_data?}
    Yields SSE:
      data: {"t": "token"}\\n\\n   — text token
      data: {"tool": "desc"}\\n\\n — tool call notification
      data: [DONE]\\n\\n           — stream complete
    """
    from fastapi.responses import StreamingResponse
    from config import get_current_location_id
    from services.brick_agent import BrickAgent as _BA
    import json as _json

    body = await request.json()
    message = (body.get("message") or "").strip()
    context_screen = body.get("context_screen") or "unknown"
    context_data = body.get("context_data") or {}

    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    location_id = get_current_location_id()

    # Enrich context_data with live DB lookups
    try:
        from db.engine import async_session as _async_session
        from db.models import BrickAction as _BAct, Post as _Post
        from sqlalchemy import select as _sel, and_ as _and, func as _func
        from datetime import datetime as _dt, timedelta as _td
        import uuid as _uuid

        loc_uuid = _uuid.UUID(location_id)
        async with _async_session() as session:
            # Pending punch list
            pending = (await session.execute(
                _sel(_BAct)
                .where(_and(_BAct.location_id == loc_uuid, _BAct.status == "pending"))
                .limit(5)
            )).scalars().all()
            context_data["pending_actions"] = [
                {"action_type": a.action_type, "rationale": a.rationale or ""}
                for a in pending
            ]

            # Foundation sample count
            from sqlalchemy import text as _txt
            row = (await session.execute(
                _txt("SELECT COUNT(*) FROM voice_samples WHERE location_id = :loc"),
                {"loc": str(location_id)},
            )).one_or_none()
            context_data["foundation_samples"] = row[0] if row else 0

        context_data["screen"] = context_screen
    except Exception as _ctx_err:
        import logging as _log
        _log.getLogger("brick.chat").warning("Context load failed: %s", _ctx_err)

    agent = _BA()

    async def event_gen():
        try:
            async for chunk in agent.chat_stream(
                message=message,
                location_id=location_id,
                context_screen=context_screen,
                context_data=context_data,
            ):
                yield chunk
        except Exception as exc:
            import logging as _log
            _log.getLogger("brick.chat").error("SSE generator error: %s", exc)
            yield f"data: {_json.dumps({'t': 'Signal lost. Try again.'})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
