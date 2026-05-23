"""
pipeline/broll.py

B-roll auto-editor for PodClickAI.

Flow:
  1. plan_broll()   — GPT-4o reads transcript segments, returns timed B-roll slots
                       with Pexels search queries (city-specific when applicable)
  2. fetch_clips()  — Downloads best-fit Pexels video clips per slot
  3. composite()    — ffmpeg: swaps video track at each slot, keeps full audio

City detection: Springfield, MO / Cheyenne, WY trigger city-specific queries.
Strategy: keep face cam for hook (first 30 s) and final 15 s; cutaway ~50% of middle.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# City keyword detection
# ---------------------------------------------------------------------------

CITY_TRIGGERS: dict[str, str] = {
    r"\bspringfield\b": "Springfield Missouri",
    r"\bcheyenne\b":    "Cheyenne Wyoming",
    r"\bmissouri\b":    "Springfield Missouri",
    r"\bwyoming\b":     "Cheyenne Wyoming",
    r"\bmo\b":          "Springfield Missouri",
    r"\bwy\b":          "Cheyenne Wyoming",
}


def _detect_city(text: str) -> Optional[str]:
    """Return city string if text contains a city mention, else None."""
    t = text.lower()
    for pattern, city in CITY_TRIGGERS.items():
        if re.search(pattern, t):
            return city
    return None


# ---------------------------------------------------------------------------
# Step 1: Plan B-roll slots via GPT-4o
# ---------------------------------------------------------------------------

def plan_broll(
    segments: list[dict],   # Whisper segments: [{start, end, text}, ...]
    openai_key: str,
    total_duration: float = 0.0,
    progress_cb=None,
) -> list[dict]:
    """
    Returns a list of B-roll slots:
      [{"start": float, "end": float, "query": str, "is_city": bool}, ...]

    Slots are 8-20 s wide, skipping the opening 30 s (hook) and
    the last 15 s (CTA/sign-off).
    """
    if not segments:
        return []

    if progress_cb:
        progress_cb("Planning B-roll segments…")

    # Build compact transcript for GPT
    lines = []
    for s in segments:
        lines.append(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text'].strip()}")
    transcript = "\n".join(lines)

    vid_end = total_duration or (segments[-1]["end"] if segments else 120)

    prompt = f"""You are a video editor AI. Given a timestamped transcript, identify segments that should show B-roll cutaway footage while the speaker's voice continues.

Rules:
- Skip the first 30 seconds (keep face cam for the hook).
- Skip the final 15 seconds (keep face cam for CTA/sign-off).
- Target 40-60% of the remaining middle content for B-roll.
- Each B-roll slot should be 8-20 seconds long.
- Prefer informational/explanatory moments, not emotional or personal ones.
- If Springfield, Cheyenne, Missouri, or Wyoming is mentioned in a segment, set is_city=true and use a city-specific query.
- Queries should be concrete and visual (e.g. "Springfield Missouri downtown aerial", "real estate agent showing house", "couple moving boxes into home", "neighborhood street suburb").
- Avoid overlapping slots.

Total video duration: {vid_end:.1f} seconds.

Transcript:
{transcript}

Return ONLY a JSON array of objects with keys: start (float), end (float), query (string), is_city (boolean).
Example: [{{"start": 32.5, "end": 48.0, "query": "real estate agent showing house interior", "is_city": false}}]"""

    try:
        import openai
        client = openai.OpenAI(api_key=openai_key)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        raw = resp.choices[0].message.content
        parsed = json.loads(raw)
        # GPT sometimes wraps in {"slots": [...]} or {"broll": [...]}
        if isinstance(parsed, dict):
            parsed = next((v for v in parsed.values() if isinstance(v, list)), [])
        slots = [
            {
                "start": float(s.get("start", 0)),
                "end":   float(s.get("end", 0)),
                "query": str(s.get("query", "real estate neighborhood")),
                "is_city": bool(s.get("is_city", False)),
            }
            for s in parsed
            if s.get("end", 0) > s.get("start", 0)
        ]
        # Filter: must be inside video, min 5 s
        slots = [
            s for s in slots
            if s["end"] - s["start"] >= 5 and s["start"] >= 28 and s["end"] <= vid_end - 10
        ]
        return slots
    except Exception as exc:
        print(f"[broll] plan_broll failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Step 2: Fetch Pexels clips
# ---------------------------------------------------------------------------

PEXELS_VIDEO_SEARCH = "https://api.pexels.com/videos/search"


def _pexels_search(query: str, pexels_key: str, min_duration: int = 8) -> Optional[str]:
    """
    Search Pexels for a video clip matching query.
    Returns a download URL for the best SD/HD file, or None.
    """
    params = f"query={urllib.parse.quote(query)}&per_page=8&orientation=landscape&size=medium"
    url = f"{PEXELS_VIDEO_SEARCH}?{params}"
    req = urllib.request.Request(url, headers={"Authorization": pexels_key})
    try:
        import urllib.parse
        params = f"query={urllib.parse.quote(query)}&per_page=8&orientation=landscape&size=medium"
        url = f"{PEXELS_VIDEO_SEARCH}?{params}"
        req = urllib.request.Request(url, headers={"Authorization": pexels_key})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        videos = data.get("videos", [])
        if not videos:
            return None
        # Pick first video with duration >= min_duration
        for vid in videos:
            if vid.get("duration", 0) >= min_duration:
                # Prefer HD, fall back to SD
                files = sorted(vid.get("video_files", []), key=lambda f: f.get("width", 0), reverse=True)
                for f in files:
                    if f.get("width", 0) <= 1920 and f.get("file_type") == "video/mp4":
                        return f["link"]
        # Fallback: first available mp4
        for vid in videos:
            for f in vid.get("video_files", []):
                if f.get("file_type") == "video/mp4":
                    return f["link"]
    except Exception as exc:
        print(f"[broll] pexels_search failed for '{query}': {exc}")
    return None


def _download_clip(url: str, dest: str) -> bool:
    """Download a video file to dest. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PodClickAI/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        return os.path.getsize(dest) > 10_000
    except Exception as exc:
        print(f"[broll] download_clip failed: {exc}")
        return False


