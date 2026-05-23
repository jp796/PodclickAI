"""
Video processing pipeline for PodClickAI Video Studio.

Features:
  - Audio extraction from MP4/MOV for Whisper transcription
  - Filler word cuts applied to video stream
  - Multi-clip assembly (intro/main/commercial/outro video)
  - Audio loudness normalization on video
  - Subtitle burning (SRT → burned-in captions, styled for Reels/TikTok/YouTube)
  - Short-form clip export under the Clips tab
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def _run(cmd: list, timeout: int = 600) -> tuple[int, str, str]:
    """Run an ffmpeg command, return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def extract_audio(video_path: str, output_wav: str) -> bool:
    """
    Extract 16kHz mono WAV from a video file for Whisper transcription.
    Returns True on success.
    """
    code, _, err = _run([
        "ffmpeg", "-y", "-i", video_path,
        "-vn",                        # no video
        "-acodec", "pcm_s16le",       # PCM 16-bit
        "-ar", "16000",               # 16 kHz
        "-ac", "1",                   # mono
        output_wav,
    ])
    if code != 0:
        print(f"[video] extract_audio failed: {err[-500:]}")
    return code == 0


def assemble_video_clips(
    clips: list,          # [{"path": str, "type": "intro"|"main"|"commercial"|"outro"}]
    output_path: str,
    progress_cb=None,
) -> bool:
    """
    Concatenate video clips in order using the ffmpeg concat demuxer.
    All clips must have the same codec, resolution, and frame rate for -c copy.
    Falls back to re-encode if -c copy fails.
    Returns True on success.
    """
    if not clips:
        return False

    # Write filelist for concat demuxer
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for clip in clips:
            f.write(f"file '{clip['path']}'\n")
        filelist = f.name

    try:
        if progress_cb:
            progress_cb("Assembling video clips…")

        # Try stream copy first (fast, lossless)
        code, _, err = _run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", filelist,
            "-c", "copy",
            output_path,
        ])

        if code != 0:
            # Re-encode fallback with H.264/AAC
            if progress_cb:
                progress_cb("Re-encoding clips for compatibility…")
            code, _, err = _run([
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", filelist,
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k",
                output_path,
            ])

        if code != 0:
            print(f"[video] assemble failed: {err[-500:]}")
        return code == 0

    finally:
        os.unlink(filelist)


def cut_video_segments(
    video_path: str,
    segments_to_remove: list,    # [{"start": float, "end": float}]
    output_path: str,
    progress_cb=None,
) -> bool:
    """
    Remove segments from video (for filler word cuts).
    Builds keep-segments from the inverse of remove-segments,
    then splices using ffmpeg concat filter.
    Returns True on success.
    """
    if not segments_to_remove:
        # Nothing to cut — copy as-is
        code, _, _ = _run([
            "ffmpeg", "-y", "-i", video_path,
            "-c", "copy", output_path,
        ])
        return code == 0

    if progress_cb:
        progress_cb("Cutting filler words from video…")

    # Get total duration
    probe_code, probe_out, _ = _run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        video_path,
    ])
    try:
        total_dur = float(json.loads(probe_out)["format"]["duration"])
    except Exception:
        total_dur = 999999.0

    # Build keep segments (inverse of remove)
    removes  = sorted(segments_to_remove, key=lambda s: s["start"])
    keeps    = []
    cursor   = 0.0
    for seg in removes:
        if seg["start"] > cursor:
            keeps.append({"start": cursor, "end": seg["start"]})
        cursor = seg["end"]
    if cursor < total_dur:
        keeps.append({"start": cursor, "end": total_dur})

    if not keeps:
        return False

    # Build select filter for video + audio
    vselect = "+".join(
        f"between(t,{k['start']},{k['end']})" for k in keeps
    )
    aselect = vselect

    code, _, err = _run([
        "ffmpeg", "-y", "-i", video_path,
        "-vf",  f"select='{vselect}',setpts=N/FRAME_RATE/TB",
        "-af",  f"aselect='{aselect}',asetpts=N/SR/TB",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ], timeout=900)

    if code != 0:
        print(f"[video] cut_video_segments failed: {err[-500:]}")
    return code == 0


def normalize_video_audio(
    video_path: str,
    output_path: str,
    target_lufs: float = -16.0,
) -> bool:
    """
    Apply two-pass loudnorm to a video's audio track.
    Video stream is stream-copied (no re-encode).
    Returns True on success.
    """
    # Pass 1: measure
    _, _, err1 = _run([
        "ffmpeg", "-y", "-i", video_path,
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ])

    # Extract loudnorm JSON from stderr
    try:
        start = err1.rfind("{")
        end   = err1.rfind("}") + 1
        info  = json.loads(err1[start:end])
    except Exception:
        info  = {}

    # Pass 2: apply
    lnorm_filter = (
        f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
        f":measured_I={info.get('input_i', -23)}"
        f":measured_TP={info.get('input_tp', -2)}"
        f":measured_LRA={info.get('input_lra', 7)}"
        f":measured_thresh={info.get('input_thresh', -33)}"
        f":offset={info.get('target_offset', 0)}"
        f":linear=true:print_format=summary"
    )

    code, _, err2 = _run([
        "ffmpeg", "-y", "-i", video_path,
        "-c:v", "copy",
        "-af", lnorm_filter,
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ])

    if code != 0:
        print(f"[video] normalize failed: {err2[-400:]}")
    return code == 0


