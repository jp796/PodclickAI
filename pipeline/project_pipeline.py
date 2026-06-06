"""
Phase 5 — Project pipeline workers.

Each function is one step in the Ship It chain:

  select_sponsor()    — round-robin sponsor selection from sponsors.json
  detect_clips()      — extract clip candidates from transcript using existing scoring
  render_vertical_clip() — FFmpeg 9:16 crop + libass caption burn
  render_all_clips()  — render all clip candidates for a project
  generate_srt_for_clip() — build SRT from word timestamps for a clip window

These are sync functions (CPU/IO-bound) run via loop.run_in_executor() in the
async API layer, matching the pattern in run_pipeline().

Construction vocabulary:
  Clip candidates = "cut list" (what Brick proposes)
  Vertical 9:16 = the standard distribution format
  Closing = when the final post ships
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────

_BASE = Path(__file__).parent.parent
_DATA = _BASE / "data"
_SPONSORS_FILE = _DATA / "sponsors.json"
_SPONSOR_ROTATION_FILE = _DATA / "sponsor_rotation.json"   # tracks last-used index
_LIBRARY_FILE = _DATA / "library.json"                     # intro / outro / commercial assets
_CLIPS_DIR = _DATA / "project_clips"
_RECORDINGS_DIR = _DATA / "recordings"
_CLIPS_DIR.mkdir(exist_ok=True)
_RECORDINGS_DIR.mkdir(exist_ok=True)


# ── Sponsor round-robin ────────────────────────────────────────────────────────

def _load_sponsors() -> list:
    if not _SPONSORS_FILE.exists():
        return []
    with open(_SPONSORS_FILE) as f:
        return json.load(f)


def _save_sponsors(sponsors: list) -> None:
    with open(_SPONSORS_FILE, "w") as f:
        json.dump(sponsors, f, indent=2)


def _load_rotation() -> dict:
    if not _SPONSOR_ROTATION_FILE.exists():
        return {"last_index": -1}
    with open(_SPONSOR_ROTATION_FILE) as f:
        return json.load(f)


def _save_rotation(data: dict) -> None:
    with open(_SPONSOR_ROTATION_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_library() -> list:
    """Load the audio asset library (intros, outros, commercials)."""
    if not _LIBRARY_FILE.exists():
        return []
    with open(_LIBRARY_FILE) as f:
        return json.load(f)


def _panda_dash_fallback() -> Optional[dict]:
    """
    Return the Panda Dash affiliate commercial from library.json as a sponsor dict.

    Panda Dash is the always-on fallback: JP's recorded affiliate ad used whenever
    no paid sponsor has an audio_url. Never returns None if library.json has a
    slot_type='commercial' entry with a valid path.
    """
    library = _load_library()
    for item in library:
        if item.get("slot_type") == "commercial":
            path = item.get("path", "")
            if path and Path(path).exists():
                return {
                    "id": item["id"],
                    "company": item.get("label", "Panda Dash"),
                    "name": item.get("label", "Panda Dash"),
                    "audio_url": path,
                    "affiliate_url": "",
                    "episodes_count": 0,
                    "_source": "library_fallback",
                }
    return None


def select_sponsor() -> Optional[dict]:
    """
    Round-robin selection from active sponsors with real audio assets.

    Selection rules (in priority order):
      1. Filter sponsors.json to status='active' AND audio_url IS NOT NULL/empty.
         sponsors.json is a PROSPECT DIRECTORY — most records have no audio_url yet.
         Only records with a recorded commercial are eligible for selection.
      2. From eligible sponsors, sort by episodes_count ASC (least-used first).
      3. Increment the selected sponsor's episodes_count and persist.
      4. If zero eligible paid sponsors: fall back to Panda Dash commercial from
         library.json (JP's affiliate ad — the always-on default).

    Returns sponsor dict (with audio_url populated) or None only if both the
    paid pool is empty AND the Panda Dash library fallback is unavailable.
    """
    sponsors = _load_sponsors()
    # Only select sponsors that have a real audio file assigned
    active = [
        s for s in sponsors
        if s.get("status") == "active" and s.get("audio_url")
    ]

    if active:
        # Sort by episodes_count ascending — lowest count = most due
        active.sort(key=lambda s: s.get("episodes_count", 0))
        selected = active[0]

        # Increment episodes_count in the source file
        for s in sponsors:
            if s["id"] == selected["id"]:
                s["episodes_count"] = s.get("episodes_count", 0) + 1
                break

        _save_sponsors(sponsors)
        return selected

    # No paid sponsors with audio — fall back to Panda Dash affiliate commercial
    return _panda_dash_fallback()


# ── SRT generation ─────────────────────────────────────────────────────────────

def generate_srt_for_clip(
    words: list,
    start_sec: float,
    end_sec: float,
    max_chars_per_line: int = 32,
    max_lines_per_card: int = 2,
) -> str:
    """
    Generate SRT content for a clip window from word-level timestamps.

    Words outside [start_sec, end_sec] are excluded.
    Timestamps are offset-corrected so clip always starts at 00:00:00,000.
    Lines are grouped into cards of max_lines_per_card lines.
    """
    # Filter words in the clip window
    clip_words = [
        w for w in words
        if w.get("start", 0) >= start_sec and w.get("end", 0) <= end_sec
    ]
    if not clip_words:
        return ""

    def _fmt(sec: float) -> str:
        sec = max(0.0, sec - start_sec)
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int((sec - int(sec)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    # Group words into lines, then into cards
    lines: list[list[dict]] = []
    current_line: list[dict] = []
    current_len = 0

    for w in clip_words:
        word = w["word"].strip()
        if current_len + len(word) + 1 > max_chars_per_line and current_line:
            lines.append(current_line)
            current_line = [w]
            current_len = len(word)
        else:
            current_line.append(w)
            current_len += len(word) + 1

    if current_line:
        lines.append(current_line)

    # Group lines into cards
    cards: list[list[list[dict]]] = []
    for i in range(0, len(lines), max_lines_per_card):
        cards.append(lines[i:i + max_lines_per_card])

    # Build SRT
    srt_parts = []
    for idx, card in enumerate(cards, start=1):
        all_words = [w for line in card for w in line]
        card_start = all_words[0]["start"]
        card_end = all_words[-1]["end"]
        card_text = "\n".join(
            " ".join(w["word"].strip() for w in line)
            for line in card
        )
        srt_parts.append(
            f"{idx}\n{_fmt(card_start)} --> {_fmt(card_end)}\n{card_text}\n"
        )

    return "\n".join(srt_parts)


# ── Clip detection ─────────────────────────────────────────────────────────────

def detect_clips_for_project(
    words: list,
    segments: list,
    num_clips: int = 5,
    min_dur: float = 45.0,
    max_dur: float = 90.0,
) -> list:
    """
    Detect clip candidates from a transcript.

    Uses the existing score_and_select_clips() from pipeline/clip.py
    (power-word density + sentence boundary scoring).

    Returns list of dicts:
      {start, end, duration, transcript, score, hook_text}
    """
    from pipeline.clip import score_and_select_clips

    candidates = score_and_select_clips(
        segments=segments,
        words=words,
        num_clips=num_clips,
        min_dur=min_dur,
        max_dur=max_dur,
    )

    # Add hook_text = first sentence of clip transcript
    for c in candidates:
        text = c.get("transcript", "")
        # First sentence = text up to first period/exclamation/question or 10 words
        first_sent = text
        for punct in (".", "!", "?"):
            pos = text.find(punct)
            if 0 < pos < 120:
                first_sent = text[:pos + 1]
                break
        if len(first_sent) > 120:
            words_list = first_sent.split()
            first_sent = " ".join(words_list[:10]) + "..."
        c["hook_text"] = first_sent.strip()

    return candidates


# ── Vertical 9:16 clip render ──────────────────────────────────────────────────

# Caption style constants — shared across both render paths.
#
# libass font size math:
#   Default PlayResY = 288 (libass internal script resolution)
#   Scale factor     = output_height / PlayResY = 1920 / 288 = 6.667
#   FontSize=14  → 14 × 6.667 ≈ 93px on-screen (~4.8% of 1920)  ← Submagic-size
#   FontSize=48  → 48 × 6.667 ≈ 320px on-screen (~16.7%)        ← was the bug
#
# MarginV math (Alignment=2 = bottom-center, margin from bottom):
#   MarginV=30  → 30 × 6.667 ≈ 200px from bottom                ← good Shorts position
#   MarginV=200 → 200 × 6.667 ≈ 1333px from bottom  (near top!) ← was the bug
_CAPTION_STYLE = (
    "Fontname=Arial,FontSize=14,Bold=1,"
    "PrimaryColour=&H00FFFFFF&,"            # white text
    "OutlineColour=&H00000000&,Outline=3,"  # black outline for readability
    "Shadow=1,"
    "Alignment=2,MarginV=30"               # bottom-center, 200px from bottom
)


def render_vertical_clip(
    source_mp3: str,
    start_sec: float,
    end_sec: float,
    srt_content: str,
    output_path: str,
    background_color: str = "black",
    width: int = 1080,
    height: int = 1920,
) -> str:
    """
    Render a vertical 9:16 clip from an MP3 source (audio-only fallback).

    Used when no video source is available. Creates a solid-color
    background with the trimmed audio and burned-in captions.

    Returns output_path on success. Raises RuntimeError on FFmpeg failure.
    """
    duration = end_sec - start_sec

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        srt_path = str(tmp / "clip.srt")
        with open(srt_path, "w") as f:
            f.write(srt_content)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec), "-t", str(duration),
            "-i", source_mp3,
            "-f", "lavfi",
            "-i", f"color=c={background_color}:s={width}x{height}:r=30",
            "-map", "1:v", "-map", "0:a",
            "-vf", f"subtitles='{srt_path}':force_style='{_CAPTION_STYLE}'",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg clip render (audio) failed: {result.stderr[-400:]}"
            )

    return output_path


def render_vertical_clip_from_video(
    source_video: str,
    start_sec: float,
    end_sec: float,
    srt_content: str,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
) -> str:
    """
    Render a 9:16 clip cut directly from the source video recording.

    The source is landscape (typically 1920×1080). We center-crop to 9:16
    and scale to 1080×1920 — standard Shorts/Reels/TikTok resolution.

    Crop formula (input-dimension-agnostic):
      crop_w = ih * (9/16)         → 9:16 width at full source height
      crop_x = (iw - crop_w) / 2   → centered horizontally
      → scale to 1080×1920

    Captions are burned in using the same libass style as the audio path,
    with timestamps rebased to 0 (generate_srt_for_clip already does this).

    Returns output_path on success. Raises RuntimeError on FFmpeg failure.
    """
    duration = end_sec - start_sec

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        srt_path = str(tmp / "clip.srt")
        with open(srt_path, "w") as f:
            f.write(srt_content)

        # Center-crop landscape → 9:16, scale to 1080×1920, burn captions
        vf = (
            f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={width}:{height},"
            f"subtitles='{srt_path}':force_style='{_CAPTION_STYLE}'"
        )
        cmd = [
            "ffmpeg", "-y",
            # Seek before -i for fast keyframe seek (input-side -ss)
            "-ss", str(start_sec), "-t", str(duration),
            "-i", source_video,
            "-vf", vf,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg clip render (video) failed: {result.stderr[-400:]}"
            )

    return output_path


def render_all_clips(
    project_id: str,
    source_mp3: str,
    words: list,
    clip_candidates: list,
    progress_cb=None,
    source_video: str = "",
) -> list:
    """
    Render all clip candidates for a project.

    Preferred path (when source_video is provided and exists on disk):
      render_vertical_clip_from_video() — cuts from the actual recording,
      center-crops 1920×1080 → 1080×1920, burns captions.

    Fallback path (audio-only):
      render_vertical_clip() — audio over solid black background.

    For each candidate:
      1. Generate SRT from word timestamps (rebased to 0)
      2. Render 9:16 MP4 to data/project_clips/{project_id}/clip_{n}.mp4
      3. Return list of {start, end, hook_text, rendered_url, srt_url, score}
    """
    out_dir = _CLIPS_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)

    use_video = bool(source_video and os.path.exists(source_video))

    def log(msg):
        if progress_cb:
            progress_cb(msg)

    if use_video:
        log(f"Clip render mode: video source (center-crop 9:16) from {Path(source_video).name}")
    else:
        log("Clip render mode: audio-over-black (no video source)")

    results = []
    for idx, candidate in enumerate(clip_candidates):
        start = candidate["start"]
        end = candidate["end"]
        clip_id = f"clip_{idx + 1:02d}"

        log(f"Rendering {clip_id} ({start:.0f}s–{end:.0f}s)…")

        try:
            srt_content = generate_srt_for_clip(words, start, end)
            srt_path = str(out_dir / f"{clip_id}.srt")
            with open(srt_path, "w") as f:
                f.write(srt_content)

            mp4_path = str(out_dir / f"{clip_id}.mp4")

            if use_video:
                try:
                    render_vertical_clip_from_video(
                        source_video=source_video,
                        start_sec=start,
                        end_sec=end,
                        srt_content=srt_content,
                        output_path=mp4_path,
                    )
                except RuntimeError as _vid_err:
                    log(f"  ⚠ {clip_id} video render failed ({_vid_err}) — falling back to audio-only")
                    render_vertical_clip(
                        source_mp3=source_mp3,
                        start_sec=start,
                        end_sec=end,
                        srt_content=srt_content,
                        output_path=mp4_path,
                    )
            else:
                render_vertical_clip(
                    source_mp3=source_mp3,
                    start_sec=start,
                    end_sec=end,
                    srt_content=srt_content,
                    output_path=mp4_path,
                )

            results.append({
                **candidate,
                "rendered_url": mp4_path,
                "srt_url": srt_path,
                "status": "rendered",
            })
            log(f"  ✓ {clip_id} rendered — {out_dir.name}/{clip_id}.mp4")

        except Exception as e:
            log(f"  ✗ {clip_id} render failed: {e}")
            results.append({
                **candidate,
                "rendered_url": None,
                "srt_url": None,
                "status": "failed",
                "error_reason": str(e),
            })

    return results


# ── Ship It orchestration ──────────────────────────────────────────────────────

def run_ship_it(
    project_id: str,
    job_data: dict,
    progress_cb=None,
    source_video: str = "",
) -> dict:
    """
    Full Ship It chain (sync, run via executor):

      a. select_sponsor → sponsor_placement (with Panda Dash fallback)
      b. detect_clips → clip candidates from transcript
      c. render_all_clips → 9:16 MP4s + SRTs
      d. assemble_episode → intro + main + commercial + outro → single MP3
      e. Return results dict for caller to persist to DB

    This does NOT call Foundation (async LLM calls for show notes +
    clip captions are done separately in async context).
    """
    def log(msg):
        if progress_cb:
            progress_cb(msg)

    result = {
        "sponsor_placement": None,
        "clip_candidates": [],
        "rendered_clips": [],
        "assembled_mp3": None,
        "errors": [],
    }

    # ── a. Sponsor round-robin ─────────────────────────────────────────────────
    log("Selecting sponsor via round-robin…")
    sponsor = None
    try:
        sponsor = select_sponsor()
        if sponsor:
            result["sponsor_placement"] = {
                "sponsor_id": sponsor["id"],
                "sponsor_name": sponsor.get("company") or sponsor.get("name", "Unknown"),
                "affiliate_url": sponsor.get("affiliate_url", ""),
                "audio_url": sponsor.get("audio_url", ""),
                "position_pct": 50,
                "assigned_at": datetime.now(timezone.utc).isoformat(),
                "_source": sponsor.get("_source", "sponsors_json"),
            }
            log(f"  Sponsor: {result['sponsor_placement']['sponsor_name']}")
        else:
            log("  No sponsor available — episode will have no commercial slot")
    except Exception as e:
        result["errors"].append(f"sponsor selection: {e}")
        log(f"  Sponsor selection error: {e}")

    # ── b. Clip detection ──────────────────────────────────────────────────────
    words = job_data.get("words", [])
    segments = job_data.get("segments", [])

    if words:
        log("Detecting clip candidates from transcript…")
        try:
            candidates = detect_clips_for_project(
                words=words,
                segments=segments,
                num_clips=5,
            )
            result["clip_candidates"] = candidates
            log(f"  {len(candidates)} clip candidates identified")
        except Exception as e:
            result["errors"].append(f"clip detection: {e}")
            log(f"  Clip detection error: {e}")
    else:
        log("  No word timestamps available — skipping clip detection")

    # ── c. Render clips ────────────────────────────────────────────────────────
    mp3_path = job_data.get("mp3_path")
    if result["clip_candidates"] and mp3_path and os.path.exists(mp3_path):
        log(f"Rendering {len(result['clip_candidates'])} clips as 9:16 vertical…")
        try:
            rendered = render_all_clips(
                project_id=project_id,
                source_mp3=mp3_path,
                words=words,
                clip_candidates=result["clip_candidates"],
                progress_cb=progress_cb,
                source_video=source_video,
            )
            result["rendered_clips"] = rendered
        except Exception as e:
            result["errors"].append(f"clip rendering: {e}")
            log(f"  Clip render error: {e}")
    else:
        log("  Skipping clip render (no MP3 or no candidates)")

    # ── d. Assemble episode: intro + main + commercial + outro ─────────────────
    if mp3_path and os.path.exists(mp3_path):
        log("Assembling finished episode (intro → main → commercial → outro)…")
        try:
            assembled = _assemble_episode(
                project_id=project_id,
                main_mp3=mp3_path,
                sponsor=result.get("sponsor_placement"),
                word_timestamps=words,
                log=log,
            )
            result["assembled_mp3"] = assembled
            if assembled:
                log(f"  ✓ Assembled episode: {assembled}")
            else:
                log("  Assembly skipped — no library assets found")
        except Exception as e:
            result["errors"].append(f"episode assembly: {e}")
            log(f"  Episode assembly error: {e}")
    else:
        log("  Skipping episode assembly (no main MP3 available)")

    return result


def _assemble_episode(
    project_id: str,
    main_mp3: str,
    sponsor: Optional[dict],
    word_timestamps: list,
    log,
) -> Optional[str]:
    """
    Assemble finished episode: intro + main + commercial + outro → single MP3.

    Loads intro and outro from library.json. Commercial comes from the selected
    sponsor's audio_url (which is the Panda Dash path when using the fallback).

    Returns the assembled MP3 path, or None if no library assets are available.
    Assembly failure raises RuntimeError (caller logs + continues gracefully).
    """
    from pipeline.assemble import assemble_clips

    library = _load_library()
    if not library:
        log("  No library assets found — skipping assembly")
        return None

    # Build slot lookup from library
    by_slot = {}
    for item in library:
        slot = item.get("slot_type")
        path = item.get("path", "")
        if slot and path and Path(path).exists():
            by_slot[slot] = path

    # Need at least a main track to assemble anything meaningful
    clips = []

    intro_path = by_slot.get("intro")
    if intro_path:
        clips.append({"path": intro_path, "type": "intro"})
        log(f"    intro: {Path(intro_path).name}")
    else:
        log("    no intro in library — assembling without intro")

    # Main track = the extracted studio recording MP3
    clips.append({"path": main_mp3, "type": "main"})
    log(f"    main: {Path(main_mp3).name}")

    # Commercial: use sponsor audio_url (Panda Dash fallback already resolves to library path)
    commercial_path = None
    if sponsor and sponsor.get("audio_url"):
        candidate = sponsor["audio_url"]
        if Path(candidate).exists():
            commercial_path = candidate
        else:
            # audio_url is a URL string, not a local path — try library fallback
            commercial_path = by_slot.get("commercial")
    else:
        commercial_path = by_slot.get("commercial")

    if commercial_path and Path(commercial_path).exists():
        clips.append({"path": commercial_path, "type": "commercial"})
        log(f"    commercial: {Path(commercial_path).name}")
    else:
        log("    no commercial available — assembling without ad slot")

    outro_path = by_slot.get("outro")
    if outro_path:
        clips.append({"path": outro_path, "type": "outro"})
        log(f"    outro: {Path(outro_path).name}")
    else:
        log("    no outro in library — assembling without outro")

    # Output path: data/recordings/{project_id}_assembled.mp3
    output_dir = str(_RECORDINGS_DIR)
    output_target = str(_RECORDINGS_DIR / f"{project_id}_assembled.mp3")

    # assemble_clips() writes to output_dir — we capture the returned mp3 path
    assembly_result = assemble_clips(
        clips=clips,
        output_dir=output_dir,
        word_timestamps=word_timestamps if word_timestamps else None,
    )

    assembled_path = assembly_result.get("output_mp3")
    if not assembled_path or not Path(assembled_path).exists():
        raise RuntimeError(
            f"assemble_clips() completed but output_mp3 is missing: {assembly_result}"
        )

    # Rename to the canonical project path so it's easy to locate
    final_path = Path(output_target)
    if Path(assembled_path) != final_path:
        import shutil
        shutil.move(assembled_path, final_path)
        assembled_path = str(final_path)

    return assembled_path
