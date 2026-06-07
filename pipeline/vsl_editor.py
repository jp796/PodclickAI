"""
vsl_editor.py — VSL Auto-Editor for PodClick

Parses a VSL script with timing markers and renders the source video
with graphics overlays using FFmpeg filter_complex:
  • Section lower-third bars at each [MM:SS — SECTION] marker
  • CTA pricing card overlay at the designated CTA timestamp
  • Sign-off card at the final timestamp
  • Word-level captions from the script body text

Usage:
    segments = parse_vsl_script(script_text)
    render_vsl(source_video, segments, output_path, style=VSL_STYLE_BOLD)
"""

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# ── Style presets ──────────────────────────────────────────────────────────────
VSL_STYLE_BOLD = {
    "lower_third_bg":   "black@0.75",
    "lower_third_h":    80,
    "section_color":    "f07030",   # orange
    "section_size":     28,
    "caption_color":    "white",
    "caption_size":     38,
    "caption_bg":       "black@0.55",
    "cta_bg":           "black@0.88",
    "cta_accent":       "f07030",
    "font":             "Arial",
}


def _ts_to_sec(ts: str) -> float:
    """Convert MM:SS to seconds."""
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def parse_vsl_script(script: str) -> dict:
    """
    Parse a VSL script into a structured edit plan.

    Extracts:
      sections  — list of {start_sec, end_sec, label, delivery_note}
      cta       — {start_sec, lines: [...]} or None
      signoff   — {start_sec, lines: [...]} or None
      captions  — list of {start_sec, end_sec, text} (estimated from word count)

    Section marker format: [MM:SS — LABEL · OPTIONAL DELIVERY NOTE]
    CTA detection: section label contains 'CTA'
    Sign-off detection: section label contains 'SIGN' or 'SIGN-OFF'
    """
    sections = []
    cta = None
    signoff = None

    # Find all [MM:SS — LABEL] markers
    marker_re = re.compile(
        r'\[(\d+:\d+)\s*[—\-]\s*([A-Z0-9\s\-/]+?)(?:\s*·\s*(.+?))?\]',
        re.IGNORECASE,
    )
    markers = list(marker_re.finditer(script))

    for i, m in enumerate(markers):
        ts_str, label, note = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
        start = _ts_to_sec(ts_str)
        end = _ts_to_sec(markers[i + 1].group(1)) if i + 1 < len(markers) else start + 30

        sec = {"start_sec": start, "end_sec": end, "label": label, "delivery_note": note}

        if "CTA" in label.upper():
            # Extract pricing lines from the block following this marker
            block_start = m.end()
            block_end   = markers[i + 1].start() if i + 1 < len(markers) else len(script)
            block = script[block_start:block_end]
            pricing_re = re.compile(r'(?:Solo|Teams?|Brokerage|White.label)[^\n]+', re.IGNORECASE)
            lines = pricing_re.findall(block)
            if not lines:
                # Try finding dollar amount lines
                lines = [l.strip() for l in block.splitlines()
                         if "$" in l and l.strip() and not l.strip().startswith("[")]
            cta = {"start_sec": start, "end_sec": end, "lines": lines[:4]}

        elif any(k in label.upper() for k in ("SIGN-OFF", "SIGN OFF", "SIGNOFF")):
            block_start = m.end()
            block_end   = markers[i + 1].start() if i + 1 < len(markers) else len(script)
            block = script[block_start:block_end]
            lines = [l.strip() for l in block.splitlines()
                     if l.strip() and not l.strip().startswith("[")][:4]
            signoff = {"start_sec": start, "end_sec": end, "lines": lines}

        sections.append(sec)

    # Build word-level caption segments (estimated at 150wpm = 2.5 words/sec)
    captions = _build_captions(script, sections)

    return {
        "sections": sections,
        "cta":      cta,
        "signoff":  signoff,
        "captions": captions,
    }


def _build_captions(script: str, sections: list) -> list:
    """
    Build phrase-level caption segments timed against the section blocks.
    Groups words into 4-6 word phrases and distributes them over the section duration.
    """
    captions = []
    # Strip delivery notes and markers for clean text
    clean = re.sub(r'\[[^\]]+\]', '', script)
    clean = re.sub(r'═+', '', clean)
    clean = re.sub(r'\n{3,}', '\n\n', clean)

    for sec in sections:
        # Extract text for this section block
        start, end = sec["start_sec"], sec["end_sec"]
        duration = max(end - start, 5)

        # Rough: split section text into phrases
        lines = [l.strip() for l in clean.split('\n\n') if l.strip()]
        # For now yield 2–3 key phrases per section spread across duration
        # This is a placeholder — real implementation uses Whisper word timestamps
        phrases = []
        for line in lines[:3]:
            words = line.split()
            for i in range(0, len(words), 5):
                phrases.append(" ".join(words[i:i+5]))

        if phrases:
            interval = duration / len(phrases)
            for j, phrase in enumerate(phrases):
                captions.append({
                    "start_sec": start + j * interval,
                    "end_sec":   start + (j + 1) * interval,
                    "text":      phrase,
                })

    return captions


