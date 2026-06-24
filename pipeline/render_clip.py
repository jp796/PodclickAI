"""
render_clip.py — Vertical 9:16 clip renderer for PodClick.

VENDORED into the PodClick repo (was ~/.claude/skills/vertical-clip-render/render_clip.py)
so the clip pipeline deploys with the app on a cloud server with no ~/.claude dependency.
Keep this in sync if the skill copy changes; pipeline/project_pipeline.py imports THIS first.

Handles two source orientations automatically:
  stacked    — speakers top/bottom (portrait content in landscape frame)
  side_by_side — speakers left/right (landscape content in landscape frame)

And four crop modes:
  split      — both speakers stacked vertically (guest/left top, host/right bottom)
  solo_top   — top speaker only OR left speaker only, fill-scaled to 9:16
  solo_bottom— bottom speaker only OR right speaker only, fill-scaled to 9:16
  center     — full content area, fill-scaled to 9:16 (wide / single-speaker shot)

Key invariant: EVERY output cell uses fill-not-pad scale:
  scale=W:H:force_original_aspect_ratio=increase,crop=W:H,setsar=1
This guarantees NO black bars in any mode.

Usage:
    layout = detect_layout(source_path, sample_time=10.0)
    render_clip(
        source=source_path, start=7*60+5, end=8*60+5,
        srt_content=srt_text, output=output_path,
        layout=layout, mode="split",
        out_w=1080, out_h=1920,
        ass_path="",   # viral word-highlight .ass; overrides srt when set
    )
"""

import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


CAPTION_STYLE = (
    "Fontname=Arial,FontSize=14,Bold=1,"
    "PrimaryColour=&H00FFFFFF&,"
    "OutlineColour=&H00000000&,Outline=3,Shadow=1,"
    "Alignment=2,MarginV=30"
)


@dataclass
class LayoutInfo:
    """Content bounds and speaker positions detected from source at a given timestamp."""
    source_w: int = 1920
    source_h: int = 1080
    content_x: int = 480
    content_y: int = 2
    content_w: int = 956
    content_h: int = 1076
    # For stacked (top/bottom): vertical split pixel offset from content_y
    v_split: int = 488
    # For side_by_side (left/right): horizontal split pixel offset from content_x
    h_split: int = 0
    # Detected orientation
    orientation: str = "stacked"   # "stacked" | "side_by_side"

    # ── Derived crop strings ───────────────────────────────────────────────
    @property
    def top_crop(self):
        """Stacked: top speaker. Side-by-side: left speaker (guest)."""
        if self.orientation == "side_by_side":
            w = self.h_split or self.content_w // 2
            return f"{w}:{self.content_h}:{self.content_x}:{self.content_y}"
        return f"{self.content_w}:{self.v_split}:{self.content_x}:{self.content_y}"

    @property
    def bot_crop(self):
        """Stacked: bottom speaker. Side-by-side: right speaker (host)."""
        if self.orientation == "side_by_side":
            w = self.h_split or self.content_w // 2
            x = self.content_x + w
            return f"{self.content_w - w}:{self.content_h}:{x}:{self.content_y}"
        y = self.content_y + self.v_split
        return f"{self.content_w}:{self.content_h - self.v_split}:{self.content_x}:{y}"

    @property
    def full_crop(self):
        return f"{self.content_w}:{self.content_h}:{self.content_x}:{self.content_y}"


