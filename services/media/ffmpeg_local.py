"""
services/media/ffmpeg_local.py — the always-available local fallback (spec §4.2).

`FfmpegLocalProvider` advertises exactly one capability, `ken_burns`, and is
configured whenever ffmpeg/ffprobe are on PATH. The module also holds the local
assembly helpers the media runners share: vertical normalize, concat, mux,
split, two-pass loudnorm, colour plates, and the caption burn (which reuses the
clip pipeline's viral ASS style in `pipeline/subtitles.py` — same look as clips).

Every ffmpeg call is an `asyncio` subprocess with a timeout, so a render never
blocks the event loop, and a failure surfaces the ffmpeg stderr tail.
"""

import asyncio
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.media.base import MediaProvider, ProviderError

# Output canvas for every agent video. Tests shrink this for speed.
VERTICAL: Tuple[int, int] = (1080, 1920)
FPS = 30


class FfmpegError(ProviderError):
    pass


class FfmpegLocalProvider(MediaProvider):
    name = "ffmpeg_local"
    capabilities = frozenset({"ken_burns"})

    def is_configured(self) -> bool:
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def run_ffmpeg(args: Sequence[str], timeout: float = 900.0) -> str:
    """Run `ffmpeg -y -hide_banner <args>`; return stderr. Raises FfmpegError."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + [str(a) for a in args]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FfmpegError("ffmpeg timed out", provider="ffmpeg_local",
                          user_message="The local render took too long and was stopped.")
    stderr = (err or b"").decode("utf-8", "replace")
    if proc.returncode != 0:
        tail = " | ".join(stderr.strip().splitlines()[-3:])
        raise FfmpegError(f"ffmpeg failed ({proc.returncode}): {tail}", provider="ffmpeg_local",
                          user_message="The local render failed — ffmpeg couldn't finish the cut.")
    return stderr


async def probe_duration(path: Path) -> Optional[float]:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except (ValueError, AttributeError):
        return None


def _size(size: Optional[Tuple[int, int]]) -> Tuple[int, int]:
    return size or VERTICAL


def _fill_filter(w: int, h: int) -> str:
    # Scale to cover, then centre-crop: no letterboxing on a vertical canvas.
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1,fps={FPS},format=yuv420p")


async def ken_burns(image: Path, dest: Path, duration_s: float,
                    size: Optional[Tuple[int, int]] = None, variant: int = 0) -> Path:
    """Slow zoom (alternating in/out by `variant`) over a still — free, always available."""
    w, h = _size(size)
    frames = max(1, int(round(duration_s * FPS)))
    if variant % 2 == 0:
        zoom = "min(zoom+0.0010,1.20)"
    else:
        zoom = "if(eq(on,1),1.20,max(zoom-0.0010,1.0))"
    # Upscale first so zoompan has pixels to work with (avoids jitter on small inputs).
    vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
          f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},"
          f"setsar=1,format=yuv420p")
    await run_ffmpeg(["-loop", "1", "-i", str(image), "-vf", vf, "-frames:v", str(frames),
                      "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(dest)])
    return dest


async def normalize_vertical(src: Path, dest: Path, duration_s: Optional[float] = None,
                             size: Optional[Tuple[int, int]] = None) -> Path:
    """Re-encode any clip to the agent canvas (silent).

    With `duration_s` the output is EXACTLY that long: trimmed if the source is
    longer, last frame held if it is shorter. A short shot must never shorten
    the cut — the later `mux_audio(-shortest)` would silently truncate the voice.
    """
    w, h = _size(size)
    vf = _fill_filter(w, h)
    args: List[str] = ["-i", str(src)]
    if duration_s:
        vf += f",tpad=stop_mode=clone:stop_duration={duration_s:.3f}"
        args += ["-t", f"{duration_s:.3f}"]
    args += ["-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "22", str(dest)]
    await run_ffmpeg(args)
    return dest


async def color_plate(dest: Path, duration_s: float, size: Optional[Tuple[int, int]] = None,
                      color: str = "black") -> Path:
    w, h = _size(size)
    await run_ffmpeg(["-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={FPS}:d={duration_s:.3f}",
                      "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(dest)])
    return dest


def _concat_list(paths: Sequence[Path], list_path: Path) -> Path:
    lines = []
    for p in paths:
        safe = str(Path(p).resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


async def concat_videos(paths: Sequence[Path], dest: Path) -> Path:
    """Concat clips that share codec/size (everything through normalize/ken_burns does)."""
    if not paths:
        raise FfmpegError("nothing to concat", provider="ffmpeg_local")
    lst = _concat_list(paths, Path(dest).with_suffix(".concat.txt"))
    await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(dest)])
    return dest


async def concat_audio(paths: Sequence[Path], dest: Path) -> Path:
    if not paths:
        raise FfmpegError("nothing to concat", provider="ffmpeg_local")
    lst = _concat_list(paths, Path(dest).with_suffix(".concat.txt"))
    await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst),
                      "-c:a", "libmp3lame", "-b:a", "128k", str(dest)])
    return dest


async def split_audio(src: Path, out_dir: Path, segments: Sequence[float],
                      stem: str = "part") -> List[Path]:
    """Cut `src` into consecutive pieces with the given lengths (seconds)."""
    out: List[Path] = []
    start = 0.0
    for i, seg in enumerate(segments):
        dest = Path(out_dir) / f"{stem}_{i:02d}.mp3"
        await run_ffmpeg(["-ss", f"{start:.3f}", "-t", f"{seg:.3f}", "-i", str(src),
                          "-c:a", "libmp3lame", "-b:a", "128k", str(dest)])
        out.append(dest)
        start += seg
    return out


async def mux_audio(video: Path, audio: Path, dest: Path) -> Path:
    await run_ffmpeg(["-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                      "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(dest)])
    return dest


async def loudnorm(src: Path, dest: Path, target_i: float = -16.0) -> Path:
    """Two-pass EBU R128 loudnorm — the `_ship_it_extract_audio` recipe, so voice
    tracks sit at episode loudness."""
    base_filter = f"loudnorm=I={target_i}:TP=-1.5:LRA=11"
    stderr = await run_ffmpeg(["-loglevel", "info", "-nostats", "-i", str(src),
                               "-af", base_filter + ":print_format=json", "-f", "null", "-"])
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    measured: Dict[str, Any] = json.loads(match.group(0)) if match else {}
    if measured.get("input_i") and measured.get("input_i") not in ("-inf", "inf"):
        af = (f"{base_filter}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
              f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
              f":offset={measured.get('target_offset', 0)}:linear=true")
    else:
        af = base_filter
    await run_ffmpeg(["-i", str(src), "-af", af, "-ar", "44100", "-c:a", "libmp3lame",
                      "-b:a", "128k", str(dest)])
    return dest


def plan_segments(total_s: float, max_seg: float = 15.0, min_seg: float = 2.0) -> List[float]:
    """Split a duration into the fewest equal pieces that each fit [min_seg, max_seg]."""
    if total_s < min_seg:
        raise ValueError(f"{total_s:.2f}s is shorter than the {min_seg:.0f}s minimum")
    n = max(1, int(math.ceil(total_s / max_seg)))
    piece = total_s / n
    return [piece] * n


def estimate_alignment(text: str, duration_s: float) -> List[Dict[str, Any]]:
    """Word timings spread across `duration_s` by character weight.

    Used because the only verified ElevenLabs TTS endpoint returns bare mp3 (no
    timestamps). Good enough for 2–3 word caption groups; marked `estimated`.
    """
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if not words or duration_s <= 0:
        return []
    weights = [len(w) + 1 for w in words]   # +1 ≈ the space / breath between words
    total = float(sum(weights))
    out: List[Dict[str, Any]] = []
    t = 0.0
    for word, wt in zip(words, weights):
        span = duration_s * wt / total
        out.append({"word": word, "start": round(t, 3), "end": round(t + span * 0.92, 3)})
        t += span
    out[-1]["end"] = round(duration_s, 3)
    return out


async def burn_captions(video: Path, words: List[Dict[str, Any]], dest: Path,
                        size: Optional[Tuple[int, int]] = None) -> Path:
    """Burn the viral word-highlight captions (pipeline/subtitles.py) into `video`."""
    from pipeline.subtitles import generate_ass_subtitles, burn_subtitles

    w, h = _size(size)
    ass_path = Path(dest).with_suffix(".ass")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, generate_ass_subtitles, words, w, h, str(ass_path))
    try:
        await loop.run_in_executor(None, burn_subtitles, str(video), str(ass_path), str(dest), None)
    except RuntimeError as exc:
        raise FfmpegError(str(exc), provider="ffmpeg_local",
                          user_message="Couldn't burn the captions in — ffmpeg refused the subtitle pass.")
    return dest
