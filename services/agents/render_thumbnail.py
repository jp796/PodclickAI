"""
Thumbnail compositor for the Painter agent (AGENTS_HUB_SPEC §1.4).

SYNC, CPU-bound PIL work — call it through run_in_executor. It turns one Cover
Forge concept into a real 1280x720 PNG: a background plate (an image a provider
generated, or a gradient built from the concept's own colors), the persona photo
filling the left third, and 3-5 words of bold outlined text on the right.

The colors are data from the concept, not new UI colors. Fonts follow the
episode-poster builder in main.py (macOS Arial Black → Arial Bold → PIL default).
"""
from typing import Optional, Tuple

W, H = 1280, 720
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def parse_color(value: Optional[str], fallback: Tuple[int, int, int]) -> Tuple[int, int, int]:
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return fallback
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def _font(size: int):
    from PIL import ImageFont
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _gradient(base: Tuple[int, int, int]):
    from PIL import Image
    top = tuple(min(255, int(c * 1.25) + 12) for c in base)
    bottom = tuple(int(c * 0.45) for c in base)
    column = Image.new("RGB", (1, H), base)
    for y in range(H):
        t = y / (H - 1)
        column.putpixel((0, y), tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    return column.resize((W, H))


def _cover(img, w: int, h: int):
    from PIL import ImageOps
    return ImageOps.fit(img.convert("RGB"), (w, h), centering=(0.5, 0.3))


def _wrap(draw, words, font, max_w: int):
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=font) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def render_thumbnail(out_path: str, text: str, background_color: Optional[str] = None,
                     text_color: Optional[str] = None, persona_path: Optional[str] = None,
                     plate_path: Optional[str] = None) -> str:
    """Write a 1280x720 PNG to out_path and return the path."""
    from PIL import Image, ImageDraw

    base = parse_color(background_color, (13, 27, 42))
    ink = parse_color(text_color, (255, 255, 255))

    plate = None
    if plate_path:
        try:
            plate = _cover(Image.open(plate_path), W, H)
        except Exception:
            plate = None
    canvas = plate or _gradient(base)

    text_left = 60
    if persona_path:
        try:
            face = _cover(Image.open(persona_path), W // 3 + 60, H)
            canvas.paste(face, (0, 0))
            text_left = W // 3 + 100
        except Exception:
            text_left = 60

    draw = ImageDraw.Draw(canvas)
    words = (str(text or "").upper().split() or ["WATCH THIS"])[:6]
    max_w = W - text_left - 50
    size = 150
    while size > 40:
        font = _font(size)
        lines = _wrap(draw, words, font, max_w)
        line_h = int(size * 1.08)
        if len(lines) <= 3 and len(lines) * line_h <= H - 120 and \
                all(draw.textlength(l, font=font) <= max_w for l in lines):
            break
        size -= 8
    font = _font(size)
    lines = _wrap(draw, words, font, max_w)
    line_h = int(size * 1.08)
    y = (H - line_h * len(lines)) // 2
    stroke = max(3, size // 14)
    for line in lines:
        draw.text((text_left, y), line, font=font, fill=ink, stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += line_h

    canvas.save(out_path, "PNG")
    return out_path
