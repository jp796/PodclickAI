"""
Avatar Video (`avatar_video`) — a vertical talking head of you delivering a
script, without filming (spec §1.4).

Hard requirements: `tts` (ElevenLabs) AND `avatar_video` (Higgsfield) AND a
public media origin (Higgsfield fetches the photo and the voice track by URL,
so `PODCLICK_PUBLIC_MEDIA_BASE_URL` must serve lane A's files route). Every one
of those, plus the persona photo, the character cap and the seconds cap, is
checked in `preflight` — BEFORE a single ElevenLabs character or Higgsfield
second is spent.

Steps: preflight → script (only when `topic` is given; Foundation-voiced) →
voice (TTS) → render (Higgsfield `wan/v2.7/image-to-video`, one call per ≤15 s
segment because the model's duration is 2–15 s) → assemble (normalize, concat,
lay the continuous voice track back over the cut, burn captions).

Likeness: only photos from the user's own AI Persona library are accepted.
Every output carries `ai_generated: true` (spec §1.4 Disclosure).

This module also holds the script-drafting helpers shared by Home Tour and
Market Reel (`draft_script`, `fabricated_numbers`, `fair_housing_flags`):
lane D's file ownership has no neutral module for them. Candidate to move into
a shared `services/agents/` helper once lane A lands.
"""

import json
import math
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from services.agents.contract import AgentResult, Output
from services.media import base, ffmpeg_local
from services.media.base import GenerationRequest, MediaProviderError, SpeechRequest
from services.media.elevenlabs import voice_settings_for

DATA_DIR = Path(__file__).resolve().parents[3] / "data"   # patchable in tests
WORDS_PER_SECOND = 2.6          # ~155 wpm conversational delivery
SCRIPT_MODEL = "claude-sonnet-4-5"
AVATAR_PROMPT = ("The person in the photo speaks directly to camera with natural head movement, "
                 "subtle hand gestures and genuine expressions. Vertical framing, steady shot.")
LENGTHS = (30, 45, 60)

NO_FABRICATION_RULE = (
    "Never invent a statistic, price, percentage, date, count or market figure. Use only the numbers "
    "I give you, written as digits. If I give you none, talk about trends without any figures."
)


# ── shared script helpers ─────────────────────────────────────────────────────

def _record_usage(ctx: Any, usage: Optional[Dict[str, Any]]) -> None:
    add = getattr(ctx, "add_usage", None)
    if usage and callable(add):
        add(usage)


def estimate_seconds(text: str) -> float:
    return len(text.split()) / WORDS_PER_SECOND


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> Set[str]:
    out: Set[str] = set()
    for raw in _NUM.findall(text or ""):
        norm = raw.replace(",", "").rstrip(".")
        if "." in norm:
            norm = norm.rstrip("0").rstrip(".")
        out.add(norm)
    return out


def fabricated_numbers(script: str, sources: Iterable[str]) -> List[str]:
    """Numbers in `script` that appear in none of the user-supplied `sources`."""
    allowed: Set[str] = set()
    for s in sources:
        allowed |= numbers_in(s or "")
    return sorted(n for n in numbers_in(script) if n not in allowed)


FAIR_HOUSING_FLAGS = (
    "perfect for families", "great for families", "family-friendly", "family friendly",
    "ideal for families", "great for kids", "perfect for kids", "no kids", "no children",
    "adults only", "ideal for couples", "perfect for couples", "bachelor pad", "singles",
    "empty nesters", "retirees", "mature buyers", "young professionals", "exclusive community",
    "exclusive neighborhood", "safe neighborhood", "safe area", "low crime", "crime-free",
    "near churches", "walking distance to church", "christian", "integrated", "ethnic",
    "able-bodied", "handicapped",
)


