"""
Story reels for the Local Story Desk — the Jared James layout.

One 9:16 reel per story, in place of the silent slideshow:

    top    the story itself: a screenshot of the article's headline area (or, for
           one of JP's listings, the listing's hero photo with price/beds/baths
           from the MLS record), a source line, and an "AI twin" disclosure
    bottom JP's HeyGen digital twin talking to camera in his cloned
           ElevenLabs voice
    middle big word-by-word captions on the seam

Every piece of text that reaches the screen or the voice comes from the story's
already-guarded `script` (no invented numbers, no Fair Housing flags) — this
module never writes copy. A reel is extra: any failure here returns None and the
caller keeps the slideshow, so a HeyGen hiccup never costs JP the week's posts.
"""

import asyncio
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.media import base, ffmpeg_local
from services.media.base import GenerationRequest, MediaProviderError, SpeechRequest

REEL_W, REEL_H = 1080, 1920
PANEL_H = 900                 # story screenshot on top
TWIN_H = REEL_H - PANEL_H     # twin underneath
STRIP_H = 96                  # source / attribution strip at the bottom of the panel
MAX_REEL_SECONDS = 50         # a story reel is a short; longer scripts get no reel
SHOT_TIMEOUT_MS = 25_000
DISCLOSURE = "AI twin of JP"

_LABEL = re.compile(r"^\s*(hook|cta|call to action|line \d+|outro|intro)\s*[:\-–]\s*", re.I)
_DIRECTION = re.compile(r"\[[^\]]*\]|\([^)]*(?:b-?roll|cut to|show|on screen|pause)[^)]*\)", re.I)
_STOP = {"the", "a", "an", "and", "or", "of", "in", "on", "to", "for", "at", "is", "with", "by", "from"}


# ── script → what the voice says ──────────────────────────────────────────────

def spoken_text(script: str) -> str:
    """The guarded reel script minus its 'Hook:' labels and stage directions."""
    lines = []
    for raw in (script or "").splitlines():
        line = _DIRECTION.sub("", _LABEL.sub("", raw)).strip()
        if line:
            lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


# ── the top panel ─────────────────────────────────────────────────────────────

def _title_words(title: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9']+", (title or "").lower()) if w not in _STOP and len(w) > 2]


def headline_visible(page_text: str, title: str) -> bool:
    """True when most of the headline's words are on the captured screen — a
    consent wall, paywall or redirect page fails this and gets the card instead."""
    words = _title_words(title)
    if not words:
        return False
    text = (page_text or "").lower()
    hits = sum(1 for w in words if w in text)
    return hits / len(words) >= 0.6


async def capture_article(url: str, title: str, dest: Path) -> bool:
    """Screenshot the top of the article (headline area) at phone width. Never raises."""
    if not url.startswith(("http://", "https://")):
        return False
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return False
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 432, "height": 540}, device_scale_factor=2.5,
                                              is_mobile=True, has_touch=True)
                await page.goto(url, wait_until="domcontentloaded", timeout=SHOT_TIMEOUT_MS)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                # Lock onto the headline element itself and put it at the top of the frame;
                # a page whose headings don't carry the story's headline (aggregator shell,
                # consent wall, paywall) gets the headline card instead.
                headings = await page.evaluate(
                    "() => [...document.querySelectorAll('h1,h2')].slice(0, 12)"
                    ".map((e, i) => { e.setAttribute('data-reel-h', String(i)); return e.innerText || ''; })")
                idx = next((i for i, h in enumerate(headings or []) if headline_visible(str(h), title)), None)
                if idx is None:
                    return False
                top = await page.evaluate(
                    "(i) => { for (const el of document.querySelectorAll('body *')) {"
                    "   const pos = getComputedStyle(el).position;"
                    "   if (pos === 'fixed' || pos === 'sticky') el.style.setProperty('display', 'none', 'important'); }"
                    " const e = document.querySelector(`[data-reel-h=\"${i}\"]`);"
                    " return e.getBoundingClientRect().top + window.scrollY; }", idx)
                await page.wait_for_timeout(300)
                y = max(0.0, float(top or 0) - 40.0)
                await page.screenshot(path=str(dest), full_page=True,
                                      clip={"x": 0, "y": y, "width": 432, "height": 540})
                return dest.is_file()
            finally:
                await browser.close()
    except Exception:
        return False


