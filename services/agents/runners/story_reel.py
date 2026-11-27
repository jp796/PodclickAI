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
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.media import base, ffmpeg_local
from services.media.base import GenerationRequest, MediaProviderError, ProviderQuotaError, SpeechRequest

VO_DISCLOSURE = "AI voice of JP"   # voice-over reels: cloned voice, no twin on screen

# ── HeyGen spend (estimated locally — HeyGen's API has no verified balance read) ──
SPEND_FILE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "heygen_spend.json"
DEFAULT_USD_PER_MIN = 4.83         # Avatar IV + digital twin, API pay-as-you-go (third-party, 2026-11)
DEFAULT_MONTHLY_BUDGET = 100.0


def _float_setting(name: str, default: float) -> float:
    try:
        return float(base.setting(name, default))
    except (TypeError, ValueError):
        return default


def heygen_rate() -> float:
    return _float_setting("heygen_usd_per_min", DEFAULT_USD_PER_MIN)


def heygen_budget() -> float:
    return _float_setting("heygen_monthly_budget_usd", DEFAULT_MONTHLY_BUDGET)


def _month(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m")


def _read_spend() -> Dict[str, Any]:
    try:
        return json.loads(SPEND_FILE.read_text())
    except (OSError, ValueError):
        return {}


def record_heygen_seconds(seconds: float, now: Optional[datetime] = None) -> None:
    """Add one finished twin render to this month's tally. Never raises."""
    try:
        data = _read_spend()
        m = data.setdefault(_month(now), {"seconds": 0.0, "renders": 0})
        m["seconds"] = round(float(m.get("seconds") or 0) + float(seconds or 0), 1)
        m["renders"] = int(m.get("renders") or 0) + 1
        SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
        SPEND_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def month_spend(now: Optional[datetime] = None) -> Dict[str, float]:
    m = _read_spend().get(_month(now)) or {}
    seconds = float(m.get("seconds") or 0)
    return {"seconds": seconds, "renders": int(m.get("renders") or 0),
            "usd": round(seconds / 60.0 * heygen_rate(), 2), "budget": heygen_budget()}


def spend_line(now: Optional[datetime] = None) -> str:
    s = month_spend(now)
    line = (f"HeyGen this month (est.): {s['seconds'] / 60:.1f} min, {s['renders']} reels, "
            f"≈ ${s['usd']:.0f} of ${s['budget']:.0f}")
    if s["budget"] and s["usd"] >= 0.8 * s["budget"]:
        line += " — nearly out; reels switch to voice-over when HeyGen runs dry"
    return line


def is_out_of_credit(exc: BaseException) -> bool:
    if isinstance(exc, ProviderQuotaError):
        return True
    text = f"{exc} {getattr(exc, 'user_message', '')}".lower()
    return "credit" in text or "quota" in text

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


def retag_panel(src: str, dest: str, text: str = VO_DISCLOSURE) -> None:
    """Copy of a rendered panel with the top-right disclosure tag swapped (the
    voice-over reel has no twin on screen, so it says 'AI voice', not 'AI twin')."""
    from PIL import Image, ImageDraw
    with Image.open(src) as raw:
        img = raw.convert("RGB")
    d = ImageDraw.Draw(img)
    tf = _font(28)
    W = img.width
    old_w = int(d.textlength(DISCLOSURE, font=tf))
    new_w = int(d.textlength(text, font=tf))
    w = max(old_w, new_w)
    d.rounded_rectangle([W - w - 64, 24, W - 24, 72], radius=12, fill=(0, 0, 0))
    d.text((W - new_w - 44, 33), text, font=tf, fill=(255, 255, 255))
    img.save(dest, "PNG")


async def stack_voiceover(panel: Path, slides: List[str], voice: Path, duration: float, dest: Path) -> None:
    """Voice-over reel: the story panel on top, the story's slides taking turns in
    the bottom half (each held an equal share of the voice), JP's cloned voice."""
    seg = max(1.0, duration / max(1, len(slides))) + 0.1     # a hair long so the voice never outruns it
    args = ["-loop", "1", "-t", f"{duration:.2f}", "-i", str(panel)]
    for s in slides:
        args += ["-loop", "1", "-t", f"{seg:.2f}", "-i", str(s)]
    args += ["-i", str(voice)]
    parts = "".join(
        f"[{i + 1}:v]scale={REEL_W}:{TWIN_H}:force_original_aspect_ratio=increase,"
        f"crop={REEL_W}:{TWIN_H},setsar=1,fps=30[s{i}];" for i in range(len(slides)))
    fc = (f"[0:v]scale={REEL_W}:{PANEL_H},setsar=1,fps=30[top];{parts}"
          + "".join(f"[s{i}]" for i in range(len(slides)))
          + f"concat=n={len(slides)}:v=1:a=0[bot];[top][bot]vstack=inputs=2,format=yuv420p[v]")
    await ffmpeg_local.run_ffmpeg(args + [
        "-filter_complex", fc, "-map", "[v]", "-map", f"{len(slides) + 1}:a", "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)], timeout=600)


# ── the reel ──────────────────────────────────────────────────────────────────

def _record_usage(ctx: Any, usage: Optional[Dict[str, Any]]) -> None:
    add = getattr(ctx, "add_usage", None)
    if usage and callable(add):
        add(usage)


def providers(ctx: Any) -> Tuple[Any, Any, Optional[str], Optional[str]]:
    """(tts, twin, why-no-reels, why-no-twin).

    No ElevenLabs → no reels at all (why-no-reels). No usable HeyGen → twin is
    None and the reels go out as voice-over reels (why-no-twin says why)."""
    media = getattr(ctx, "media", None)
    if not callable(media):
        return None, None, "no media providers in this context", None
    tts, twin = media("tts"), media("twin_video")
    if tts is None:
        return None, None, "ElevenLabs isn't connected", None
    if twin is None:
        return tts, None, None, "HeyGen isn't connected"
    try:
        twin.check_ready()
    except MediaProviderError as exc:
        return tts, None, None, exc.user_message
    return tts, twin, None, None


TWIN_ASPECT = "16:9"           # JP's HeyGen look is landscape; a 9:16 request comes back letterboxed


def content_box(frame: str, threshold: float = 6.0) -> Optional[Tuple[int, int, int, int]]:
    """(w, h, x, y) of the picture inside plain letterbox/pillarbox bars of any
    colour (HeyGen pads JP's 4:3 look with light grey). None when there are no bars."""
    from PIL import Image, ImageStat
    with Image.open(frame) as raw:
        im = raw.convert("L")
    W, H = im.size
    rows = [y for y in range(H) if ImageStat.Stat(im.crop((0, y, W, y + 1))).stddev[0] > threshold]
    cols = [x for x in range(W) if ImageStat.Stat(im.crop((x, 0, x + 1, H))).stddev[0] > threshold]
    if not rows or not cols:
        return None
    x0, x1, y0, y1 = cols[0], cols[-1] + 1, rows[0], rows[-1] + 1
    w, h = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    if w >= W - 4 and h >= H - 4:
        return None
    if w < W // 3 or h < H // 4:            # a mostly-flat frame: don't trust it
        return None
    return w, h, x0, y0


async def _twin_crop(twin_raw: Path, duration: float) -> str:
    frame = twin_raw.with_name(twin_raw.stem + "_probe.png")
    try:
        await ffmpeg_local.run_ffmpeg(["-ss", f"{min(3.0, duration / 2):.2f}", "-i", str(twin_raw),
                                       "-frames:v", "1", str(frame)])
        box = await asyncio.get_running_loop().run_in_executor(None, content_box, str(frame))
    except Exception:
        box = None
    return f"crop={box[0]}:{box[1]}:{box[2]}:{box[3]}," if box else ""


async def stack(panel: Path, twin_raw: Path, voice: Path, duration: float, dest: Path) -> None:
    """Panel on top (static, so the source strip is never cropped), the twin filling
    the bottom half: bars trimmed, then cover-scaled and centred on JP."""
    trim = await _twin_crop(Path(twin_raw), duration)
    fc = (f"[0:v]scale={REEL_W}:{PANEL_H},setsar=1,fps=30[top];"
          f"[1:v]{trim}scale={REEL_W}:{TWIN_H}:force_original_aspect_ratio=increase,"
          f"crop={REEL_W}:{TWIN_H},setsar=1,fps=30[bot];"
          f"[top][bot]vstack=inputs=2,format=yuv420p[v]")
    await ffmpeg_local.run_ffmpeg([
        "-loop", "1", "-t", f"{duration:.2f}", "-i", str(panel), "-i", str(twin_raw), "-i", str(voice),
        "-filter_complex", fc, "-map", "[v]", "-map", "2:a", "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)], timeout=600)


async def burn_captions(video: Path, words: List[Dict[str, Any]], dest: Path) -> Path:
    """The Bold word captions, centred on the seam between story and twin."""
    from pipeline.subtitles import burn_subtitles, generate_ass_subtitles
    ass = Path(dest).with_suffix(".ass")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: generate_ass_subtitles(
        words, REEL_W, REEL_H, str(ass), style={"preset": "bold", "position": "middle"}))
    try:
        await loop.run_in_executor(None, burn_subtitles, str(video), str(ass), str(dest), None)
    except RuntimeError as exc:
        raise ffmpeg_local.FfmpegError(str(exc), provider="ffmpeg_local",
                                       user_message="Couldn't burn the captions in.")
    return Path(dest)


async def build_reel(ctx: Any, tts: Any, twin: Any, out_dir: Path, n: int, script: str,
                     panel: Path, slides: Optional[List[str]] = None,
                     state: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Voice → twin → stack → captions. If the twin can't render (no HeyGen, out of
    credits, any HeyGen failure) and the story has slides, it becomes a voice-over
    reel instead: same panel on top, the slides underneath, JP's cloned voice.
    Returns {"file", "duration_s", "captions", "mode"} or None (with a warning) —
    never raises for a provider problem."""
    from services.agents.runners.twin_video import build_timing
    from services.media.elevenlabs import voice_settings_for

    state = state if state is not None else {}
    slides = [s for s in (slides or []) if s and Path(s).is_file()]
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
        v = await tts.synthesize_speech(SpeechRequest(text=text, settings=voice_settings_for("narration")), voice)
        _record_usage(ctx, v.usage)
        duration = await ffmpeg_local.probe_duration(voice) or v.duration_s
        if not duration:
            raise RuntimeError("the voice track came back unreadable")
    except MediaProviderError as exc:
        ctx.warn(f"Story {n}: no reel — {exc.user_message} It keeps the slideshow.")
        return None
    except Exception as exc:
        ctx.warn(f"Story {n}: no reel — {str(exc)[:160]}. It keeps the slideshow.")
        return None

    mode, t = None, None
    if twin is not None and not state.get("twin_out"):
        try:
            twin.check_seconds(float(duration))
            ctx.check_cancelled()
            t = await twin.generate(GenerationRequest(capability="twin_video", prompt="",
                                                      duration_s=int(math.ceil(duration)), source_audio=voice,
                                                      aspect=TWIN_ASPECT), raw)
            _record_usage(ctx, t.usage)
            record_heygen_seconds(float(getattr(t, "duration_s", None) or duration))
            await stack(panel, raw, voice, float(duration), stacked)
            mode = "twin"
        except MediaProviderError as exc:
            if is_out_of_credit(exc):
                if not state.get("twin_out"):
                    state["twin_out"] = True
                    ctx.warn("HeyGen is out of credits — switching to voice-over reels "
                             "(your ElevenLabs voice, no twin). Top up at https://app.heygen.com/settings/api "
                             "to bring the twin back.")
            else:
                ctx.warn(f"Story {n}: no twin — {exc.user_message}")
        except Exception as exc:
            ctx.warn(f"Story {n}: no twin — {str(exc)[:160]}.")
    if mode is None:
        if not slides:
            ctx.warn(f"Story {n}: no reel — no twin and no slides to show. It keeps the slideshow.")
            return None
        vo_panel = out_dir / f"story{n}_panel_vo.png"
        try:
            await asyncio.get_running_loop().run_in_executor(None, retag_panel, str(panel), str(vo_panel))
            await stack_voiceover(vo_panel, slides, voice, float(duration), stacked)
            mode = "voiceover"
        except Exception as exc:
            ctx.warn(f"Story {n}: no reel — {str(exc)[:160]}. It keeps the slideshow.")
            return None
    words, _segs, _src = build_timing(text, float(duration), ((t.meta if t else None) or {}).get("subtitle_path"))
    captioned = False
    if words:
        try:
            await burn_captions(stacked, words, final)
            captioned = final.is_file()
        except MediaProviderError:
            ctx.warn(f"Story {n}: couldn't burn the captions in — the reel ships without them.")
    if not captioned:
        stacked.replace(final)
    return {"file": final.name, "duration_s": round(float(duration), 1), "captions": captioned, "mode": mode}


async def build_all(ctx: Any, tts: Any, twin: Any, jobs: List[Dict[str, Any]],
                    concurrency: int = 2) -> Dict[int, Dict[str, Any]]:
    """Render reels for several stories at once (HeyGen renders take minutes).
    Each job: {"n", "script", "panel", "slides"?}. `tts`/`twin` come from `providers()`;
    twin may be None (voice-over reels only). A story past the per-run HeyGen cap
    gets a voice-over reel instead. Returns {n: reel} for the ones that landed."""
    cap = int(getattr(twin, "max_seconds", 0) or 0) if twin is not None else 0
    budget = float(cap) if cap else float("inf")
    planned: List[Tuple[Dict[str, Any], Any]] = []
    for j in jobs:
        est = len(spoken_text(j["script"]).split()) / 2.6
        use_twin = twin
        if twin is not None and est > budget:
            ctx.warn(f"Story {j['n']}: no twin — HEYGEN_MAX_SECONDS ({cap}s) is used up for this run; "
                     "it gets a voice-over reel.")
            use_twin = None
        elif twin is not None:
            budget -= est
        planned.append((j, use_twin))
    sem = asyncio.Semaphore(max(1, concurrency))
    state: Dict[str, Any] = {}

    async def one(j: Dict[str, Any], tw: Any) -> Tuple[int, Optional[Dict[str, Any]]]:
        async with sem:
            return j["n"], await build_reel(ctx, tts, tw, Path(ctx.output_dir), j["n"], j["script"],
                                            Path(j["panel"]), slides=j.get("slides"), state=state)

    done = await asyncio.gather(*(one(j, tw) for j, tw in planned))
    return {n: r for n, r in done if r}