def detect_layout(source: str, sample_time: float = 10.0) -> LayoutInfo:
    """
    Auto-detect content bounds, orientation (stacked vs side-by-side),
    and speaker split from source video at sample_time.

    Returns LayoutInfo. Raises RuntimeError on detection failure.
    """
    # ── cropdetect ────────────────────────────────────────────────────────
    cmd = [
        "ffmpeg", "-ss", str(sample_time), "-t", "2",
        "-i", source,
        "-vf", "cropdetect=limit=24:round=2:reset=1",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    crop_line = None
    for line in result.stderr.splitlines():
        if "crop=" in line and "Parsed_cropdetect" in line:
            crop_line = line
    if not crop_line:
        raise RuntimeError(f"cropdetect found no content in {source} at t={sample_time}")

    crop_str = crop_line.split("crop=")[-1].strip().split()[0]
    cw, ch, cx, cy = [int(v) for v in crop_str.split(":")]

    # ── Detect orientation ────────────────────────────────────────────────
    # stacked:      content is portrait-ish (h >> w) — two speakers top/bottom
    # side_by_side: content is landscape-ish (w >> h) — two speakers left/right
    ar = cw / max(ch, 1)
    orientation = "side_by_side" if ar > 1.5 else "stacked"

    # Source dimensions
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", source],
        capture_output=True, text=True,
    )
    sw, sh = 1920, 1080
    if probe.stdout.strip():
        parts = probe.stdout.strip().split(",")
        if len(parts) == 2:
            sw, sh = int(parts[0]), int(parts[1])

    layout = LayoutInfo(
        source_w=sw, source_h=sh,
        content_x=cx, content_y=cy,
        content_w=cw, content_h=ch,
        orientation=orientation,
    )

    if orientation == "stacked":
        # Scan vertical center column for the speaker gap
        center_x = cx + cw // 2
        raw_cmd = [
            "ffmpeg", "-y", "-ss", str(sample_time + 5), "-t", "0.5",
            "-i", source,
            "-frames:v", "1",
            "-vf", f"crop=40:{ch}:{center_x - 20}:{cy},scale=1:{ch},format=gray",
            "-f", "rawvideo", "pipe:1",
        ]
        raw_result = subprocess.run(raw_cmd, capture_output=True)
        pixel_data = raw_result.stdout
        v_split = ch // 2
        if len(pixel_data) >= ch:
            window = 8
            best_avg = 999
            for y in range(ch // 4, 3 * ch // 4):
                chunk = pixel_data[y: y + window]
                if len(chunk) < window:
                    continue
                avg = sum(chunk) / window
                if avg < best_avg:
                    best_avg = avg
                    v_split = y + window // 2
        layout.v_split = v_split

    else:
        # side_by_side: use exact midpoint — pixel scanning is unreliable
        # for Zoom/Meet side-by-side layouts where there's no clear dark gap.
        # The output is always top/bottom stacked (JP's standard for Shorts).
        layout.h_split = cw // 2

    return layout


def _fill_scale(w: int, h: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


def _build_filter(
    mode: str,
    layout: LayoutInfo,
    srt_path: str,
    out_w: int = 1080,
    out_h: int = 1920,
    ass_path: str = "",
) -> str:
    # Viral ASS captions (word-by-word highlight) when provided; else styled SRT.
    if ass_path and Path(ass_path).exists():
        cap = f"ass='{ass_path}'"
    else:
        cap = f"subtitles='{srt_path}':force_style='{CAPTION_STYLE}'"
    fill = _fill_scale(out_w, out_h)
    half_h = out_h // 2

    if mode == "split":
        top_fill = _fill_scale(out_w, half_h)
        bot_fill = _fill_scale(out_w, half_h)
        return (
            f"[0:v]crop={layout.top_crop},{top_fill}[top];"
            f"[0:v]crop={layout.bot_crop},{bot_fill}[bot];"
            f"[top][bot]vstack=inputs=2,{cap}[v]"
        )
    elif mode == "solo_top":
        return f"[0:v]crop={layout.top_crop},{fill},{cap}[v]"
    elif mode == "solo_bottom":
        return f"[0:v]crop={layout.bot_crop},{fill},{cap}[v]"
    else:  # center
        return f"[0:v]crop={layout.full_crop},{fill},{cap}[v]"


def render_clip(
    source: str,
    start: float,
    end: float,
    srt_content: str,
    output: str,
    layout: Optional[LayoutInfo] = None,
    mode: str = "split",
    out_w: int = 1080,
    out_h: int = 1920,
    verify: bool = False,
    ass_path: str = "",
) -> str:
    """
    Render a vertical 9:16 clip. Auto-detects layout if not provided.
    Falls back to detect_layout at mid-clip timestamp if layout is None.

    If ass_path is given (a viral word-highlight .ass file on disk), it is
    burned in place of the plain SRT captions.
    """
    if layout is None:
        layout = detect_layout(source, sample_time=max(start + 1, 10.0))

    duration = end - start

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        srt_path = str(tmp / "clip.srt")
        Path(srt_path).write_text(srt_content, encoding="utf-8")

        filter_complex = _build_filter(mode, layout, srt_path, out_w, out_h, ass_path)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start), "-t", str(duration),
            "-i", source,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            output,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"render_clip ({mode}/{layout.orientation}) failed:\n{result.stderr[-600:]}"
            )

    if verify:
        verify_path = output + ".verify.jpg"
        subprocess.run([
            "ffmpeg", "-y", "-ss", "2", "-i", output,
            "-frames:v", "1", verify_path,
        ], capture_output=True)
        print(f"[render_clip] verify frame: {verify_path}")

    return output
