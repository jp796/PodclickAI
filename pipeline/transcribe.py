"""
Transcription using faster-whisper (local, no API needed).
Returns full transcript text AND word-level timestamps for filler removal.

Model sizes (speed vs accuracy tradeoff):
  tiny   — fastest, lowest accuracy  (~75 MB download)
  base   — fast, decent accuracy  ← default  (~145 MB)
  small  — good balance  (~466 MB)
  medium — high accuracy, slower  (~1.5 GB)
  large  — best quality, slowest  (~3 GB)
"""

import os
import time
from pathlib import Path
from typing import Optional, Callable
from faster_whisper import WhisperModel


_model_cache: dict[str, WhisperModel] = {}

_MODEL_MB = {
    "tiny": 75, "base": 145, "small": 466,
    "medium": 1500, "large-v3": 3000,
}

# faster-whisper caches models here by default
_CACHE_DIR = Path.home() / ".cache" / "huggingface" / "hub"


def _model_is_cached(model_size: str) -> bool:
    """Best-effort check if the model is already on disk."""
    if not _CACHE_DIR.exists():
        return False
    # Look for any directory whose name contains the model size
    for p in _CACHE_DIR.iterdir():
        if model_size in p.name and p.is_dir():
            return True
    return False


def get_model(model_size: str = "base", progress_cb=None) -> WhisperModel:
    """Load and cache the Whisper model (downloads on first use)."""
    if model_size in _model_cache:
        return _model_cache[model_size]

    cached = _model_is_cached(model_size)
    mb = _MODEL_MB.get(model_size, "?")

    if cached:
        if progress_cb:
            progress_cb(f"Loading Whisper '{model_size}' model from disk...")
    else:
        if progress_cb:
            progress_cb(
                f"Downloading Whisper '{model_size}' model (~{mb} MB) — "
                f"first-time only, please wait..."
            )

    _model_cache[model_size] = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",   # int8 quantization — fast on any CPU
    )

    if progress_cb:
        progress_cb(f"Whisper '{model_size}' model ready.")

    return _model_cache[model_size]


def transcribe(
    audio_path: str,
    model_size: str = "base",
    language: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Transcribe audio file. Returns:
      {
        "text": str,
        "words": [{"word", "start", "end", "probability"}, ...],
        "language": str,
        "duration": float,
      }
    """
    model = get_model(model_size, progress_cb=progress_cb)

    if progress_cb:
        progress_cb("Analysing audio for transcription...")

    segments_gen, info = model.transcribe(
        audio_path,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        beam_size=1,          # faster: greedy decoding instead of beam search
        best_of=1,            # no candidate sampling — much faster on CPU
        temperature=0.0,      # deterministic, no retries
        condition_on_previous_text=False,  # prevents looping on long silences
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
    )

    total_duration = info.duration or 1.0
    if progress_cb:
        mins = int(total_duration // 60)
        secs = int(total_duration % 60)
        progress_cb(f"Transcribing {mins}m {secs}s of audio... (hang tight)")

    all_words: list[dict] = []
    all_segments: list[dict] = []
    full_text_parts: list[str] = []
    last_report = 0.0          # last timestamp we reported progress for
    report_interval = 30.0     # report every 30 seconds of audio processed

    for segment in segments_gen:
        seg_text = segment.text.strip()
        full_text_parts.append(seg_text)
        all_segments.append({
            "start": round(segment.start, 2),
            "end":   round(segment.end, 2),
            "text":  seg_text,
        })
        if segment.words:
            for w in segment.words:
                all_words.append({
                    "word":        w.word,
                    "start":       round(w.start, 3),
                    "end":         round(w.end, 3),
                    "probability": round(w.probability, 3),
                })

        # Progress update every ~30 s of audio
        if progress_cb and segment.end - last_report >= report_interval:
            pct = min(99, int(segment.end / total_duration * 100))
            mins_done = int(segment.end // 60)
            progress_cb(f"Transcribing... {pct}% ({mins_done}m done)")
            last_report = segment.end

    full_text = " ".join(full_text_parts)
    word_count = len(full_text.split())

    if progress_cb:
        progress_cb(f"Transcription complete — {word_count:,} words, "
                    f"language: {info.language}.")

    return {
        "text":     full_text,
        "words":    all_words,
        "segments": all_segments,
        "language": info.language,
        "duration": round(info.duration, 2),
    }
