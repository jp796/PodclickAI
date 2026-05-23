"""
Multi-clip assembly pipeline.

Handles: Intro → Main Podcast (with Commercial inserted at best pause) → Outro

Clip type auto-detection from filename:
  intro     → "intro", "opening", "open", "start"
  outro     → "outro", "ending", "end", "close", "closing"
  commercial → "commercial", "ad", "sponsor", "advertisement", "promo", "break"
  main      → everything else (default)

Assembly steps:
  1. Normalize each clip to same sample rate / channels
  2. Process ONLY the main clip: noise removal + normalization + filler cuts
  3. Find best commercial insertion point (longest silence near the 40–55% mark)
  4. Assemble: intro + main_part1 + [commercial] + main_part2 + outro
  5. Final loudness pass on assembled output
"""

import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Callable

import numpy as np
import soundfile as sf

from pipeline.audio import (
    convert_to_wav,
    remove_noise,
    normalize_loudness,
    splice_audio,
    build_keep_segments,
    export_mp3,
    CROSSFADE,
)

SAMPLE_RATE = 44100
FADE_SAMPLES = int(CROSSFADE * SAMPLE_RATE)

# ── Clip type detection ────────────────────────────────────────────────────────

INTRO_KEYWORDS    = {"intro", "opening", "open", "start", "begin", "beginning"}
OUTRO_KEYWORDS    = {"outro", "ending", "end", "close", "closing", "exit", "farewell"}
COMMERCIAL_KEYWORDS = {"commercial", "ad", "sponsor", "advertisement", "promo", "break", "spot"}


def detect_clip_type(filename: str) -> str:
    """Guess clip type from filename."""
    name = Path(filename).stem.lower()
    # Check each word / token in the filename
    tokens = set(name.replace("-", " ").replace("_", " ").split())
    if tokens & INTRO_KEYWORDS:
        return "intro"
    if tokens & OUTRO_KEYWORDS:
        return "outro"
    if tokens & COMMERCIAL_KEYWORDS:
        return "commercial"
    # substring match for combined words like "myintro.mp3"
    for kw in INTRO_KEYWORDS:
        if kw in name:
            return "intro"
    for kw in OUTRO_KEYWORDS:
        if kw in name:
            return "outro"
    for kw in COMMERCIAL_KEYWORDS:
        if kw in name:
            return "commercial"
    return "main"


# ── Audio helpers ──────────────────────────────────────────────────────────────

