"""
Home Tour Video (`home_tour`) — listing photos → vertical tour with voiceover
and captions (spec §1.4).

Everything outside is a SOFT requirement:
- Voice: ElevenLabs when connected and `voice` is on; otherwise captions-only
  with a warning.
- Motion: Higgsfield image-to-video per photo when connected AND a public media
  origin is set AND the per-photo shot fits the model's 2–15 s window AND the
  total fits HIGGSFIELD_MAX_SECONDS. Otherwise — or the moment Higgsfield
  errors — a local ffmpeg slow-zoom (Ken Burns), which is free and always works.
  A failed/uncertain Higgsfield submit is never retried; that photo (and every
  remaining one) falls back locally and the job says so.

Script: Foundation-voiced, with a Fair Housing guardrail in the prompt AND a
deterministic red-flag check on the result, plus the no-invented-numbers check
against the listing facts the user typed.
"""

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.agents.contract import AgentResult, Output
from services.agents.runners.avatar_video import DATA_DIR as _DATA_DIR
from services.agents.runners.avatar_video import draft_script
from services.media import base, ffmpeg_local
from services.media.base import (
    GenerationRequest,
    MediaProviderError,
    ProviderRejected,
    SpeechRequest,
)
from services.media.elevenlabs import voice_settings_for

UPLOADS_DIR = _DATA_DIR / "agent_uploads"   # patchable in tests
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
VIDEO_EXT = {".mp4", ".mov"}
MIN_PHOTOS, MAX_PHOTOS = 4, 25
LENGTHS = (30, 60)
MOTION_PROMPT = ("Slow, smooth real-estate camera move through this exact space. Keep every wall, "
                 "window and fixture as shown — do not add or remove anything. No people.")

FAIR_HOUSING_RULE = (
    "Fair Housing: describe the PROPERTY, never the buyer. No protected-class language and no steering — "
    "never say who it's 'perfect for' (families, couples, kids, retirees, singles), never describe a "
    "neighborhood as safe/exclusive, never mention churches, ethnicity, religion or disability."
)


def resolve_upload(upload_id: str) -> Tuple[Path, str]:
    """Upload id → (file, 'image'|'video'), only inside data/agent_uploads/."""
    upload_id = str(upload_id or "").strip()
    if not re.match(r"^[A-Za-z0-9._-]+$", upload_id) or upload_id in (".", ".."):
        raise ValueError("One of the photos has a bad upload id — upload it again.")
    root = UPLOADS_DIR.resolve()
    target = (root / upload_id)
    candidates: List[Path] = []
    if target.is_dir():
        candidates = sorted(p for p in target.iterdir() if p.is_file())
    elif target.is_file():
        candidates = [target]
    else:
        candidates = sorted(root.glob(upload_id + ".*"))
    for path in candidates:
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        ext = resolved.suffix.lower()
        if ext in IMAGE_EXT:
            return resolved, "image"
        if ext in VIDEO_EXT:
            return resolved, "video"
    raise ValueError("One of the photos is missing or isn't an image/clip — upload it again.")


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [v for v in re.split(r"[,\s]+", value) if v]
    return [str(v) for v in (value or [])]


def _record_usage(ctx: Any, usage: Optional[Dict[str, Any]]) -> None:
    add = getattr(ctx, "add_usage", None)
    if usage and callable(add):
        add(usage)


