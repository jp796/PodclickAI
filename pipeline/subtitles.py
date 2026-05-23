"""
Viral TikTok subtitle engine.
Generates ASS subtitle files with word-level yellow highlight animation,
then burns them into video with ffmpeg.

Style: 2025 CapCut-style — bold white text, yellow active word,
black stroke, centered at ~72% height.
"""

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional


# ── ASS colour format: &HAABBGGRR (alpha, blue, green, red) ──────────────────
_WHITE   = "&H00FFFFFF"
_YELLOW  = "&H0000D7FF"   # #FFD700 in BGR
_BLACK   = "&H00000000"
_TRANS   = "&HFF000000"   # fully transparent


def _ass_header(width: int, height: int) -> str:
    font_size = max(48, int(height * 0.040))   # ~72px at 1920h
    margin_v  = int(height * 0.08)             # bottom margin

    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 1
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: PodWhite,Arial,{font_size},{_WHITE},{_WHITE},{_BLACK},{_TRANS},-1,0,0,0,100,100,0,0,1,3,1,2,20,20,{margin_v},1
Style: PodYellow,Arial,{font_size},{_YELLOW},{_YELLOW},{_BLACK},{_TRANS},-1,0,0,0,100,100,0,0,1,3,1,2,20,20,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(secs: float) -> str:
    """Convert seconds to ASS timestamp H:MM:SS.cc"""
    cs  = int(round(secs * 100))
    h   = cs // 360000;  cs %= 360000
    m   = cs // 6000;    cs %= 6000
    s   = cs // 100;     cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _group_words(words: list) -> list[list]:
    """
    Group words into chunks of 2-3.
    Split on pauses > 0.4s OR every 3 words.
    """
    groups  = []
    current = []

    for i, w in enumerate(words):
        current.append(w)
        gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 999
        if len(current) >= 3 or gap > 0.4:
            groups.append(current)
            current = []

    if current:
        groups.append(current)

    return groups


def generate_ass_subtitles(
    words: list,
    video_width: int,
    video_height: int,
    output_path: str,
) -> str:
    """
    Generate an ASS subtitle file with viral TikTok word-highlight style.
    Each word in a group is highlighted yellow when spoken.
    Returns output_path.
    """
    if not words:
        Path(output_path).write_text(_ass_header(video_width, video_height))
        return output_path

    lines  = [_ass_header(video_width, video_height)]
    groups = _group_words(words)

    for group in groups:
        if not group:
            continue

        group_start = group[0]["start"]
        group_end   = group[-1]["end"]
        group_text  = "  ".join(w["word"].upper().strip() for w in group)

        # Background line — full group in white, shown for entire group duration
        lines.append(
            f"Dialogue: 0,{_ts(group_start)},{_ts(group_end)},PodWhite,,0,0,0,,{group_text}"
        )

        # Highlight line — one per word, yellow, shown only while that word is spoken
        for w in group:
            w_text  = w["word"].upper().strip()
            w_start = w["start"]
            w_end   = w["end"] if w["end"] > w_start else w_start + 0.15

            # Build the group with only this word in yellow, rest in white
            highlighted = "  ".join(
                f"{{\\c{_YELLOW}&}}{x['word'].upper().strip()}{{\\c{_WHITE}&}}"
                if x is w else x["word"].upper().strip()
                for x in group
            )

            lines.append(
                f"Dialogue: 1,{_ts(w_start)},{_ts(w_end)},PodYellow,,0,0,0,,{highlighted}"
            )

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    return output_path


# ── Burn subtitles into video ─────────────────────────────────────────────────

def burn_subtitles(
    video_path: str,
    ass_path: str,
    output_path: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    def log(msg):
        if progress_cb: progress_cb(msg)

    log("Burning subtitles into video…")

    # Escape colons and backslashes in the path for the ffmpeg filter
    safe_ass = ass_path.replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"ass={safe_ass}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]

    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
    time_re = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    last_pct = -1
    stderr_buf = []

    # Get video duration for progress
    dur_cmd = [
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", video_path,
    ]
    try:
        dur_out = subprocess.run(dur_cmd, capture_output=True, text=True)
        total_dur = float(dur_out.stdout.strip() or "0")
    except Exception:
        total_dur = 0.0

    for line in proc.stderr:
        stderr_buf.append(line)
        m = time_re.search(line)
        if m and total_dur > 0:
            h, mm, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
            elapsed = h * 3600 + mm * 60 + s
            pct = min(99, int(elapsed / total_dur * 100))
            if pct != last_pct and pct % 10 == 0:
                log(f"Burning subtitles… {pct}%")
                last_pct = pct

    proc.wait()
    if proc.returncode != 0:
        # Surface the actual ffmpeg error instead of just the exit code
        error_lines = [l.strip() for l in stderr_buf if any(k in l for k in ("Error", "error", "Invalid", "No option", "Unknown", "failed"))]
        detail = " | ".join(error_lines[-3:]) if error_lines else "".join(stderr_buf[-5:]).strip()
        if not detail:
            detail = f"(no stderr captured — run ffmpeg manually to diagnose)"
        raise RuntimeError(f"ffmpeg subtitle burn failed (code {proc.returncode}): {detail}")

    log("Subtitles burned ✓")
    return output_path


# ── Full per-clip subtitle pipeline ──────────────────────────────────────────

async def process_clip_with_subtitles(
    clip_dict: dict,
    all_words: list,
    output_dir: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    loop = asyncio.get_event_loop()

    clip_start = clip_dict["start"]
    clip_end   = clip_dict["end"]
    idx        = clip_dict.get("index", 1)

    # Filter words within this clip, offset timestamps to clip-relative time
    clip_words = [
        {**w, "start": w["start"] - clip_start, "end": w["end"] - clip_start}
        for w in all_words
        if w["start"] >= clip_start and w["end"] <= clip_end
    ]

    # Get video dimensions
    dim_cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        clip_dict["raw_path"],
    ]
    try:
        dim_out = subprocess.run(dim_cmd, capture_output=True, text=True)
        w_str, h_str = dim_out.stdout.strip().split(",")
        vid_w, vid_h = int(w_str), int(h_str)
    except Exception:
        vid_w, vid_h = 1080, 1920

    ass_path = str(Path(output_dir) / f"clip_{idx}.ass")
    out_path = str(Path(output_dir) / f"clip_{idx}_final.mp4")

    # Generate ASS
    await loop.run_in_executor(
        None, generate_ass_subtitles, clip_words, vid_w, vid_h, ass_path
    )

    # Burn in
    final_path = await loop.run_in_executor(
        None, burn_subtitles, clip_dict["raw_path"], ass_path, out_path, progress_cb
    )

    return {**clip_dict, "subtitled_path": final_path, "ass_path": ass_path}