def _load_wav(path: str) -> tuple[np.ndarray, int]:
    """Load WAV to float32 mono numpy array."""
    audio, rate = sf.read(path)
    audio = audio.astype(np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, rate


def _resample_if_needed(audio: np.ndarray, src_rate: int, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Resample using ffmpeg if rates differ."""
    if src_rate == target_rate:
        return audio
    import soundfile as sf
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_in = f.name
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_out = f.name
    try:
        sf.write(tmp_in, audio, src_rate)
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_in, "-ar", str(target_rate), tmp_out],
            check=True, capture_output=True,
        )
        out, _ = _load_wav(tmp_out)
        return out
    finally:
        for p in (tmp_in, tmp_out):
            try: os.unlink(p)
            except: pass


def _crossfade_join(a: np.ndarray, b: np.ndarray, fade: int = FADE_SAMPLES) -> np.ndarray:
    """Join two arrays with a linear crossfade of `fade` samples."""
    fade = min(fade, len(a), len(b))
    if fade <= 0:
        return np.concatenate([a, b])
    fade_out = np.linspace(1.0, 0.0, fade, dtype=np.float32)
    fade_in  = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    a[-fade:] = a[-fade:] * fade_out + b[:fade] * fade_in
    return np.concatenate([a, b[fade:]])


# ── Commercial insertion point ─────────────────────────────────────────────────

def find_best_insertion_point(
    audio: np.ndarray,
    rate: int,
    target_pct: float = 0.47,
    search_window_pct: float = 0.20,
    min_silence_s: float = 0.3,
) -> float:
    """
    Find the best point (in seconds) to insert the commercial break.

    Strategy:
      1. Look for the longest silence in the window [target_pct ± search_window_pct].
      2. Insert at the center of that silence.
      3. If no silence found, fall back to the exact target_pct mark.

    Returns insertion time in seconds.
    """
    total_duration = len(audio) / rate
    center_s = total_duration * target_pct
    window_s = total_duration * search_window_pct

    search_start = max(0.0, center_s - window_s)
    search_end   = min(total_duration, center_s + window_s)

    # Use RMS energy in short frames to find silence
    frame_size = int(0.05 * rate)   # 50 ms frames
    hop        = int(0.025 * rate)  # 25 ms hop

    start_frame = int(search_start * rate)
    end_frame   = int(search_end   * rate)

    SILENCE_THRESHOLD = 0.01   # RMS below this = silence

    silences: list[tuple[float, float]] = []   # (start_s, end_s)
    in_silence = False
    sil_start  = 0.0

    i = start_frame
    while i + frame_size < end_frame:
        frame = audio[i: i + frame_size]
        rms   = float(np.sqrt(np.mean(frame ** 2)))
        t     = i / rate
        if rms < SILENCE_THRESHOLD:
            if not in_silence:
                in_silence = True
                sil_start  = t
        else:
            if in_silence:
                in_silence = False
                sil_end = t
                if sil_end - sil_start >= min_silence_s:
                    silences.append((sil_start, sil_end))
        i += hop

    if in_silence:
        sil_end = end_frame / rate
        if sil_end - sil_start >= min_silence_s:
            silences.append((sil_start, sil_end))

    if not silences:
        return center_s   # no silence found → use center

    # Pick the silence whose midpoint is closest to the target
    best = min(silences, key=lambda s: abs(((s[0] + s[1]) / 2) - center_s))
    insertion = (best[0] + best[1]) / 2
    return insertion


# ── Main assembly function ─────────────────────────────────────────────────────

def assemble_clips(
    clips: list[dict],          # [{"path": str, "type": str}, ...]
    output_dir: str,
    word_timestamps: Optional[list[dict]] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Assemble multiple clips into a single podcast episode MP3.

    clips must contain at least one item with type == "main".

    Returns same shape as audio.process_audio():
      {
        processed_wav, output_mp3,
        fillers_removed, duration_saved, final_duration,
        commercial_inserted_at,   # seconds into main (or None)
      }
    """
    def log(msg):
        print(msg)
        if progress_cb: progress_cb(msg)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Sort clips into roles — each role can have multiple files (concatenated in order)
    intro_clips      = [c for c in clips if c["type"] == "intro"]
    main_clips       = [c for c in clips if c["type"] == "main"]
    commercial_clips = [c for c in clips if c["type"] == "commercial"]
    outro_clips      = [c for c in clips if c["type"] == "outro"]

    if not main_clips:
        raise ValueError("No main podcast clip found. Please add a clip labeled 'Main'.")

    # Pre-flight: every clip path must exist on disk with non-zero size.
    # Catches the "uploaded tmp file got cleaned up between upload and
    # assembly" case (TMPDIR rotation, server restart, finally-block of
    # a sibling job) up-front with a single error naming every missing
    # file — instead of dying inside ffmpeg on the first one.
    missing = []
    empty = []
    for c in clips:
        p = c.get("path")
        label = c.get("filename") or p or "?"
        if not p or not os.path.exists(p):
            missing.append(f"{c.get('type', '?')}: {label}")
        else:
            try:
                if os.path.getsize(p) == 0:
                    empty.append(f"{c.get('type', '?')}: {label}")
            except OSError:
                missing.append(f"{c.get('type', '?')}: {label}")
    if missing or empty:
        parts = []
        if missing:
            parts.append("Missing files: " + "; ".join(missing))
        if empty:
            parts.append("Zero-byte files: " + "; ".join(empty))
        raise FileNotFoundError(
            " | ".join(parts)
            + ". Please re-upload the affected clip(s) and run again."
        )

    # ── Helper: load + convert one file to float32 mono 44.1 kHz ─────────────
    def load_clip(clip_path: str) -> np.ndarray:
        wav_tmp = str(out / f"_tmp_{uuid.uuid4().hex}.wav")
        convert_to_wav(clip_path, wav_tmp)
        audio, rate = _load_wav(wav_tmp)
        audio = _resample_if_needed(audio, rate, SAMPLE_RATE)
        try: os.unlink(wav_tmp)
        except: pass
        return audio

    # ── Helper: load + concatenate multiple files of the same type ────────────
    def load_and_concat(clip_list: list[dict], label: str) -> Optional[np.ndarray]:
        """Load all files for a role and concatenate them with a short crossfade."""
        if not clip_list:
            return None
        names = ", ".join(Path(c["path"]).name for c in clip_list)
        log(f"Loading {label} ({len(clip_list)} file{'s' if len(clip_list) > 1 else ''}): {names}")
        parts = [load_clip(c["path"]) for c in clip_list]
        if len(parts) == 1:
            result = parts[0]
        else:
            result = parts[0]
            for part in parts[1:]:
                result = _crossfade_join(result, part)
        return normalize_loudness(result, SAMPLE_RATE, target_lufs=-16.0)

    # ── Step 1: Load and lightly normalize intro / commercial / outro ──────────
    intro_audio      = load_and_concat(intro_clips,      "intro")
    commercial_audio = load_and_concat(commercial_clips, "commercial")
    outro_audio      = load_and_concat(outro_clips,      "outro")

    # ── Step 2: Load + concatenate all main clips ─────────────────────────────
    names = ", ".join(Path(c["path"]).name for c in main_clips)
    log(f"Loading main podcast ({len(main_clips)} file{'s' if len(main_clips) > 1 else ''}): {names}")
    main_parts = [load_clip(c["path"]) for c in main_clips]
    if len(main_parts) > 1:
        log(f"Joining {len(main_parts)} main clips...")
        main_raw = main_parts[0]
        for part in main_parts[1:]:
            main_raw = _crossfade_join(main_raw, part)
    else:
        main_raw = main_parts[0]
    original_duration = len(main_raw) / SAMPLE_RATE

    log("Removing background noise from main audio...")
    main_audio = remove_noise(main_raw, SAMPLE_RATE)

    log("Normalizing loudness...")
    main_audio = normalize_loudness(main_audio, SAMPLE_RATE, target_lufs=-16.0)

    # Filler word removal on main clip
    fillers_removed  = 0
    duration_saved   = 0.0
    commercial_at    = None   # seconds into main (after filler removal)

    if word_timestamps:
        log("Removing filler words from main audio...")
        keep_segments = build_keep_segments(original_duration, word_timestamps)
        for w in word_timestamps:
            word = w["word"].strip().lower().strip(".,!?;:'\"")
            from pipeline.audio import FILLER_WORDS
            if word in FILLER_WORDS:
                fillers_removed += 1
                duration_saved  += w["end"] - w["start"]
        main_audio = splice_audio(main_audio, SAMPLE_RATE, keep_segments)
        if fillers_removed:
            log(f"Removed {fillers_removed} filler words ({duration_saved:.1f}s saved).")
            main_audio = normalize_loudness(main_audio, SAMPLE_RATE, target_lufs=-16.0)

    # ── Step 3: Find commercial insertion point ────────────────────────────────
    main_part1 = main_audio
    main_part2 = None

    if commercial_audio is not None:
        insertion_s = find_best_insertion_point(main_audio, SAMPLE_RATE)
        commercial_at = round(insertion_s, 2)
        log(f"Commercial will be inserted at {insertion_s:.1f}s into the main audio.")
        insertion_sample = int(insertion_s * SAMPLE_RATE)
        main_part1 = main_audio[:insertion_sample].copy()
        main_part2 = main_audio[insertion_sample:].copy()

    # ── Step 4: Assemble ───────────────────────────────────────────────────────
    log("Assembling final episode...")
    assembled = np.zeros(0, dtype=np.float32)

    if intro_audio is not None:
        assembled = intro_audio.copy()
        log(f"  + Intro ({len(intro_audio)/SAMPLE_RATE:.1f}s)")

    # Main part 1
    if len(assembled):
        assembled = _crossfade_join(assembled, main_part1)
    else:
        assembled = main_part1.copy()
    log(f"  + Main part 1 ({len(main_part1)/SAMPLE_RATE:.1f}s)")

    # Commercial break
    if commercial_audio is not None and main_part2 is not None:
        assembled = _crossfade_join(assembled, commercial_audio)
        log(f"  + Commercial ({len(commercial_audio)/SAMPLE_RATE:.1f}s)")
        assembled = _crossfade_join(assembled, main_part2)
        log(f"  + Main part 2 ({len(main_part2)/SAMPLE_RATE:.1f}s)")

    # Outro
    if outro_audio is not None:
        assembled = _crossfade_join(assembled, outro_audio)
        log(f"  + Outro ({len(outro_audio)/SAMPLE_RATE:.1f}s)")

    # ── Step 5: Final loudness pass ────────────────────────────────────────────
    log("Final loudness normalization...")
    assembled = normalize_loudness(assembled, SAMPLE_RATE, target_lufs=-16.0)

    # ── Step 6: Write outputs ──────────────────────────────────────────────────
    wav_path = str(out / "episode_assembled.wav")
    mp3_path = str(out / "episode_final.mp3")
    sf.write(wav_path, assembled, SAMPLE_RATE)
    log("Exporting final MP3...")
    export_mp3(assembled, SAMPLE_RATE, mp3_path)

    final_duration = round(len(assembled) / SAMPLE_RATE, 2)
    log(f"Assembly complete! Total duration: {final_duration/60:.1f} min")

    return {
        "processed_wav": wav_path,
        "output_mp3": mp3_path,
        "fillers_removed": fillers_removed,
        "duration_saved": round(duration_saved, 2),
        "final_duration": final_duration,
        "commercial_inserted_at": commercial_at,
        "has_intro": intro_audio is not None,
        "has_commercial": commercial_audio is not None,
        "has_outro": outro_audio is not None,
    }