def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    for path in (("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else
                  "/System/Library/Fonts/Supplemental/Arial.ttf"),
                 "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, width: int) -> List[str]:
    lines: List[str] = []
    cur = ""
    for word in (text or "").split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _cover(img, w: int, h: int):
    from PIL import Image
    scale = max(w / img.width, h / img.height)
    img = img.resize((max(w, int(img.width * scale + 0.5)), max(h, int(img.height * scale + 0.5))),
                     Image.LANCZOS)
    return img.crop(((img.width - w) // 2, 0, (img.width - w) // 2 + w, h))   # keep the top: headlines live there


def render_panel(dest: str, strip_text: str, shot: Optional[str] = None, headline: str = "",
                 source: str = "", chip: str = "") -> None:
    """The 1080x900 top half: the screenshot (or a headline card when there's no
    usable screenshot), an optional fact chip, the source/attribution strip and
    the AI-twin disclosure tag."""
    from PIL import Image, ImageDraw
    W, H = REEL_W, PANEL_H
    img = Image.new("RGB", (W, H), (20, 22, 26))
    d = ImageDraw.Draw(img)
    body_h = H - STRIP_H
    if shot and Path(shot).is_file():
        try:
            with Image.open(shot) as raw:
                img.paste(_cover(raw.convert("RGB"), W, body_h), (0, 0))
        except OSError:
            shot = None
    if not shot or not Path(shot).is_file():
        # headline card: outlet on top, the headline big, nothing else from the article
        d.rectangle([0, 0, W, body_h], fill=(245, 243, 238))
        d.rectangle([0, 0, 18, body_h], fill=(200, 60, 45))
        d.text((70, 80), (source or "Local news").upper(), font=_font(38), fill=(200, 60, 45))
        f = _font(74)
        lines = _wrap(d, headline, f, W - 140)[:6]
        y = 170
        for line in lines:
            d.text((70, y), line, font=f, fill=(20, 22, 26))
            y += 92
    if chip:
        f = _font(46)
        tw = int(d.textlength(chip, font=f))
        x0, y0 = 40, body_h - 110
        d.rounded_rectangle([x0, y0, x0 + tw + 56, y0 + 80], radius=18, fill=(20, 22, 26))
        d.text((x0 + 28, y0 + 14), chip, font=f, fill=(255, 255, 255))
    # disclosure tag, top-right
    tf = _font(28)
    tw = int(d.textlength(DISCLOSURE, font=tf))
    d.rounded_rectangle([W - tw - 64, 24, W - 24, 72], radius=12, fill=(0, 0, 0))
    d.text((W - tw - 44, 33), DISCLOSURE, font=tf, fill=(255, 255, 255))
    # source / attribution strip
    d.rectangle([0, body_h, W, H], fill=(12, 13, 16))
    sf = _font(26, bold=False)
    lines = _wrap(d, strip_text, sf, W - 60)[:2]
    y = body_h + (STRIP_H - 32 * len(lines)) // 2
    for line in lines:
        d.text((30, y), line, font=sf, fill=(215, 215, 215))
        y += 32
    img.save(dest, "PNG")


# ── the reel ──────────────────────────────────────────────────────────────────

def _record_usage(ctx: Any, usage: Optional[Dict[str, Any]]) -> None:
    add = getattr(ctx, "add_usage", None)
    if usage and callable(add):
        add(usage)


def providers(ctx: Any) -> Tuple[Any, Any, Optional[str]]:
    """(tts, twin, why-not). why-not is None when both are ready."""
    media = getattr(ctx, "media", None)
    if not callable(media):
        return None, None, "no media providers in this context"
    tts, twin = media("tts"), media("twin_video")
    missing = [n for n, p in (("ElevenLabs", tts), ("HeyGen", twin)) if p is None]
    if missing:
        return tts, twin, " and ".join(missing) + " isn't connected"
    try:
        twin.check_ready()
    except MediaProviderError as exc:
        return tts, twin, exc.user_message
    return tts, twin, None


async def stack(panel: Path, twin_raw: Path, voice: Path, duration: float, dest: Path) -> None:
    """Panel on top (slow push-in), the twin's face-and-shoulders underneath."""
    frames = int(math.ceil(duration * 30)) + 30
    fc = (f"[0:v]scale={REEL_W * 2}:{PANEL_H * 2},zoompan=z='min(zoom+0.0004,1.06)':d={frames}"
          f":x='iw/2-(iw/zoom/2)':y='0':s={REEL_W}x{PANEL_H}:fps=30,setsar=1[top];"
          f"[1:v]scale={REEL_W}:-2,crop={REEL_W}:{TWIN_H}:0:'min(ih-{TWIN_H},ih*0.12)',setsar=1,fps=30[bot];"
          f"[top][bot]vstack=inputs=2,format=yuv420p[v]")
    await ffmpeg_local.run_ffmpeg([
        "-loop", "1", "-t", f"{duration:.2f}", "-i", str(panel), "-i", str(twin_raw), "-i", str(voice),
        "-filter_complex", fc, "-map", "[v]", "-map", "2:a", "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)], timeout=600)


async def build_reel(ctx: Any, tts: Any, twin: Any, out_dir: Path, n: int, script: str,
                     panel: Path) -> Optional[Dict[str, Any]]:
    """Voice → twin → stack → captions. Returns {"file", "duration_s", "captions"} or None
    (with a warning) — never raises for a provider problem."""
    from services.agents.runners.twin_video import build_timing
    from services.media.elevenlabs import voice_settings_for

    text = spoken_text(script)
    if not text:
        ctx.warn(f"Story {n}: no reel — the script was empty, so it keeps the slideshow.")
        return None
    est = len(text.split()) / 2.6
    if est > MAX_REEL_SECONDS:
        ctx.warn(f"Story {n}: no reel — the script runs about {est:.0f}s, over the {MAX_REEL_SECONDS}s "
                 "reel limit. It keeps the slideshow.")
        return None
    voice = out_dir / f"story{n}_voice.mp3"
    raw = out_dir / f"story{n}_twin_raw.mp4"
    stacked = out_dir / f"story{n}_reel_raw.mp4"
    final = out_dir / f"story{n}_reel.mp4"
    try:
        tts.check_text(text)
        twin.check_seconds(est)
        v = await tts.synthesize_speech(SpeechRequest(text=text, settings=voice_settings_for("narration")), voice)
        _record_usage(ctx, v.usage)
        duration = await ffmpeg_local.probe_duration(voice) or v.duration_s
        if not duration:
            raise RuntimeError("the voice track came back unreadable")
        twin.check_seconds(float(duration))
        ctx.check_cancelled()
        t = await twin.generate(GenerationRequest(capability="twin_video", prompt="",
                                                  duration_s=int(math.ceil(duration)), source_audio=voice,
                                                  aspect="9:16"), raw)
        _record_usage(ctx, t.usage)
        await stack(panel, raw, voice, float(duration), stacked)
    except MediaProviderError as exc:
        ctx.warn(f"Story {n}: no reel — {exc.user_message} It keeps the slideshow.")
        return None
    except Exception as exc:  # a render problem never costs the week's posts
        ctx.warn(f"Story {n}: no reel — {str(exc)[:160]}. It keeps the slideshow.")
        return None
    words, _segs, _src = build_timing(text, float(duration), (t.meta or {}).get("subtitle_path"))
    captioned = False
    if words:
        try:
            await ffmpeg_local.burn_captions(stacked, words, final, size=(REEL_W, REEL_H))
            captioned = final.is_file()
        except MediaProviderError:
            ctx.warn(f"Story {n}: couldn't burn the captions in — the reel ships without them.")
    if not captioned:
        stacked.replace(final)
    return {"file": final.name, "duration_s": round(float(duration), 1), "captions": captioned}


async def build_all(ctx: Any, tts: Any, twin: Any, jobs: List[Dict[str, Any]],
                    concurrency: int = 2) -> Dict[int, Dict[str, Any]]:
    """Render reels for several stories at once (HeyGen renders take minutes).
    Each job: {"n", "script", "panel"}. `tts`/`twin` come from `providers()`.
    Returns {n: reel} for the ones that landed."""
    cap = int(getattr(twin, "max_seconds", 0) or 0)
    budget = float(cap) if cap else float("inf")
    planned: List[Dict[str, Any]] = []
    for j in jobs:
        est = len(spoken_text(j["script"]).split()) / 2.6
        if est > budget:
            ctx.warn(f"Story {j['n']}: no reel — HEYGEN_MAX_SECONDS ({cap}s) is used up for this run.")
            continue
        budget -= est
        planned.append(j)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(j: Dict[str, Any]) -> Tuple[int, Optional[Dict[str, Any]]]:
        async with sem:
            return j["n"], await build_reel(ctx, tts, twin, Path(ctx.output_dir), j["n"], j["script"],
                                            Path(j["panel"]))

    done = await asyncio.gather(*(one(j) for j in planned))
    return {n: r for n, r in done if r}