def fetch_clips(
    slots: list[dict],
    pexels_key: str,
    cache_dir: str,
    progress_cb=None,
) -> list[dict]:
    """
    For each slot, fetch a Pexels clip.
    Returns slots with "clip_path" added (slots without a clip are dropped).
    """
    import urllib.parse  # ensure available

    os.makedirs(cache_dir, exist_ok=True)
    result = []

    for i, slot in enumerate(slots):
        query = slot["query"]
        duration = int(slot["end"] - slot["start"])

        if progress_cb:
            progress_cb(f"Fetching B-roll clip {i+1}/{len(slots)}: {query[:40]}…")

        clip_url = _pexels_search(query, pexels_key, min_duration=max(5, duration - 3))

        # Fallback: try a simpler query if city-specific fails
        if not clip_url and slot.get("is_city"):
            fallback = query.split()[-2] + " city aerial"  # e.g. "Missouri city aerial"
            clip_url = _pexels_search(fallback, pexels_key, min_duration=5)

        # Final fallback: generic real estate
        if not clip_url:
            clip_url = _pexels_search("real estate neighborhood street", pexels_key, min_duration=5)

        if not clip_url:
            print(f"[broll] no clip found for slot {i+1}, skipping")
            continue

        dest = os.path.join(cache_dir, f"broll_{i:03d}.mp4")
        if _download_clip(clip_url, dest):
            result.append({**slot, "clip_path": dest})
        else:
            print(f"[broll] download failed for slot {i+1}, skipping")

    return result


# ---------------------------------------------------------------------------
# Step 3: Composite B-roll into main video
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 600) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    )
    return proc.returncode, proc.stdout, proc.stderr


def _get_duration(path: str) -> float:
    code, out, _ = _run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", path
    ])
    try:
        return float(json.loads(out)["format"]["duration"])
    except Exception:
        return 0.0


def _get_video_dims(path: str) -> tuple[int, int]:
    """Return (width, height) of first video stream."""
    code, out, _ = _run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", path
    ])
    try:
        s = json.loads(out)["streams"][0]
        return s["width"], s["height"]
    except Exception:
        return 1920, 1080


