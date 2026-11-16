"""
Twin Video (`twin_video`) — a full video of your HeyGen digital twin delivering
a script in your cloned voice, with stock cutaways and captions.

The pieces, and who owns each:

    script   Foundation (`draft_script`, contract #3) — or your own words, verbatim
    voice    ElevenLabs (`tts`) — your cloned voice
    face     HeyGen (`twin_video`) — your digital twin, lip-synced to that voice
    cutaways pipeline/broll.py — stock footage over the middle of the video,
             and Higgsfield (`image_to_video`) — a few of those cutaways as
             stills brought to life instead of stock clips
    captions pipeline/subtitles.py — the same word-highlight captions clips use

Hard requirements: ElevenLabs AND HeyGen (key + `HEYGEN_AVATAR_ID`). Cutaways
are soft: with no Pexels/OpenAI key, or when nothing fits, you stay on camera
and the job carries a warning. Animated cutaways are softer still: with no
Higgsfield key, or when a render fails, that moment gets a stock clip instead.

Animated cutaways need no public media origin either: Higgsfield fetches the
still straight from the stock library's own public address, and the downloaded
copy in this job's folder is what the likeness check looks at. Every animated
shot is `ANIMATION_SECONDS` long and the count is capped by
`HIGGSFIELD_MAX_SECONDS` before the first one is submitted.

Unlike Avatar Video this needs no public media origin — HeyGen takes the voice
track as an upload — and no persona photo: the twin lives in HeyGen, where the
person it depicts recorded their consent.

Cost discipline: every cap (ElevenLabs characters, HeyGen seconds) is checked in
`preflight`, before a character or a second is spent, and again on the measured
voice track before the render is submitted.

Nothing here publishes. The job ends with a file to review; every output carries
`ai_generated: true` so whatever posts it can set the platform's disclosure.
"""

import asyncio
import math
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.agents.contract import AgentResult, Output
from services.agents.runners.avatar_video import WORDS_PER_SECOND, draft_script, estimate_seconds
from services.media import base, ffmpeg_local
from services.media.base import GenerationRequest, MediaProviderError, SpeechRequest
from services.media.elevenlabs import voice_settings_for

FORMATS: Dict[str, str] = {"landscape": "16:9", "vertical": "9:16"}
FALLBACK_SIZE: Dict[str, Tuple[int, int]] = {"landscape": (1920, 1080), "vertical": (1080, 1920)}
MINUTES = (1, 2, 3, 5, 8)
CHARS_PER_WORD = 6.0            # spoken English incl. the space — for the pre-draft cap check
SEGMENT_WORDS = 14              # fallback cutaway-planning segment size when HeyGen sends no SRT
ANIMATION_SECONDS = 5           # length of each Higgsfield-animated cutaway
ANIMATION_CHOICES = (0, 2, 3, 4, 6)
ANIMATION_PROMPT = ("Slow cinematic camera move with subtle, natural motion. Photorealistic. "
                    "No text, no captions, no logos. Scene: {query}")
PEXELS_PHOTO_SEARCH = "https://api.pexels.com/v1/search"

LONG_FORM_RULES = (
    "Structure: a hook, then the points one at a time, then one clear call to action.",
    "Write for the ear: short sentences, contractions, the way I talk on camera.",
    "Spell out numbers, dollar amounts, abbreviations and street suffixes the way they're said aloud.",
)


def _record_usage(ctx: Any, usage: Optional[Dict[str, Any]]) -> None:
    add = getattr(ctx, "add_usage", None)
    if usage and callable(add):
        add(usage)


# ── timing: HeyGen's SRT when we have it, an estimate when we don't ───────────

_SRT_TIME = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def parse_srt(text: str) -> List[Dict[str, Any]]:
    """SRT → [{start, end, text}]. Tolerant: skips anything it can't read."""
    cues: List[Dict[str, Any]] = []
    for block in re.split(r"\r?\n\s*\r?\n", (text or "").strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            m = _SRT_TIME.search(line)
            if not m:
                continue
            h1, m1, s1, ms1, h2, m2, s2, ms2 = m.groups()
            start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1.ljust(3, "0")) / 1000.0
            end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2.ljust(3, "0")) / 1000.0
            body = re.sub(r"<[^>]+>", "", " ".join(lines[i + 1:])).strip()
            if body and end > start:
                cues.append({"start": start, "end": end, "text": body})
            break
    return cues


