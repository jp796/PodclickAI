"""
Voiceover (`voiceover`) — turn a script into a clean voice track (spec §1.4).

Speaks the script VERBATIM: no Foundation, no rewriting. Voiceover's one job is
voicing. Hard requirement: `tts` (ElevenLabs). With no provider the run stops
before any network call.

Steps: preflight (caps, voice) → voice (ElevenLabs TTS) → level (two-pass
loudnorm to −16 LUFS, fail-soft) → outputs.

Alignment: the verified ElevenLabs endpoint returns bare mp3, so word timings
are estimated from the measured duration (`meta.source = "estimated"`). The
Avatar / Home Tour / Market Reel agents use the same estimate for captions.
"""

import json
from pathlib import Path
from typing import Any, Dict

from services.agents.contract import AgentResult, Output
from services.media import base, ffmpeg_local
from services.media.base import MediaProviderError, SpeechRequest
from services.media.elevenlabs import voice_settings_for

PURPOSES = ("intro", "outro", "ad read", "narration")


def _record_usage(ctx: Any, usage: Dict[str, Any]) -> None:
    add = getattr(ctx, "add_usage", None)
    if callable(add):
        add(usage)


async def run(ctx: Any, inp: Dict[str, Any]) -> AgentResult:
    script = inp.get("script") or ""
    if not script.strip():
        raise ValueError("Give me a script to read.")
    purpose = (inp.get("purpose") or "narration").strip().lower()
    with_timestamps = inp.get("with_timestamps", True) is not False

    async with ctx.step("preflight", "Checking the mic"):
        tts = ctx.media("tts")
        if tts is None:
            raise RuntimeError("ElevenLabs isn't connected — add ELEVENLABS_API_KEY to .env and restart the server.")
        check_text = getattr(tts, "check_text", None)
        if callable(check_text):
            try:
                check_text(script.strip())
            except MediaProviderError as exc:
                raise base.as_user_error(exc)
        voice_id = (inp.get("voice") or "").strip() or getattr(tts, "default_voice_id", "")
        if not voice_id:
            raise RuntimeError("Pick a voice, or set ELEVENLABS_VOICE_ID in .env and restart.")

    ctx.check_cancelled()
    out_dir = Path(ctx.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "voiceover_raw.mp3"
    final_path = out_dir / "voiceover.mp3"

    async with ctx.step("voice", "Recording the voice track"):
        try:
            asset = await tts.synthesize_speech(
                SpeechRequest(text=script, voice_id=voice_id, settings=voice_settings_for(purpose),
                              with_timestamps=with_timestamps),
                raw_path,
            )
        except MediaProviderError as exc:
            raise base.as_user_error(exc)
        _record_usage(ctx, asset.usage or {})

    ctx.check_cancelled()
    async with ctx.step("level", "Leveling to episode loudness"):
        try:
            await ffmpeg_local.loudnorm(raw_path, final_path)
            raw_path.unlink(missing_ok=True)
        except Exception:
            raw_path.replace(final_path)
            ctx.warn("Couldn't level the track — kept it as recorded.")

    duration = await ffmpeg_local.probe_duration(final_path) or asset.duration_s
    ctx.add_output(Output(kind="audio", label="Voiceover", file=final_path.name,
                          meta={"duration_s": duration, "characters": len(script.strip()),
                                "purpose": purpose}))
    if with_timestamps and duration:
        words = ffmpeg_local.estimate_alignment(script, duration)
        (out_dir / "alignment.json").write_text(json.dumps({"source": "estimated", "words": words}),
                                                encoding="utf-8")
        ctx.add_output(Output(kind="file", label="Word timings", file="alignment.json",
                              meta={"source": "estimated", "words": len(words)}))
    ctx.add_output(Output(kind="json", label="Usage", value=[asset.usage or {}]))
    return AgentResult(outputs=[], commit=None)
