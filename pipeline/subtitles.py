"""
Caption style engine.

Generates ASS subtitle files from Whisper word timestamps in one of a set of
named presets (clean corporate → word-by-word high energy), then burns them
into video with ffmpeg/libass.

A caption style is a small dict, stored on the project (and optionally per
clip):  {"preset": "bold", "accent": "#FFD700", "position": "bottom"}
Every field is optional; missing/unknown values fall back to the defaults, so
`generate_ass_subtitles(words, w, h, path)` with no style keeps working.

Fonts are macOS system fonts (Impact, Avenir Next, Futura, Helvetica Neue,
Didot, American Typewriter, Menlo). libass falls back to a default sans if one
is missing, so a render never fails on a font.
"""

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional


# ── Presets ──────────────────────────────────────────────────────────────────
#
# mode:
#   highlight — phrase on screen, the spoken word changes (colour/pop/box/glow)
#   karaoke   — phrase on screen, colour sweeps across each word as it's said
#   reveal    — words appear one by one as they're said (typewriter)
#   one_word  — a single big word at a time
#   static    — whole phrase, no per-word change (subtitle look)
# active:      how the spoken word is marked — color | pop | box | glow | none
# upcoming:    how not-yet-spoken words look — normal | dim
# box:         phrase background — none | dark | accent
# size:        font size as a fraction of frame width
# case:        upper | sentence
# entrance:    none | pop | fade | slide

PRESETS: Dict[str, dict] = {
    # Restrained
    "clean": dict(
        label="Clean", family="Restrained", font="Helvetica Neue", bold=True,
        size=0.058, case="sentence", max_words=6, mode="static", active="none",
        upcoming="normal", box="none", outline=2, shadow=3, entrance="fade",
        desc="Quiet white subtitles with a soft shadow.",
    ),
    "executive": dict(
        label="Executive", family="Restrained", font="Avenir Next Demi Bold", bold=False,
        size=0.050, case="sentence", max_words=6, mode="static", active="none",
        upcoming="normal", box="dark", outline=0, shadow=0, entrance="fade",
        desc="White text on a dark bar. Corporate and calm.",
    ),
    # Balanced
    "bold": dict(
        label="Bold", family="Balanced", font="Avenir Next Heavy", bold=True,
        size=0.078, case="upper", max_words=3, mode="highlight", active="color",
        upcoming="normal", box="none", outline=6, shadow=2, entrance="none",
        desc="Big white caps, spoken word in your accent colour.",
    ),
    "karaoke": dict(
        label="Karaoke", family="Balanced", font="Avenir Next Heavy", bold=True,
        size=0.066, case="sentence", max_words=5, mode="karaoke", active="color",
        upcoming="normal", box="none", outline=5, shadow=2, entrance="none",
        desc="Colour sweeps across each word as it's said.",
    ),
    "pop": dict(
        label="Pop", family="Balanced", font="Avenir Next Heavy", bold=True,
        size=0.078, case="upper", max_words=3, mode="highlight", active="pop",
        upcoming="normal", box="none", outline=6, shadow=2, entrance="pop",
        desc="Spoken word grows and lights up.",
    ),
    "highlight_box": dict(
        label="Highlight Box", family="Balanced", font="Avenir Next Heavy", bold=True,
        size=0.072, case="upper", max_words=3, mode="highlight", active="box",
        upcoming="normal", box="none", outline=5, shadow=0, entrance="none",
        desc="A colour block slides under the spoken word.",
    ),
    # High energy
    "punch": dict(
        label="Punch", family="High energy", font="Impact", bold=False,
        size=0.100, case="upper", max_words=2, mode="highlight", active="pop",
        upcoming="normal", box="none", outline=8, shadow=4, entrance="pop",
        desc="Huge two-word hits with a thick stroke. Built for Shorts.",
    ),
    "one_word": dict(
        label="One Word", family="High energy", font="Impact", bold=False,
        size=0.130, case="upper", max_words=1, mode="one_word", active="color",
        upcoming="normal", box="none", outline=9, shadow=4, entrance="pop",
        desc="One giant word at a time, punched in.",
    ),
    "sticker": dict(
        label="Sticker", family="High energy", font="Futura", bold=True,
        size=0.070, case="upper", max_words=3, mode="highlight", active="none",
        upcoming="normal", box="accent", outline=0, shadow=0, entrance="pop",
        desc="Dark text on a tilted accent-colour sticker.",
    ),
    "glow": dict(
        label="Glow", family="High energy", font="Futura", bold=True,
        size=0.074, case="upper", max_words=3, mode="highlight", active="glow",
        upcoming="dim", box="none", outline=3, shadow=0, entrance="fade",
        desc="Spoken word glows in your accent colour.",
    ),
    # Refined
    "editorial": dict(
        label="Editorial", family="Refined", font="Didot", bold=True, italic=True,
        size=0.072, case="sentence", max_words=5, mode="highlight", active="none",
        upcoming="dim", box="none", outline=1, shadow=3, entrance="fade",
        desc="Elegant serif italics, words brighten as they're said.",
    ),
    # Techy
    "typewriter": dict(
        label="Typewriter", family="Techy", font="American Typewriter", bold=True,
        size=0.060, case="sentence", max_words=5, mode="reveal", active="color",
        upcoming="normal", box="dark", outline=0, shadow=0, entrance="none",
        desc="Words type on as they're spoken.",
    ),
}