async def run(ctx: Any, inp: Dict[str, Any]) -> AgentResult:
    ids = _as_list(inp.get("photos"))
    if not (MIN_PHOTOS <= len(ids) <= MAX_PHOTOS):
        raise ValueError(f"A tour needs {MIN_PHOTOS}–{MAX_PHOTOS} photos (got {len(ids)}).")
    media = [resolve_upload(i) for i in ids]
    try:
        length = int(inp.get("length") or 30)
    except (TypeError, ValueError):
        length = 30
    if length not in LENGTHS:
        length = 30
    want_voice = str(inp.get("voice", "on")).lower() not in ("off", "false", "0", "no")
    facts = {k: str(inp.get(k) or "").strip() for k in ("address", "price", "beds", "baths", "sqft", "highlights")}
    out_dir = Path(ctx.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    usage: List[Dict[str, Any]] = []

    # ── script ──
    ctx.check_cancelled()
    async with ctx.step("script", "Writing the tour narration"):
        fact_lines = "\n".join(f"{k}: {v}" for k, v in facts.items() if v)
        script = await draft_script(
            ctx, topic=facts["address"] or "home tour", seconds=length, fair_housing=True,
            rules=[FAIR_HOUSING_RULE, "Walk the viewer through the home in photo order, end with a call to book a showing."],
            brief=f"Write a {length}-second listing tour narration from these facts:\n{fact_lines}",
            number_sources=list(facts.values()),
        )

    # ── voice (soft) ──
    voice_path: Optional[Path] = None
    duration = float(length)
    ctx.check_cancelled()
    async with ctx.step("voice", "Recording the narration"):
        tts = ctx.media("tts") if want_voice else None
        if want_voice and tts is None:
            ctx.warn("No voice — ElevenLabs isn't connected. The tour ships with captions only.")
        if tts is not None:
            try:
                asset = await tts.synthesize_speech(
                    SpeechRequest(text=script, settings=voice_settings_for("narration")),
                    out_dir / "voice.mp3")
                voice_path = asset.path
                usage.append(asset.usage or {})
                _record_usage(ctx, asset.usage)
                duration = await ffmpeg_local.probe_duration(voice_path) or asset.duration_s or duration
            except MediaProviderError as exc:
                ctx.warn(exc.user_message + " The tour ships with captions only.")
                voice_path = None

    # ── motion: Higgsfield per photo when it fits, local slow-zoom otherwise ──
    per = duration / len(media)
    hf = ctx.media("image_to_video")
    use_hf = False
    if hf is not None:
        reason = None
        hf_secs = int(math.ceil(per))
        if base.public_file_url(ctx.job_id, "probe") is None:
            reason = "no public media address (PODCLICK_PUBLIC_MEDIA_BASE_URL)"
        elif hf_secs < 2 or hf_secs > 15:
            reason = f"each photo gets {per:.1f}s and Higgsfield shots must be 2–15s"
        else:
            try:
                hf.check_seconds(float(hf_secs * sum(1 for _, k in media if k == "image")))
            except MediaProviderError as exc:
                reason = exc.user_message
        if reason:
            ctx.warn(f"Used local slow-zoom instead of Higgsfield motion — {reason}.")
        else:
            use_hf = True

    clips: List[Path] = []
    hf_shots = 0
    ctx.check_cancelled()
    async with ctx.step("motion", "Putting the photos in motion"):
        for i, (path, kind) in enumerate(media):
            ctx.check_cancelled()
            dest = out_dir / f"scene_{i:02d}.mp4"
            if kind == "video":
                clips.append(await ffmpeg_local.normalize_vertical(path, dest, duration_s=per))
                continue
            if use_hf:
                name = f"photo_{i:02d}{path.suffix.lower()}"
                (out_dir / name).write_bytes(path.read_bytes())
                try:
                    asset = await hf.generate(
                        GenerationRequest(capability="image_to_video", prompt=MOTION_PROMPT,
                                          duration_s=int(math.ceil(per)),
                                          image_url=base.public_file_url(ctx.job_id, name),
                                          source_image=path),
                        out_dir / f"hf_{i:02d}.mp4")
                    usage.append(asset.usage or {})
                    _record_usage(ctx, asset.usage)
                    hf_shots += 1
                    clips.append(await ffmpeg_local.normalize_vertical(asset.path, dest, duration_s=per))
                    continue
                except MediaProviderError as exc:
                    if isinstance(exc, ProviderRejected):
                        ctx.warn(f"Photo {i + 1}: {exc.user_message} Used a slow-zoom for that one.")
                    else:
                        # Never resubmit after an error: the rest go local.
                        use_hf = False
                        ctx.warn(f"Photo {i + 1}: {exc.user_message} Switched the rest of the tour to "
                                 f"local slow-zoom.")
            clips.append(await ffmpeg_local.ken_burns(path, dest, per, variant=i))

    # ── assemble ──
    ctx.check_cancelled()
    async with ctx.step("assemble", "Cutting the tour with captions"):
        silent = await ffmpeg_local.concat_videos(clips, out_dir / "tour_silent.mp4")
        cut = silent
        if voice_path is not None:
            cut = await ffmpeg_local.mux_audio(silent, voice_path, out_dir / "tour_voiced.mp4")
        final = out_dir / "tour.mp4"
        await ffmpeg_local.burn_captions(cut, ffmpeg_local.estimate_alignment(script, duration), final)

    ctx.add_output(Output(kind="video", label="Home tour", file=final.name,
                          meta={"duration_s": round(duration, 2), "photos": len(media),
                                "motion_shots": {"higgsfield": hf_shots, "ken_burns": len(media) - hf_shots},
                                "voice": voice_path is not None, "ai_generated": hf_shots > 0}))
    ctx.add_output(Output(kind="text", label="Script", value=script))
    ctx.add_output(Output(kind="json", label="Usage", value=usage))
    return AgentResult(outputs=[], commit=None)