def composite(
    main_video: str,
    slots_with_clips: list[dict],   # output of fetch_clips()
    output_path: str,
    progress_cb=None,
) -> bool:
    """
    Build the final video: face cam everywhere except at B-roll slots,
    where the Pexels clip replaces the video track (audio stays from main).

    Strategy: build a concat list of trimmed segments (face or B-roll),
    then merge with the full main audio via a single ffmpeg pass.
    """
    if not slots_with_clips:
        # No B-roll — just copy
        code, _, _ = _run(["ffmpeg", "-y", "-i", main_video, "-c", "copy", output_path])
        return code == 0

    if progress_cb:
        progress_cb("Compositing B-roll into video…")

    total_dur = _get_duration(main_video)
    w, h = _get_video_dims(main_video)

    # Sort slots by start time; merge any overlaps
    slots = sorted(slots_with_clips, key=lambda s: s["start"])
    merged: list[dict] = []
    for s in slots:
        if merged and s["start"] < merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], s["end"])
        else:
            merged.append(dict(s))

    # Build timeline: alternating face and broll segments
    # Each entry: {"type": "face"|"broll", "start": float, "end": float, "clip_path": str|None}
    timeline = []
    cursor = 0.0
    for slot in merged:
        if slot["start"] > cursor:
            timeline.append({"type": "face", "start": cursor, "end": slot["start"], "clip_path": None})
        timeline.append({"type": "broll", "start": slot["start"], "end": slot["end"], "clip_path": slot["clip_path"]})
        cursor = slot["end"]
    if cursor < total_dur:
        timeline.append({"type": "face", "start": cursor, "end": total_dur, "clip_path": None})

    # ── Build ffmpeg filter_complex ──────────────────────────────────────────
    # Inputs: [0] main video, [1..N] B-roll clips
    inputs = ["-i", main_video]
    broll_input_idx = {}  # clip_path -> input index

    for seg in timeline:
        if seg["type"] == "broll" and seg["clip_path"] not in broll_input_idx:
            idx = len(broll_input_idx) + 1
            broll_input_idx[seg["clip_path"]] = idx
            inputs += ["-i", seg["clip_path"]]

    filter_parts = []
    seg_labels = []

    for i, seg in enumerate(timeline):
        label = f"v{i}"
        dur = seg["end"] - seg["start"]
        if seg["type"] == "face":
            # Trim main video segment
            filter_parts.append(
                f"[0:v]trim=start={seg['start']:.3f}:end={seg['end']:.3f},"
                f"setpts=PTS-STARTPTS[{label}]"
            )
        else:
            # Trim B-roll clip, scale to match main video
            inp = broll_input_idx[seg["clip_path"]]
            broll_dur = _get_duration(seg["clip_path"])
            # Loop if clip is shorter than slot; trim to needed duration
            loop_times = max(1, int(dur / broll_dur) + 2) if broll_dur > 0 else 1
            loop_filter = f"[{inp}:v]loop={loop_times}:size=32767:start=0," if loop_times > 1 else f"[{inp}:v]"
            trim_filter = (
                f"{loop_filter if loop_times > 1 else f'[{inp}:v]'}"
                f"trim=duration={dur:.3f},setpts=PTS-STARTPTS,"
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2[{label}]"
            )
            if loop_times > 1:
                # Reassemble with proper chaining
                trim_filter = (
                    f"[{inp}:v]loop={loop_times}:size=32767:start=0,"
                    f"trim=duration={dur:.3f},setpts=PTS-STARTPTS,"
                    f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2[{label}]"
                )
            else:
                trim_filter = (
                    f"[{inp}:v]trim=duration={dur:.3f},setpts=PTS-STARTPTS,"
                    f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2[{label}]"
                )
            filter_parts.append(trim_filter)
        seg_labels.append(f"[{label}]")

    n = len(seg_labels)
    concat_filter = "".join(seg_labels) + f"concat=n={n}:v=1:a=0[vout]"
    filter_parts.append(concat_filter)
    filter_complex = ";".join(filter_parts)

    cmd = (
        inputs
        + ["-filter_complex", filter_complex]
        + ["-map", "[vout]", "-map", "0:a"]
        + ["-c:v", "libx264", "-crf", "20", "-preset", "fast"]
        + ["-c:a", "aac", "-b:a", "192k"]
        + [output_path]
    )
    cmd = ["ffmpeg", "-y"] + cmd

    code, _, err = _run(cmd, timeout=1800)
    if code != 0:
        print(f"[broll] composite failed:\n{err[-1000:]}")
        return False
    return True


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def add_broll(
    main_video: str,
    segments: list[dict],
    output_path: str,
    openai_key: str,
    pexels_key: str,
    cache_dir: str,
    progress_cb=None,
) -> str:
    """
    Full B-roll pipeline.
    Returns output_path on success, main_video on failure (non-fatal).
    """
    if not pexels_key:
        if progress_cb:
            progress_cb("Skipping B-roll — PEXELS_API_KEY not set")
        return main_video

    try:
        total_dur = _get_duration(main_video)

        slots = plan_broll(segments, openai_key, total_dur, progress_cb)
        if not slots:
            if progress_cb:
                progress_cb("No B-roll slots planned — keeping face cam only")
            return main_video

        slots_with_clips = fetch_clips(slots, pexels_key, cache_dir, progress_cb)
        if not slots_with_clips:
            if progress_cb:
                progress_cb("B-roll clips unavailable — keeping face cam only")
            return main_video

        ok = composite(main_video, slots_with_clips, output_path, progress_cb)
        if ok:
            if progress_cb:
                progress_cb(f"B-roll composite complete — {len(slots_with_clips)} cutaways added")
            return output_path
        else:
            if progress_cb:
                progress_cb("B-roll composite failed — using original video")
            return main_video

    except Exception as exc:
        print(f"[broll] add_broll exception: {exc}")
        if progress_cb:
            progress_cb(f"B-roll skipped (error: {exc})")
        return main_video