DEFAULT_PRESET = "bold"
DEFAULT_ACCENT = "#FFD700"
POSITIONS = ("bottom", "middle", "top")


def list_presets() -> List[dict]:
    """Preset catalogue for the UI (no internal knobs)."""
    return [
        {"id": k, "label": p["label"], "family": p["family"], "description": p["desc"]}
        for k, p in PRESETS.items()
    ]


def normalize_style(style: Optional[dict]) -> dict:
    """Return a complete, valid style dict from partial/unknown input."""
    style = style or {}
    preset = style.get("preset") if style.get("preset") in PRESETS else DEFAULT_PRESET
    accent = style.get("accent") or DEFAULT_ACCENT
    if not re.fullmatch(r"#?[0-9A-Fa-f]{6}", str(accent)):
        accent = DEFAULT_ACCENT
    if not accent.startswith("#"):
        accent = "#" + accent
    position = style.get("position") if style.get("position") in POSITIONS else "bottom"
    return {"preset": preset, "accent": accent.upper(), "position": position}


# ── ASS helpers ──────────────────────────────────────────────────────────────

def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB → &HAABBGGRR (ASS is alpha+BGR)."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ink_for(hex_rgb: str) -> str:
    """Black or white text, whichever reads better on the given colour."""
    h = hex_rgb.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"


_WHITE = "#FFFFFF"
_BLACK = "#000000"


def _ts(secs: float) -> str:
    """Convert seconds to ASS timestamp H:MM:SS.cc"""
    cs  = int(round(max(0.0, secs) * 100))
    h   = cs // 360000;  cs %= 360000
    m   = cs // 6000;    cs %= 6000
    s   = cs // 100;     cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean_word(word: str, case: str) -> str:
    w = (word or "").strip()
    # ASS treats { } as override blocks and \ as an escape.
    w = w.replace("{", "(").replace("}", ")").replace("\\", "/")
    if case == "upper":
        w = w.upper().strip(",.;:")
    return w


