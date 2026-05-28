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
_CLIPS_DIR = _DATA / "project_clips"
_CLIPS_DIR.mkdir(exist_ok=True)


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


def select_sponsor() -> Optional[dict]:
    """
    Round-robin selection from active sponsors.

    Returns the selected sponsor dict (with id, company, affiliate_url, etc.)
    or None if no active sponsors are configured.

    Round-robin algorithm:
      - Filter to status='active' sponsors
      - Sort by episodes_count ASC (lowest count first = most due for rotation)
      - Pick first (least-used active sponsor)
      - Increment that sponsor's episodes_count in sponsors.json
      - Return the sponsor record
    """
    sponsors = _load_sponsors()
    active = [s for s in sponsors if s.get("status") == "active"]

    if not active:
        return None

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
    Render a vertical 9:16 clip from an MP3 source.

    Since the source is audio-only (MP3), this creates a solid-color
    background video with the audio overlay and burned-in captions.

    Steps:
      1. Trim audio to [start_sec, end_sec]
      2. Create 1080×1920 solid-color background video at 30fps
      3. Overlay audio on the background
      4. Burn captions via libass from SRT file
      5. Output MP4 to output_path

    Returns output_path on success. Raises RuntimeError on FFmpeg failure.
    """
    duration = end_sec - start_sec

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Write SRT file
        srt_path = str(tmp / "clip.srt")
        with open(srt_path, "w") as f:
            f.write(srt_content)

        # Build FFmpeg command:
        # - lavfi color source for background
        # - amovie input for audio trim
        # - subtitles filter for caption burn
        cmd = [
            "ffmpeg", "-y",
            # 1. Audio source trimmed from MP3
            "-ss", str(start_sec), "-t", str(duration),
            "-i", source_mp3,
            # 2. Solid background video
            "-f", "lavfi",
            "-i", f"color=c={background_color}:s={width}x{height}:r=30",
            # 3. Map: background video [1] + audio [0]
            "-map", "1:v", "-map", "0:a",
            # 4. Burn subtitles
            "-vf", f"subtitles='{srt_path}':force_style='Fontname=Arial,FontSize=48,Bold=1,"
                   f"PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,Outline=2,"
                   f"Alignment=2,MarginV=200'",
            # 5. Shorten to actual audio duration
            "-t", str(duration),
            # 6. Encoding
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            # 7. Output
            output_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg clip render failed (start={start_sec:.1f}s end={end_sec:.1f}s): "
                f"{result.stderr[-400:]}"
            )

    return output_path


def render_all_clips(
    project_id: str,
    source_mp3: str,
    words: list,
    clip_candidates: list,
    progress_cb=None,
) -> list:
    """
    Render all clip candidates for a project.

    For each candidate:
      1. Generate SRT from word timestamps
      2. Render 9:16 MP4 to data/project_clips/{project_id}/clip_{n}.mp4
      3. Return list of {start, end, hook_text, rendered_url, srt_url, score}

    Returns updated candidates list with rendered_url and srt_url populated.
    """
    out_dir = _CLIPS_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)

    def log(msg):
        if progress_cb:
            progress_cb(msg)

    results = []
    for idx, candidate in enumerate(clip_candidates):
        start = candidate["start"]
        end = candidate["end"]
        clip_id = f"clip_{idx + 1:02d}"

        log(f"Rendering {clip_id} ({start:.0f}s–{end:.0f}s)…")

        try:
            # Generate SRT
            srt_content = generate_srt_for_clip(words, start, end)
            srt_path = str(out_dir / f"{clip_id}.srt")
            with open(srt_path, "w") as f:
                f.write(srt_content)

            # Render MP4
            mp4_path = str(out_dir / f"{clip_id}.mp4")
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
                "status": "pending",  # stays pending so retry is possible
            })

    return results


# ── Ship It orchestration ──────────────────────────────────────────────────────

def run_ship_it(
    project_id: str,
    job_data: dict,
    progress_cb=None,
) -> dict:
    """
    Full Ship It chain (sync, run via executor):

      a. select_sponsor → sponsor_placement
      b. detect_clips → clip candidates from transcript
      c. render_all_clips → 9:16 MP4s + SRTs
      d. Return results dict for caller to persist to DB

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
        "errors": [],
    }

    # ── a. Sponsor round-robin ─────────────────────────────────────────────────
    log("Selecting sponsor via round-robin…")
    try:
        sponsor = select_sponsor()
        if sponsor:
            result["sponsor_placement"] = {
                "sponsor_id": sponsor["id"],
                "sponsor_name": sponsor.get("company") or sponsor.get("name", "Unknown"),
                "affiliate_url": sponsor.get("affiliate_url", ""),
                "position_pct": 50,
                "assigned_at": datetime.now(timezone.utc).isoformat(),
            }
            log(f"  Sponsor: {result['sponsor_placement']['sponsor_name']}")
        else:
            log("  No active sponsors — skipping sponsor placement")
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
            )
            result["rendered_clips"] = rendered
        except Exception as e:
            result["errors"].append(f"clip rendering: {e}")
            log(f"  Clip render error: {e}")
    else:
        log("  Skipping clip render (no MP3 or no candidates)")

    return result