def _esc(s: str) -> str:
    """Escape special chars for FFmpeg drawtext."""
    return s.replace("'", "\\'").replace(":", "\\:").replace("\\", "\\\\")


def render_vsl(
    source_video: str,
    plan: dict,
    output_path: str,
    style: Optional[dict] = None,
    width: int = 1920,
    height: int = 1080,
) -> str:
    """
    Render the VSL video with auto-generated graphic overlays.

    Overlays applied:
      1. Section lower-third bars (label + optional delivery hint)
      2. CTA pricing card (full-width translucent bar, 3 tier lines)
      3. Sign-off card
      4. Caption phrase overlays

    Returns output_path. Raises RuntimeError on FFmpeg failure.
    """
    s = style or VSL_STYLE_BOLD
    font = s["font"]
    sec_clr = s["section_color"]
    sections = plan.get("sections", [])
    cta      = plan.get("cta")
    signoff  = plan.get("signoff")
    captions = plan.get("captions", [])

    filters = []

    # ── 1. Section lower thirds ──────────────────────────────────────────────
    lth = s["lower_third_h"]
    for sec in sections:
        t0, t1 = sec["start_sec"], min(sec["start_sec"] + 4, sec["end_sec"])
        label = _esc(sec["label"].upper())
        cond  = f"between(t,{t0},{t1})"
        # Background bar
        filters.append(
            f"drawbox=y=h-{lth}:w=iw:h={lth}:color={s['lower_third_bg']}:t=fill:enable='{cond}'"
        )
        # Section label text
        filters.append(
            f"drawtext=text='{label}':fontfile=/System/Library/Fonts/Helvetica.ttc:"
            f"fontsize={s['section_size']}:fontcolor=0x{sec_clr}:"
            f"x=40:y=h-{lth//2}-{s['section_size']//2}:enable='{cond}'"
        )

    # ── 2. CTA pricing card ───────────────────────────────────────────────────
    if cta:
        t0, t1 = cta["start_sec"], cta["end_sec"]
        cond = f"between(t,{t0},{t1})"
        card_h = 200
        card_y = (height - card_h) // 2
        filters.append(
            f"drawbox=y={card_y}:w=iw:h={card_h}:color={s['cta_bg']}:t=fill:enable='{cond}'"
        )
        line_y = card_y + 20
        for i, line in enumerate(cta.get("lines", [])[:3]):
            yt = line_y + i * 50
            filters.append(
                f"drawtext=text='{_esc(line)}':fontfile=/System/Library/Fonts/Helvetica.ttc:"
                f"fontsize=34:fontcolor=white:x=(w-text_w)/2:y={yt}:enable='{cond}'"
            )

    # ── 3. Sign-off card ──────────────────────────────────────────────────────
    if signoff:
        t0, t1 = signoff["start_sec"], signoff["end_sec"]
        cond = f"between(t,{t0},{t1})"
        card_h = 140
        filters.append(
            f"drawbox=y=h-{card_h}:w=iw:h={card_h}:color=black@0.82:t=fill:enable='{cond}'"
        )
        for i, line in enumerate(signoff.get("lines", [])[:3]):
            yy = height - card_h + 20 + i * 38
            filters.append(
                f"drawtext=text='{_esc(line)}':fontfile=/System/Library/Fonts/Helvetica.ttc:"
                f"fontsize=30:fontcolor=0x{sec_clr}:x=(w-text_w)/2:y={yy}:enable='{cond}'"
            )

    # ── 4. Caption overlays ───────────────────────────────────────────────────
    for cap in captions:
        t0, t1 = cap["start_sec"], cap["end_sec"]
        text = _esc(cap["text"])
        cond = f"between(t,{t0},{t1})"
        filters.append(
            f"drawtext=text='{text}':fontfile=/System/Library/Fonts/Helvetica.ttc:"
            f"fontsize={s['caption_size']}:fontcolor={s['caption_color']}:"
            f"x=(w-text_w)/2:y=h*0.82:"
            f"box=1:boxcolor={s['caption_bg']}:boxborderw=8:"
            f"enable='{cond}'"
        )

    if not filters:
        # No overlays — just copy
        import shutil
        shutil.copy2(source_video, output_path)
        return output_path

    vf = ",".join(filters)
    cmd = [
        "ffmpeg", "-y", "-i", source_video,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"VSL render failed: {result.stderr[-600:]}")
    return output_path