def fair_housing_flags(text: str) -> List[str]:
    low = (text or "").lower()
    return [p for p in FAIR_HOUSING_FLAGS if re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", low)]


async def _complete(system: str, user: str, max_tokens: int = 900) -> str:
    """One Claude call. Patched in tests — no real model calls there."""
    import anthropic

    key = base.setting("anthropic_api_key", "")
    if not key:
        raise RuntimeError("Claude isn't connected — add ANTHROPIC_API_KEY to .env and restart.")
    client = anthropic.AsyncAnthropic(api_key=key)
    msg = await client.messages.create(model=SCRIPT_MODEL, max_tokens=max_tokens, system=system,
                                       messages=[{"role": "user", "content": user}])
    return msg.content[0].text


def _voice_block(bc: Any) -> str:
    """Brand context → prompt lines. Duck-typed: lane A may hand back the
    Foundation BrandContext model or a dict-like stand-in."""
    if bc is None:
        return ""
    bp = getattr(bc, "brand_profile", None)
    vp = getattr(bc, "voice_profile", None)
    vocab = getattr(bc, "vocabulary", None)
    lines: List[str] = []
    name = getattr(bp, "full_name", None)
    niche = getattr(bp, "niche_primary", None) or "real estate"
    market = getattr(bp, "market_city", None)
    lines.append(f"I'm {name}, " if name else "I work ")
    lines[-1] += f"in {niche}" + (f" in {market}." if market else ".")
    tones = getattr(vp, "tone", None) or []
    if tones:
        lines.append("My tone: " + ", ".join(tones[:3]) + ".")
    cadence = getattr(vp, "cadence", None)
    if cadence:
        lines.append(f"My cadence: {cadence}.")
    use = getattr(vocab, "use", None) or []
    avoid = getattr(vocab, "avoid", None) or []
    if use:
        lines.append("Words I use: " + ", ".join(use[:10]) + ".")
    if avoid:
        lines.append("Words I never use: " + ", ".join(avoid[:10]) + ".")
    samples = getattr(bc, "voice_samples", None) or []
    if samples:
        excerpts = [str(getattr(s, "text", s))[:160] for s in samples[:3]]
        lines.append("How I actually talk:\n" + "\n---\n".join(excerpts))
    return "\n".join(lines)


def clean_script(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    text = re.sub(r"\[[^\]]*\]|\([^)]*(?:pause|beat|music)[^)]*\)", "", text, flags=re.I)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


async def draft_script(ctx: Any, topic: str, brief: str, seconds: int, rules: Iterable[str] = (),
                       number_sources: Optional[Iterable[str]] = None,
                       fair_housing: bool = False) -> str:
    """Foundation-voiced spoken script (contract #3: every generation goes
    through brand context). Deterministic guards run on the result; one redraft
    is allowed, then the step fails rather than ship an invented number or a
    Fair Housing problem."""
    try:
        from schemas.foundation import BrandContextTaskType
        task_type: Any = BrandContextTaskType.scout_remix_script
    except Exception:  # pragma: no cover — schema always importable in-app
        task_type = "scout_remix_script"
    bc = await ctx.brand_context(task_type, topic=topic)
    words = int(seconds * WORDS_PER_SECOND)
    all_rules = [
        "Spoken words only — no stage directions, headings, labels, emojis or hashtags.",
        f"About {words} words (≈{seconds} seconds). Hook in the first sentence.",
        "Plain English, no corporate-speak, never mention being an AI.",
        NO_FABRICATION_RULE,
    ] + list(rules)
    system = ("You write short vertical-video scripts in my voice.\n" + _voice_block(bc) +
              "\n\nRules:\n- " + "\n- ".join(all_rules))
    sources = list(number_sources) if number_sources is not None else None

    problems: List[str] = []
    script = ""
    for attempt in range(2):
        user = brief
        if problems:
            user += ("\n\nYour last draft broke the rules: " + "; ".join(problems) +
                     ". Rewrite it without those.")
        script = clean_script(await _complete(system, user))
        problems = []
        if sources is not None:
            bad = fabricated_numbers(script, sources)
            if bad:
                problems.append("it used numbers I never gave you (" + ", ".join(bad) + ")")
        if fair_housing:
            flags = fair_housing_flags(script)
            if flags:
                problems.append("it used Fair Housing red-flag language (" + ", ".join(flags) + ")")
        if not script:
            problems.append("it was empty")
        if not problems:
            return script
    raise RuntimeError("Couldn't get a clean script — the draft kept " + problems[0] +
                       ". Adjust the inputs and run it again.")


# ── persona ───────────────────────────────────────────────────────────────────

def resolve_persona(photo_id: str) -> Path:
    """AI Persona photo id → file, only inside data/ai_persona/ (likeness rule)."""
    photo_id = (photo_id or "").strip()
    if not photo_id or not re.match(r"^[A-Za-z0-9_-]+$", photo_id):
        raise ValueError("Pick one of your AI Persona photos.")
    store_path = DATA_DIR / "ai_persona.json"
    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        store = {}
    match = next((p for p in store.get("photos", []) if p.get("id") == photo_id), None)
    if not match:
        raise ValueError("That persona photo isn't in your AI Persona library.")
    path = Path(match.get("path", "")).resolve()
    root = (DATA_DIR / "ai_persona").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("That persona photo isn't in your AI Persona library.")
    if not path.is_file():
        raise ValueError("That persona photo file is missing — re-upload it on the AI Persona page.")
    return path


# ── runner ────────────────────────────────────────────────────────────────────

async def run(ctx: Any, inp: Dict[str, Any]) -> AgentResult:
    script = (inp.get("script") or "").strip()
    topic = (inp.get("topic") or "").strip()
    if not script and not topic:
        raise ValueError("Give me a script, or a topic and I'll draft one.")
    try:
        length = int(inp.get("length") or 45)
    except (TypeError, ValueError):
        length = 45
    if length not in LENGTHS:
        length = 45
    captions = inp.get("captions", True) is not False
    out_dir = Path(ctx.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── preflight: nothing below spends a cent until all of this passes ──
    async with ctx.step("preflight", "Checking the crew's tools"):
        tts = ctx.media("tts")
        video = ctx.media("avatar_video")
        missing = [n for n, p in (("ElevenLabs", tts), ("Higgsfield", video)) if p is None]
        if missing:
            raise RuntimeError(" and ".join(missing) + " not connected — Avatar Video needs both. "
                               "Add the keys to .env and restart.")
        if base.public_file_url(ctx.job_id, "probe") is None:
            raise RuntimeError("Higgsfield has to fetch your photo and voice from a public address — "
                               "set PODCLICK_PUBLIC_MEDIA_BASE_URL to an https origin that serves this "
                               "studio, then restart.")
        persona = resolve_persona(inp.get("persona_photo") or "")
        planned = estimate_seconds(script) if script else float(length)
        try:
            if script:
                tts.check_text(script)
            video.check_seconds(planned)
        except MediaProviderError as exc:
            raise base.as_user_error(exc)

    if topic and not script:
        ctx.check_cancelled()
        async with ctx.step("script", "Drafting the script in your voice"):
            script = await draft_script(
                ctx, topic=topic, seconds=length,
                brief=f"Write a {length}-second talking-head script about: {topic}",
                number_sources=[topic],
            )
            try:
                tts.check_text(script)
                video.check_seconds(estimate_seconds(script))
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
    segments = ffmpeg_local.plan_segments(duration, max_seg=15.0, min_seg=2.0)
    shot_seconds = [min(15, max(2, int(math.ceil(s)))) for s in segments]
    async with ctx.step("render", f"Rendering {len(segments)} shot(s) on Higgsfield"):
        try:
            video.check_seconds(float(sum(shot_seconds)))   # exact cap, before any submit
        except MediaProviderError as exc:
            raise base.as_user_error(exc)
        photo_name = "persona" + persona.suffix.lower()
        shutil.copyfile(persona, out_dir / photo_name)
        image_url = base.public_file_url(ctx.job_id, photo_name)
        pieces = await ffmpeg_local.split_audio(voice_path, out_dir, segments, stem="voice_part")
        shots: List[Path] = []
        for i, (piece, secs) in enumerate(zip(pieces, shot_seconds)):
            ctx.check_cancelled()
            dest = out_dir / f"shot_{i:02d}.mp4"
            try:
                asset = await video.generate(
                    GenerationRequest(capability="avatar_video", prompt=AVATAR_PROMPT, duration_s=secs,
                                      image_url=image_url,
                                      audio_url=base.public_file_url(ctx.job_id, piece.name),
                                      source_image=persona),
                    dest)
            except MediaProviderError as exc:
                raise base.as_user_error(exc)
            _record_usage(ctx, asset.usage)
            shots.append(dest)

    ctx.check_cancelled()
    async with ctx.step("assemble", "Cutting it together" + (" with captions" if captions else "")):
        normalized = []
        for i, (shot, seg) in enumerate(zip(shots, segments)):
            normalized.append(await ffmpeg_local.normalize_vertical(shot, out_dir / f"norm_{i:02d}.mp4",
                                                                    duration_s=seg))
        silent = await ffmpeg_local.concat_videos(normalized, out_dir / "avatar_silent.mp4")
        voiced = await ffmpeg_local.mux_audio(silent, voice_path, out_dir / "avatar_voiced.mp4")
        final = out_dir / "avatar.mp4"
        if captions:
            words = ffmpeg_local.estimate_alignment(script, duration)
            await ffmpeg_local.burn_captions(voiced, words, final)
        else:
            voiced.replace(final)

    ctx.add_output(Output(kind="video", label="Avatar video", file=final.name,
                          meta={"duration_s": round(duration, 2), "ai_generated": True,
                                "shots": len(shots)}))
    ctx.add_output(Output(kind="text", label="Script", value=script))
    ctx.add_output(Output(kind="json", label="Usage",
                          value=[v_asset.usage or {},
                                 {"provider": "higgsfield", "unit": "seconds", "amount": sum(shot_seconds)}]))
    return AgentResult(outputs=[], commit=None)
