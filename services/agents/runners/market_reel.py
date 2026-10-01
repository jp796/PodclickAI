"""
Market Update Reel (`market_reel`) — a 30–90 s vertical neighborhood / market
update in your voice (spec §1.4).

Script: Foundation-voiced. Uses ONLY numbers the user typed; with none it speaks
in trends. Enforced twice: in the prompt, and by a deterministic check that
every number in the draft appears in the user's own inputs (one redraft, then
the step fails — an invented stat never ships).

Voice: ElevenLabs when connected; otherwise captions-only with a warning.

Visuals: Pexels stock through `pipeline/broll.py`'s own search/download helpers
(fixed User-Agent, same API key handling). `plan_broll()` itself is NOT used:
it plans cutaways around a face cam (keeps the first 30 s and last 15 s on the
speaker), and a reel has no face cam — so scenes are planned deterministically
from the area name instead, with no LLM. Scenes stock can't fill are generated
with `text_to_video` when a provider is configured (off by default — no
verified Higgsfield text-to-video model), else filled with a plain plate and
a warning.
"""

import asyncio
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.agents.contract import AgentResult, Output
from services.agents.runners.avatar_video import draft_script
from services.media import base, ffmpeg_local
from services.media.base import GenerationRequest, MediaProviderError, SpeechRequest
from services.media.elevenlabs import voice_settings_for

LENGTHS = (30, 60, 90)
SCENE_SECONDS = 6.0
_SCENE_QUERIES = (
    "{area} aerial", "{area} downtown", "{area} neighborhood homes", "suburban houses street",
    "real estate house exterior", "home interior living room", "{area} skyline", "aerial neighborhood",
)


def scene_queries(area: str, n: int) -> List[str]:
    return [_SCENE_QUERIES[i % len(_SCENE_QUERIES)].format(area=area) for i in range(n)]


def _pexels_search(query: str, key: str, min_duration: int) -> Optional[str]:
    from pipeline.broll import _pexels_search as search
    return search(query, key, min_duration=min_duration)


def _pexels_download(url: str, dest: str) -> bool:
    from pipeline.broll import _download_clip
    return _download_clip(url, dest)


def _record_usage(ctx: Any, usage: Optional[Dict[str, Any]]) -> None:
    add = getattr(ctx, "add_usage", None)
    if usage and callable(add):
        add(usage)


async def run(ctx: Any, inp: Dict[str, Any]) -> AgentResult:
    area = (inp.get("area") or "").strip()
    if not area:
        raise ValueError("Which area is this update for?")
    angle = (inp.get("angle") or "").strip()
    numbers = (inp.get("numbers") or "").strip()
    try:
        length = int(inp.get("length") or 60)
    except (TypeError, ValueError):
        length = 60
    if length not in LENGTHS:
        length = 60
    out_dir = Path(ctx.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    usage: List[Dict[str, Any]] = []

    ctx.check_cancelled()
    async with ctx.step("script", "Writing the market update"):
        brief = f"Write a {length}-second market update reel for {area}."
        if angle:
            brief += f" Angle: {angle}."
        brief += (f"\nNumbers I'm giving you (use only these): {numbers}" if numbers
                  else "\nI'm giving you no numbers — talk trends only, no figures at all.")
        script = await draft_script(ctx, topic=f"{area} market update", seconds=length, brief=brief,
                                    number_sources=[numbers, area, angle])

    voice_path: Optional[Path] = None
    duration = float(length)
    ctx.check_cancelled()
    async with ctx.step("voice", "Recording the update"):
        tts = ctx.media("tts")
        if tts is None:
            ctx.warn("No voice — ElevenLabs not connected. The reel ships with captions only.")
        else:
            try:
                asset = await tts.synthesize_speech(
                    SpeechRequest(text=script, settings=voice_settings_for("narration")),
                    out_dir / "voice.mp3")
                voice_path = asset.path
                usage.append(asset.usage or {})
                _record_usage(ctx, asset.usage)
                duration = await ffmpeg_local.probe_duration(voice_path) or asset.duration_s or duration
            except MediaProviderError as exc:
                ctx.warn(exc.user_message + " The reel ships with captions only.")

    n = max(3, int(math.ceil(duration / SCENE_SECONDS)))
    per = duration / n
    queries = scene_queries(area, n)
    scenes: List[Optional[Path]] = [None] * n
    ctx.check_cancelled()
    async with ctx.step("visuals", "Pulling b-roll for the reel"):
        key = str(base.setting("pexels_api_key", "") or "")
        loop = asyncio.get_running_loop()
        if not key:
            ctx.warn("No stock footage — PEXELS_API_KEY isn't set.")
        else:
            seen = set()
            for i, q in enumerate(queries):
                ctx.check_cancelled()
                url = await loop.run_in_executor(None, _pexels_search, q, key, max(3, int(per)))
                if not url or url in seen:
                    continue
                seen.add(url)
                raw = out_dir / f"stock_{i:02d}.mp4"
                ok = await loop.run_in_executor(None, _pexels_download, url, str(raw))
                if ok:
                    scenes[i] = await ffmpeg_local.normalize_vertical(
                        raw, out_dir / f"scene_{i:02d}.mp4", duration_s=per)

        empty = [i for i, s in enumerate(scenes) if s is None]
        t2v = ctx.media("text_to_video") if empty else None
        if t2v is not None:
            secs = min(15, max(2, int(math.ceil(per))))
            try:
                t2v.check_seconds(float(secs * len(empty)))
                for i in list(empty):
                    asset = await t2v.generate(
                        GenerationRequest(capability="text_to_video", duration_s=secs,
                                          prompt=f"Cinematic vertical b-roll: {queries[i]}. No people, no text."),
                        out_dir / f"gen_{i:02d}.mp4")
                    usage.append(asset.usage or {})
                    _record_usage(ctx, asset.usage)
                    scenes[i] = await ffmpeg_local.normalize_vertical(
                        asset.path, out_dir / f"scene_{i:02d}.mp4", duration_s=per)
            except MediaProviderError as exc:
                ctx.warn(exc.user_message + " Filled the rest with plain plates.")
        empty = [i for i, s in enumerate(scenes) if s is None]
        if empty:
            if len(empty) < n:
                ctx.warn(f"{len(empty)} of {n} scenes had no footage — filled with a plain plate.")
            for i in empty:
                scenes[i] = await ffmpeg_local.color_plate(out_dir / f"scene_{i:02d}.mp4", per)

    ctx.check_cancelled()
    async with ctx.step("assemble", "Cutting the reel with captions"):
        silent = await ffmpeg_local.concat_videos([s for s in scenes if s], out_dir / "reel_silent.mp4")
        cut = silent
        if voice_path is not None:
            cut = await ffmpeg_local.mux_audio(silent, voice_path, out_dir / "reel_voiced.mp4")
        final = out_dir / "reel.mp4"
        await ffmpeg_local.burn_captions(cut, ffmpeg_local.estimate_alignment(script, duration), final)

    ctx.add_output(Output(kind="video", label="Market update reel", file=final.name,
                          meta={"duration_s": round(duration, 2), "scenes": n,
                                "voice": voice_path is not None}))
    ctx.add_output(Output(kind="text", label="Script", value=script))
    ctx.add_output(Output(kind="json", label="Usage", value=usage))
    return AgentResult(outputs=[], commit=None)