def burn_subtitles(
    video_path: str,
    srt_path: str,
    output_path: str,
    style: str = "reels",    # "reels" | "youtube" | "clean"
    progress_cb=None,
) -> bool:
    """
    Burn SRT subtitles into video using ffmpeg's subtitles filter.

    Styles:
      reels   — Bold white, centered bottom, black outline, large font (TikTok/Reels/Shorts)
      youtube — Smaller white, bottom, semi-transparent box
      clean   — White, small, bottom-third, no box
    """
    if progress_cb:
        progress_cb("Burning subtitles into video…")

    style_map = {
        "reels": (
            "FontName=Arial,FontSize=22,Bold=1,"
            "PrimaryColour=&H00FFFFFF,"    # white
            "OutlineColour=&H00000000,"    # black outline
            "Outline=3,Shadow=1,"
            "Alignment=2,"                 # bottom center
            "MarginV=60"
        ),
        "youtube": (
            "FontName=Arial,FontSize=16,Bold=0,"
            "PrimaryColour=&H00FFFFFF,"
            "BackColour=&H80000000,"       # semi-transparent bg
            "BorderStyle=4,"              # opaque box
            "Alignment=2,MarginV=40"
        ),
        "clean": (
            "FontName=Arial,FontSize=14,Bold=0,"
            "PrimaryColour=&H00FFFFFF,"
            "Outline=2,Shadow=0,"
            "Alignment=2,MarginV=30"
        ),
    }
    force_style = style_map.get(style, style_map["reels"])

    code, _, err = _run([
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"subtitles={srt_path}:force_style='{force_style}'",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy",
        output_path,
    ], timeout=900)

    if code != 0:
        print(f"[video] burn_subtitles failed: {err[-500:]}")
    return code == 0


def segments_to_srt(segments: list, output_path: str) -> str:
    """
    Convert Whisper segment dicts to SRT subtitle file.
    segments: [{"start": float, "end": float, "text": str}]
    Returns the path to the written SRT file.
    """
    def fmt_time(s: float) -> str:
        h  = int(s // 3600)
        m  = int((s % 3600) // 60)
        sc = int(s % 60)
        ms = int((s - int(s)) * 1000)
        return f"{h:02d}:{m:02d}:{sc:02d},{ms:03d}"

    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{fmt_time(seg['start'])} --> {fmt_time(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    return output_path


def export_short_clip(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    srt_path: str = "",
    subtitle_style: str = "reels",
    target_format: str = "vertical",   # "vertical" (9:16) | "square" (1:1) | "original"
    progress_cb=None,
) -> bool:
    """
    Export a short-form clip (TikTok / Reels / YouTube Shorts).
    Optionally crops to vertical/square and burns subtitles.
    """
    if progress_cb:
        progress_cb(f"Exporting clip {start:.0f}s–{end:.0f}s…")

    duration = end - start
    filters  = []

    # Crop filter
    if target_format == "vertical":
        # Crop to 9:16 from center
        filters.append("crop=ih*9/16:ih:(iw-ih*9/16)/2:0")
    elif target_format == "square":
        filters.append("crop=ih:ih:(iw-ih)/2:0")

    # Subtitle filter (must be after crop)
    if srt_path and Path(srt_path).exists():
        style_map = {
            "reels": "FontName=Arial,FontSize=22,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,Alignment=2,MarginV=60",
        }
        force = style_map.get(subtitle_style, style_map["reels"])
        filters.append(f"subtitles={srt_path}:force_style='{force}'")

    vf_arg = ",".join(filters) if filters else "null"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-vf", vf_arg,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        output_path,
    ]
    code, _, err = _run(cmd, timeout=300)
    if code != 0:
        print(f"[video] export_short_clip failed: {err[-400:]}")
    return code == 0


def image_to_video_segment(
    image_path: str,
    output_path: str,
    duration: float = 5.0,
    audio_path: str = "",
    width: int = 1920,
    height: int = 1080,
) -> bool:
    """
    Convert a static image (JPG/PNG) into an MP4 video segment.
    If audio_path is provided, the clip duration matches the audio length.
    Otherwise uses `duration` seconds with no audio track.
    Output is H.264/AAC, letterboxed/pillarboxed to fit target resolution.
    """
    scale_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    )

    if audio_path and Path(audio_path).exists():
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-i", audio_path,
            "-vf", scale_filter,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            output_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-vf", scale_filter,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-an",
            "-t", str(duration),
            output_path,
        ]

    code, _, err = _run(cmd, timeout=120)
    if code != 0:
        print(f"[video] image_to_video_segment failed: {err[-400:]}")
    return code == 0