def words_from_cues(cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Spread each cue's words across that cue's own window (a few seconds), so
    caption timing can't drift the way a whole-video estimate does."""
    words: List[Dict[str, Any]] = []
    for cue in cues:
        span = float(cue["end"]) - float(cue["start"])
        for w in ffmpeg_local.estimate_alignment(cue["text"], span):
            words.append({"word": w["word"],
                          "start": round(cue["start"] + w["start"], 3),
                          "end": round(cue["start"] + w["end"], 3)})
    return words


def segments_from_words(words: List[Dict[str, Any]], size: int = SEGMENT_WORDS) -> List[Dict[str, Any]]:
    segs: List[Dict[str, Any]] = []
    for i in range(0, len(words), size):
        chunk = words[i:i + size]
        segs.append({"start": chunk[0]["start"], "end": chunk[-1]["end"],
                     "text": " ".join(w["word"] for w in chunk)})
    return segs


def build_timing(script: str, duration: float,
                 subtitle_path: Optional[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """(words, segments, source) — source is 'heygen_srt' or 'estimated'."""
    cues: List[Dict[str, Any]] = []
    if subtitle_path:
        try:
            cues = parse_srt(Path(subtitle_path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            cues = []
    if cues:
        return words_from_cues(cues), cues, "heygen_srt"
    words = ffmpeg_local.estimate_alignment(script, duration)
    return words, segments_from_words(words), "estimated"


# ── cutaways (sync pipeline code, run off the event loop; patched in tests) ───

def _video_size(path: Path, fmt: str) -> Tuple[int, int]:
    try:
        from pipeline.broll import _get_video_dims
        w, h = _get_video_dims(str(path))
        if int(w) > 0 and int(h) > 0:
            return int(w), int(h)
    except Exception:
        pass
    return FALLBACK_SIZE[fmt]


def _plan_slots_sync(segments: List[Dict[str, Any]], openai_key: str, duration: float) -> List[Dict[str, Any]]:
    from pipeline.broll import plan_broll
    return plan_broll(segments, openai_key, duration)


def _fetch_clips_sync(slots: List[Dict[str, Any]], pexels_key: str, cache: Path) -> List[Dict[str, Any]]:
    from pipeline.broll import fetch_clips
    cache.mkdir(parents=True, exist_ok=True)
    return fetch_clips(slots, pexels_key, str(cache))


def _composite_sync(video: Path, slots: List[Dict[str, Any]], dest: Path) -> bool:
    from pipeline.broll import composite
    return bool(composite(str(video), slots, str(dest)))


def pick_spread(slots: List[Dict[str, Any]], count: int) -> List[int]:
    """Indexes of `count` slots spread evenly through the video (not the first few)."""
    n = len(slots)
    if count <= 0 or n == 0:
        return []
    if count >= n:
        return list(range(n))
    step = n / float(count)
    return sorted({min(n - 1, int(step * i + step / 2.0)) for i in range(count)})


async def fetch_still(query: str, pexels_key: str, dest: Path, fmt: str) -> Optional[Tuple[Path, str]]:
    """One stock photo for `query` → (local copy, its public https address), or None.

    The public address is what Higgsfield fetches; the local copy is the
    `source_image` its likeness check requires. Never raises: a missing still
    just means that moment gets a stock clip.
    """
    orientation = "portrait" if fmt == "vertical" else "landscape"
    headers = {"Authorization": pexels_key, "User-Agent": "PodClickAI/1.0"}
    try:
        async with base.http_client(timeout=30.0) as client:
            resp = await client.get(PEXELS_PHOTO_SEARCH, headers=headers,
                                    params={"query": query, "per_page": 3, "orientation": orientation})
            if resp.status_code != 200:
                return None
            photos = (resp.json() or {}).get("photos") or []
            for photo in photos:
                src = photo.get("src") or {}
                url = str(src.get("large2x") or src.get("large") or src.get("original") or "")
                if not url.startswith("https://"):
                    continue
                img = await client.get(url, headers={"User-Agent": "PodClickAI/1.0"}, follow_redirects=True)
                if img.status_code == 200 and img.content:
                    base.atomic_write_bytes(dest, img.content)
                    return dest, url
    except Exception:
        return None
    return None


async def animate_slots(ctx: Any, slots: List[Dict[str, Any]], count: int, pexels_key: str,
                        out_dir: Path, fmt: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Turn up to `count` slots into Higgsfield-animated stills.

    Returns (animated slots with `clip_path`, the slots left for stock footage).
    Any slot that can't be animated stays in the stock list — never raises.
    """
    if count <= 0 or not slots:
        return [], list(slots)
    hf = ctx.media("image_to_video")
    if hf is None:
        ctx.warn("No animated cutaways on this one — Higgsfield isn't connected. Stock footage only.")
        return [], list(slots)
    cap = int(getattr(hf, "max_seconds", 0) or 0)
    if cap:
        fits = cap // ANIMATION_SECONDS
        if fits < count:
            count = fits
            ctx.warn(f"Animated cutaways trimmed to {count} — HIGGSFIELD_MAX_SECONDS is {cap}.")
    chosen = set(pick_spread(slots, count))
    animated: List[Dict[str, Any]] = []
    stock: List[Dict[str, Any]] = []
    stopped = False
    for i, slot in enumerate(slots):
        if i not in chosen or stopped:
            stock.append(slot)
            continue
        ctx.check_cancelled()
        still = await fetch_still(slot["query"], pexels_key, out_dir / f"still_{i:02d}.jpg", fmt)
        if still is None:
            stock.append(slot)
            continue
        photo, url = still
        clip = out_dir / f"anim_{i:02d}.mp4"
        try:
            asset = await hf.generate(
                GenerationRequest(capability="image_to_video",
                                  prompt=ANIMATION_PROMPT.format(query=slot["query"]),
                                  duration_s=ANIMATION_SECONDS, image_url=url, source_image=photo,
                                  aspect=FORMATS[fmt]),
                clip)
        except MediaProviderError as exc:
            # One bad render falls back to stock. Anything that will fail the
            # same way next time (key, credits, an unheard-from submit) stops
            # the animating so the job doesn't keep spending or keep failing.
            stock.append(slot)
            if not isinstance(exc, base.ProviderRejected):
                stopped = True
                ctx.warn("Animated cutaways stopped early — " + exc.user_message)
            continue
        except ValueError:
            stock.append(slot)
            continue
        _record_usage(ctx, asset.usage)
        animated.append({**slot, "end": min(float(slot["end"]), float(slot["start"]) + ANIMATION_SECONDS),
                         "clip_path": str(clip), "animated": True})
    return animated, stock


async def add_cutaways(ctx: Any, video: Path, segments: List[Dict[str, Any]], dest: Path, out_dir: Path,
                       duration: float, openai_key: str, pexels_key: str, animate: int,
                       fmt: str) -> Tuple[Path, int, int]:
    """(video to use, stock cutaways, animated cutaways). Returns `video`
    unchanged when nothing landed. Never raises for a cutaway problem."""
    loop = asyncio.get_running_loop()
    try:
        slots = await loop.run_in_executor(None, _plan_slots_sync, segments, openai_key, duration)
    except Exception:
        slots = []
    if not slots:
        return video, 0, 0
    animated, rest = await animate_slots(ctx, slots, animate, pexels_key, out_dir, fmt)
    try:
        stock = await loop.run_in_executor(None, _fetch_clips_sync, rest, pexels_key,
                                           out_dir / "broll_cache") if rest else []
    except Exception:
        stock = []
    placed = sorted(animated + list(stock), key=lambda s2: s2["start"])
    if not placed:
        return video, 0, 0
    try:
        ok = await loop.run_in_executor(None, _composite_sync, video, placed, dest)
    except Exception:
        ok = False
    if not ok or not Path(dest).is_file():
        return video, 0, 0
    return Path(dest), len(stock), len(animated)


# ── runner ────────────────────────────────────────────────────────────────────

def _planned_chars(seconds: float) -> int:
    return int(seconds * WORDS_PER_SECOND * CHARS_PER_WORD)


async def run(ctx: Any, inp: Dict[str, Any]) -> AgentResult:
    script = (inp.get("script") or "").strip()
    topic = (inp.get("topic") or "").strip()
    if not script and not topic:
        raise ValueError("Give me a script, or a topic and I'll draft one.")
    try:
        minutes = int(inp.get("length") or 2)
    except (TypeError, ValueError):
        minutes = 2
    if minutes not in MINUTES:
        minutes = 2
    fmt = str(inp.get("format") or "landscape").strip().lower()
    if fmt not in FORMATS:
        fmt = "landscape"
    want_broll = inp.get("broll", True) is not False
    try:
        animate = int(inp.get("animate") if inp.get("animate") not in (None, "") else 3)
    except (TypeError, ValueError):
        animate = 3
    if animate not in ANIMATION_CHOICES:
        animate = 3
    want_captions = inp.get("captions", True) is not False
    out_dir = Path(ctx.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── preflight: nothing below spends a cent until all of this passes ──
    async with ctx.step("preflight", "Checking the crew's tools"):
        tts = ctx.media("tts")
        twin = ctx.media("twin_video")
        missing = [n for n, p in (("ElevenLabs", tts), ("HeyGen", twin)) if p is None]
        if missing:
            raise RuntimeError(" and ".join(missing) + " not connected — Twin Video needs both. "
                               "Add the keys to .env and restart.")
        planned = estimate_seconds(script) if script else float(minutes * 60)
        try:
            twin.check_ready()
            twin.check_seconds(planned)
            if script:
                tts.check_text(script)
        except MediaProviderError as exc:
            raise base.as_user_error(exc)
        cap = int(getattr(tts, "max_chars", 0) or 0)
        if not script and cap and _planned_chars(planned) > cap:
            raise RuntimeError(
                f"A {minutes}-minute script runs about {_planned_chars(planned):,} characters — the voice cap "
                f"is {cap:,}. Pick a shorter length or raise ELEVENLABS_MAX_CHARS.")
        openai_key = str(base.setting("openai_api_key", "") or "")
        pexels_key = str(base.setting("pexels_api_key", "") or "")
        if want_broll and not (openai_key and pexels_key):
            want_broll = False
            ctx.warn("No cutaways on this one — they need PEXELS_API_KEY and OPENAI_API_KEY. "
                     "You're on camera the whole way.")

    if topic and not script:
        ctx.check_cancelled()
        async with ctx.step("script", "Drafting the script in your voice"):
            seconds = minutes * 60
            words = int(seconds * WORDS_PER_SECOND)
            script = await draft_script(
                ctx, topic=topic, seconds=seconds,
                brief=(f"Write a {minutes}-minute talking-to-camera video script about: {topic}"),
                rules=LONG_FORM_RULES, number_sources=[topic],
                max_tokens=min(8000, int(words * 1.8) + 400),
            )
            try:
                tts.check_text(script)
                twin.check_seconds(estimate_seconds(script))
            except MediaProviderError as exc:
                raise base.as_user_error(exc)

    ctx.check_cancelled()
    voice_path = out_dir / "voice.mp3"
    async with ctx.step("voice", "Recording your voice"):
        try:
            v_asset = await tts.synthesize_speech(
                SpeechRequest(text=script, settings=voice_settings_for("narration")), voice_path)
        except MediaProviderError as exc:
            raise base.as_user_error(exc)
        _record_usage(ctx, v_asset.usage)
        duration = await ffmpeg_local.probe_duration(voice_path) or v_asset.duration_s
        if not duration:
            raise RuntimeError("The voice track came back unreadable.")

    ctx.check_cancelled()
    raw = out_dir / "twin_raw.mp4"
    async with ctx.step("twin", "Rendering your twin on HeyGen"):
        try:
            twin.check_seconds(float(duration))             # exact cap, before any upload or submit
            t_asset = await twin.generate(
                GenerationRequest(capability="twin_video", prompt="", duration_s=int(math.ceil(duration)),
                                  source_audio=voice_path, aspect=FORMATS[fmt]),
                raw)
        except MediaProviderError as exc:
            raise base.as_user_error(exc)
        _record_usage(ctx, t_asset.usage)

    words, segments, timing_source = build_timing(script, float(duration),
                                                  (t_asset.meta or {}).get("subtitle_path"))
    current = raw
    stock_n = animated_n = 0

    if want_broll:
        ctx.check_cancelled()
        async with ctx.step("cutaways", "Cutting in footage"):
            current, stock_n, animated_n = await add_cutaways(
                ctx, raw, segments, out_dir / "twin_broll.mp4", out_dir, float(duration),
                openai_key, pexels_key, animate, fmt)
            if current == raw:
                ctx.warn("No cutaways landed on this one — you're on camera the whole way.")
    elif callable(getattr(ctx, "skip_step", None)):
        ctx.skip_step("cutaways", "Cutting in footage")
    cutaways = (stock_n + animated_n) > 0

    ctx.check_cancelled()
    final = out_dir / "twin.mp4"
    captioned = False
    if want_captions and words:
        async with ctx.step("captions", "Burning in captions"):
            try:
                await ffmpeg_local.burn_captions(current, words, final, size=_video_size(current, fmt))
                captioned = True
            except MediaProviderError:
                # A paid render is already on disk: ship it clean rather than fail the job.
                ctx.warn("Couldn't burn the captions in — this cut ships without them.")
    if not captioned:
        shutil.copyfile(current, final)

    ctx.add_output(Output(kind="video", label="Twin video", file=final.name,
                          meta={"duration_s": round(float(duration), 2), "ai_generated": True,
                                "format": fmt, "cutaways": cutaways, "stock_cutaways": stock_n,
                                "animated_cutaways": animated_n, "captions": captioned,
                                "caption_timing": timing_source}))
    ctx.add_output(Output(kind="text", label="Script", value=script))
    ctx.add_output(Output(kind="json", label="Usage", value=[v_asset.usage or {}, t_asset.usage or {}]))
    return AgentResult(outputs=[], commit=None)
