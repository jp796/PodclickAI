"""
Clip extraction pipeline.
Transcribes a video, scores segments for virality, extracts top clips.
"""

import asyncio
import io
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from faster_whisper import WhisperModel

POWER_WORDS = {
    "secret","mistake","truth","money","strategy","never","always","revealed",
    "warning","wrong","biggest","top","best","worst","simple","hack","tip",
    "trick","key","critical","real","honest","exact","proven","fail","win",
    "lose","broke","rich","free","fast","easy","hard","fear","risk","deal",
    "close","agent","client","lead","buyer","seller","invest","profit",
    "commission","growth","success","failure","system","method","formula",
}

_model_cache: dict[str, WhisperModel] = {}


def _get_model(model_size: str) -> WhisperModel:
    if model_size not in _model_cache:
        _model_cache[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _model_cache[model_size]


# ── Transcription ──────────────────────────────────────────────────────────────

def transcribe_video(
    video_path: str,
    model_size: str = "tiny",
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    def log(msg):
        if progress_cb: progress_cb(msg)

    log(f"Extracting audio from video…")

    # Pull audio as raw WAV bytes via ffmpeg pipe
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ar", "16000", "-ac", "1",
        "-f", "wav", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extract failed: {proc.stderr.decode()[:300]}")

    audio_bytes = proc.stdout
    log(f"Transcribing with Whisper ({model_size})… this takes a few minutes")

    model = _get_model(model_size)

    import numpy as np
    import wave

    wav_buf = io.BytesIO(audio_bytes)
    with wave.open(wav_buf) as wf:
        total_frames = wf.getnframes()
        sample_rate  = wf.getframerate()
        raw          = wf.readframes(total_frames)
    audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    total_secs = len(audio_np) / sample_rate

    segments_out = []
    words_out    = []
    last_report  = 0.0

    segments_iter, info = model.transcribe(
        audio_np,
        word_timestamps=True,
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
    )

    full_text_parts = []
    for seg in segments_iter:
        seg_words = []
        for w in (seg.words or []):
            word_entry = {"word": w.word.strip(), "start": w.start, "end": w.end}
            words_out.append(word_entry)
            seg_words.append(word_entry)

        segments_out.append({
            "text":  seg.text.strip(),
            "start": seg.start,
            "end":   seg.end,
            "words": seg_words,
        })
        full_text_parts.append(seg.text.strip())

        if seg.end - last_report >= 30:
            pct = min(100, int(seg.end / total_secs * 100)) if total_secs else 0
            log(f"Transcribing… {pct}% ({int(seg.end)}s / {int(total_secs)}s)")
            last_report = seg.end

    log(f"Transcription complete — {len(words_out)} words, {len(segments_out)} segments")
    return {
        "segments": segments_out,
        "words":    words_out,
        "text":     " ".join(full_text_parts),
    }


# ── Clip scoring ──────────────────────────────────────────────────────────────

def score_and_select_clips(
    segments: list,
    words: list,
    num_clips: int = 3,
    min_dur: float = 45.0,
    max_dur: float = 90.0,
    step: float = 5.0,
) -> list:
    if not words:
        return []

    total_end = words[-1]["end"] if words else 0

    # Build scored windows
    windows = []
    t = 0.0
    while t + min_dur <= total_end:
        for dur in (60.0, 75.0, 90.0, 45.0):
            end = t + dur
            if end > total_end:
                end = total_end
            if end - t < min_dur:
                continue

            window_words = [w for w in words if w["start"] >= t and w["end"] <= end]
            if len(window_words) < 10:
                continue

            # Word density
            wps = len(window_words) / (end - t)

            # Power word score
            pw_hits = sum(1 for w in window_words if w["word"].lower().strip(".,!?\"'") in POWER_WORDS)
            pw_score = pw_hits / max(len(window_words), 1)

            # Sentence boundary score
            sent_score = 0.0
            first_word = window_words[0]["word"] if window_words else ""
            last_word  = window_words[-1]["word"] if window_words else ""
            if first_word[:1].isupper():
                sent_score += 0.1
            if last_word.endswith((".", "!", "?")):
                sent_score += 0.1

            transcript = " ".join(w["word"] for w in window_words)

            windows.append({
                "start":      t,
                "end":        end,
                "duration":   end - t,
                "wps":        wps,
                "pw_score":   pw_score,
                "sent_score": sent_score,
                "transcript": transcript,
                "word_count": len(window_words),
            })
            break  # one duration per start position

        t += step

    if not windows:
        return []

    # Normalize density and power scores
    max_wps = max(w["wps"] for w in windows) or 1
    max_pw  = max(w["pw_score"] for w in windows) or 1

    for w in windows:
        density_n = w["wps"] / max_wps
        power_n   = w["pw_score"] / max_pw
        w["score"] = density_n * 0.4 + power_n * 0.4 + w["sent_score"] * 0.2

    windows.sort(key=lambda x: x["score"], reverse=True)

    # Select non-overlapping top clips
    selected = []
    for win in windows:
        overlap = any(
            not (win["end"] <= s["start"] or win["start"] >= s["end"])
            for s in selected
        )
        if not overlap:
            selected.append(win)
        if len(selected) >= num_clips:
            break

    # Sort by time order
    selected.sort(key=lambda x: x["start"])
    for i, clip in enumerate(selected):
        clip["index"] = i + 1

    return selected


# ── Clip extraction ───────────────────────────────────────────────────────────

def extract_clip(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    dur = end - start

    # Try stream copy first (fast, lossless)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(dur),
        "-c", "copy",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True)

    if proc.returncode != 0:
        # Fallback: re-encode
        if progress_cb: progress_cb("Stream copy failed — re-encoding clip…")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", video_path,
            "-t", str(dur),
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg clip extraction failed: {proc.stderr.decode()[:300]}")

    return output_path


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def run_clip_pipeline(
    job_id: str,
    video_path: str,
    model_size: str,
    num_clips: int,
    output_dir: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    loop = asyncio.get_event_loop()
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    def log(msg):
        print(f"[clip/{job_id}] {msg}")
        if progress_cb: progress_cb(msg)

    # 1 — Transcribe
    log("Starting transcription…")
    result = await loop.run_in_executor(
        None, transcribe_video, video_path, model_size, progress_cb
    )
    segments = result["segments"]
    words    = result["words"]
    text     = result["text"]

    # 2 — Score
    log(f"Scoring segments for best clips…")
    scored = await loop.run_in_executor(
        None, score_and_select_clips, segments, words, num_clips
    )
    log(f"Selected {len(scored)} clips from {len(words)} words")

    # 3 — Extract each clip
    clips = []
    for i, clip in enumerate(scored):
        log(f"Extracting clip {i+1}/{len(scored)} ({clip['start']:.0f}s – {clip['end']:.0f}s, score {clip['score']:.2f})")
        out = str(Path(output_dir) / f"clip_{job_id}_{i+1}_raw.mp4")
        path = await loop.run_in_executor(
            None, extract_clip, video_path, clip["start"], clip["end"], out, progress_cb
        )
        clips.append({**clip, "raw_path": path})

    log(f"Extraction complete — {len(clips)} clips ready for subtitles")

    return {
        "clips":      clips,
        "transcript": text,
        "word_count": len(words),
        "words":      words,
    }