def _group_words(words: list, max_words: int = 3) -> List[list]:
    """
    Group words into on-screen phrases.
    Split on pauses > 0.4s, sentence-ending punctuation, or every max_words.
    """
    groups: List[list] = []
    current: list = []
    for i, w in enumerate(words):
        current.append(w)
        gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 999
        sentence_end = (w.get("word") or "").strip()[-1:] in (".", "!", "?")
        if len(current) >= max_words or gap > 0.4 or sentence_end:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _ass_header(width: int, height: int, p: dict, style: dict) -> str:
    landscape = width > height
    base = min(width, height) if landscape else width
    font_size = max(28, int(base * p["size"] * (0.75 if landscape else 1.0)))
    accent = style["accent"]

    position = style["position"]
    if position == "middle":
        alignment, margin_v = 5, 0
    elif position == "top":
        alignment, margin_v = 8, int(height * 0.12)
    else:
        # Vertical video: sit above the Shorts/Reels/TikTok caption + button UI.
        alignment, margin_v = 2, int(height * (0.08 if landscape else 0.22))
    margin_lr = int(width * 0.08)

    primary = _ass_color(_WHITE)
    secondary = _ass_color(_WHITE)
    outline_c = _ass_color(_BLACK)
    back_c = _ass_color(_BLACK, 0x80)
    border_style = 1
    outline = p["outline"]
    shadow = p["shadow"]

    if p["mode"] == "karaoke":
        # \kf sweeps from SecondaryColour (unsaid) to PrimaryColour (said).
        primary, secondary = _ass_color(accent), _ass_color(_WHITE)
    if p["box"] == "dark":
        border_style, outline, shadow = 3, max(10, int(font_size * 0.28)), 0
        outline_c = _ass_color(_BLACK, 0x50)
        back_c = _ass_color(_BLACK, 0xFF)
    elif p["box"] == "accent":
        border_style, outline, shadow = 3, max(12, int(font_size * 0.30)), 0
        primary = secondary = _ass_color(_ink_for(accent))
        outline_c = _ass_color(accent)
        back_c = _ass_color(_BLACK, 0xFF)

    bold = -1 if p.get("bold") else 0
    italic = -1 if p.get("italic") else 0

    box_style = ""
    if p["active"] == "box":
        # Second, opaque-box style drawn on a layer above the outlined phrase.
        # Every word on that layer is invisible except the spoken one, so the
        # accent box sits only behind the active word.
        pad = max(8, int(font_size * 0.16))
        box_style = (
            f"\nStyle: CapBox,{p['font']},{font_size},{_ass_color(_ink_for(accent))},"
            f"{_ass_color(_ink_for(accent))},{_ass_color(accent)},{_ass_color(_BLACK, 0xFF)},"
            f"{bold},{italic},0,0,100,100,0,0,3,{pad},0,{alignment},{margin_lr},{margin_lr},{margin_v},1"
        )

    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{p["font"]},{font_size},{primary},{secondary},{outline_c},{back_c},{bold},{italic},0,0,100,100,0,0,{border_style},{outline},{shadow},{alignment},{margin_lr},{margin_lr},{margin_v},1{box_style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _entrance_tag(p: dict) -> str:
    e = p["entrance"]
    if e == "pop":
        return r"{\fscx80\fscy80\t(0,120,\fscx100\fscy100)}"
    if e == "fade":
        return r"{\fad(120,0)}"
    return ""


def _word_markup(p: dict, accent: str, state: str, text: str, animate: bool) -> str:
    """Override tags for one word. state: past | active | upcoming."""
    white = _ass_color(_WHITE)
    acc = _ass_color(accent)

    if state == "upcoming":
        if p["upcoming"] == "dim":
            return r"{\alpha&H80&}" + text + r"{\alpha&H00&}"
        return text

    if state == "past" or p["active"] == "none":
        if p["mode"] == "reveal" and state == "active":
            return "{\\c" + acc + "&}" + text + "{\\c" + white + "&}"
        return text

    a = p["active"]
    if a == "color":
        return "{\\c" + acc + "&}" + text + "{\\c" + white + "&}"
    if a == "pop":
        grow = r"\fscx100\fscy100\t(0,90,\fscx118\fscy118)" if animate else r"\fscx118\fscy118"
        return "{\\c" + acc + "&" + grow + "}" + text + "{\\c" + white + r"&\fscx100\fscy100}"
    if a == "glow":
        return ("{\\c" + white + "&\\3c" + acc + "&\\bord5\\blur6\\4c" + _ass_color(_BLACK) +
                "&\\shad5}" + text + "{\\c" + white + "&\\3c" + _ass_color(_BLACK) + "&\\bord" +
                str(p["outline"]) + "\\blur0\\shad" + str(p["shadow"]) + "}")
    return text


def generate_ass_subtitles(
    words: list,
    video_width: int,
    video_height: int,
    output_path: str,
    style: Optional[dict] = None,
) -> str:
    """
    Generate an ASS caption file in the given style (see PRESETS).
    `style` = {"preset", "accent", "position"}; None → the Bold default.
    Returns output_path.
    """
    style = normalize_style(style)
    p = PRESETS[style["preset"]]
    accent = style["accent"]
    header = _ass_header(video_width, video_height, p, style)

    words = [w for w in (words or []) if (w.get("word") or "").strip()]
    if not words:
        Path(output_path).write_text(header, encoding="utf-8")
        return output_path

    lines = [header]
    sticker_tilt = r"{\frz-3}" if p["box"] == "accent" else ""

    for group in _group_words(words, p["max_words"]):
        g_start = group[0]["start"]
        g_end = max(group[-1]["end"], g_start + 0.2)
        texts = [_clean_word(w["word"], p["case"]) for w in group]
        entrance = _entrance_tag(p)

        no_word_change = (p["mode"] == "highlight" and p["active"] == "none"
                          and p["upcoming"] == "normal")
        if p["mode"] == "static" or no_word_change:
            # One event for the whole phrase.
            lines.append(
                f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Cap,,0,0,0,,"
                f"{entrance}{sticker_tilt}{' '.join(texts)}"
            )
            continue

        if p["mode"] == "karaoke":
            parts = []
            for i, w in enumerate(group):
                nxt = group[i + 1]["start"] if i + 1 < len(group) else g_end
                dur_cs = max(1, int(round((nxt - w["start"]) * 100)))
                parts.append(f"{{\\kf{dur_cs}}}{texts[i]}")
            lines.append(
                f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Cap,,0,0,0,,{entrance}{' '.join(parts)}"
            )
            continue

        if p["mode"] == "one_word":
            for i, w in enumerate(group):
                w_end = max(w["end"], w["start"] + 0.25)
                lines.append(
                    f"Dialogue: 0,{_ts(w['start'])},{_ts(w_end)},Cap,,0,0,0,,"
                    f"{entrance}{_word_markup(p, accent, 'active', texts[i], True)}"
                )
            continue

        # highlight / reveal: one event per spoken word, back-to-back so the
        # phrase never flickers. Each event redraws the phrase with that word
        # active, earlier words past and later words upcoming.
        for i, w in enumerate(group):
            ev_start = g_start if i == 0 else w["start"]
            ev_end = group[i + 1]["start"] if i + 1 < len(group) else g_end
            if ev_end <= ev_start:
                ev_end = ev_start + 0.05
            lead = (entrance if i == 0 else "") + sticker_tilt
            if p["active"] == "box":
                # Layer 0: the outlined phrase. Layer 1: only the spoken word,
                # in ink on its accent box (the rest of the line invisible but
                # still taking space, so the box lands exactly on the word).
                hidden = r"{\alpha&HFF&}"
                shown = r"{\alpha&H00&}"
                boxed = " ".join(
                    (shown + texts[j] + hidden) if j == i else texts[j]
                    for j in range(len(group))
                )
                lines.append(
                    f"Dialogue: 0,{_ts(ev_start)},{_ts(ev_end)},Cap,,0,0,0,,{lead}{' '.join(texts)}"
                )
                lines.append(
                    f"Dialogue: 1,{_ts(ev_start)},{_ts(ev_end)},CapBox,,0,0,0,,{lead}{hidden}{boxed}"
                )
                continue
            if p["mode"] == "reveal":
                # Spoken words visible; the rest (and the spaces between them)
                # fully transparent so the line doesn't jump as it types on.
                said = " ".join(
                    _word_markup(p, accent, "active" if j == i else "past", texts[j], True)
                    for j in range(i + 1)
                )
                rest = " ".join(texts[i + 1:])
                body = said + ((r"{\alpha&HFF&} " + rest) if rest else "")
            else:
                body = " ".join(
                    _word_markup(p, accent,
                                 "past" if j < i else "active" if j == i else "upcoming",
                                 texts[j], animate=True)
                    for j in range(len(group))
                )
            lines.append(
                f"Dialogue: 0,{_ts(ev_start)},{_ts(ev_end)},Cap,,0,0,0,,{lead}{body}"
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
