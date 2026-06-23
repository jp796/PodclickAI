"""
Audio processing pipeline:
  1. Convert input to WAV (via ffmpeg)
  2. Remove background noise (noisereduce)
  3. Normalize loudness to -16 LUFS (podcast standard, pyloudnorm)
  4. Remove filler words using word-level timestamps from Whisper
  5. Export final MP3 for upload
"""

import os
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf
import noisereduce as nr
import pyloudnorm as pyln
import ffmpeg


FILLER_WORDS = {
    "um", "uh", "uhh", "umm", "er", "ah", "ahh",
    "hmm", "hm", "mhm", "mm",
}

# Padding (seconds) to leave around each cut so speech doesn't feel clipped
FILLER_PAD = 0.05
# Short crossfade (seconds) between spliced segments to avoid clicks
CROSSFADE = 0.02


def _log(msg: str, callback: Optional[Callable] = None):
    print(msg)
    if callback:
        callback(msg)


def convert_to_wav(input_path: str, output_path: str):
    """Use ffmpeg to convert any audio format to 16-bit 44.1 kHz mono WAV.

    Adds two guardrails over the bare ffmpeg-python `.run(quiet=True)`:
      1. Pre-flight existence + non-zero-size check. Catches the
         common "tmp file got cleaned up between upload and assembly"
         case with a clear message instead of an opaque ffmpeg error.
      2. Captures ffmpeg's stderr on failure and re-raises with the
         actual reason inline. ffmpeg-python's quiet=True hides
         stderr behind the cryptic "ffmpeg error (see stderr output
         for detail)" string — useless when stderr was suppressed.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(
            f"Audio file missing on disk: {input_path}. "
            "If this was an upload, the temp file may have been cleaned up "
            "(server restart, /var/folders rotation). Please re-upload."
        )
    try:
        size = os.path.getsize(input_path)
    except OSError as e:
        raise RuntimeError(f"Cannot stat {input_path}: {e}") from e
    if size == 0:
        raise ValueError(
            f"Audio file is 0 bytes: {input_path}. "
            "Upload likely failed mid-write — please re-upload."
        )

    try:
        (
            ffmpeg
            .input(input_path)
            .output(output_path, ar=44100, ac=1, acodec="pcm_s16le")
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        # Surface the real reason. ffmpeg writes the actual failure
        # (codec error, format mismatch, etc.) to stderr; without
        # this catch we'd just see the generic ffmpeg-python wrapper.
        stderr = (e.stderr or b"").decode("utf-8", errors="replace").strip()
        tail = "\n".join(stderr.splitlines()[-10:]) if stderr else "(no stderr)"
        raise RuntimeError(
            f"ffmpeg failed converting {input_path} → {output_path}:\n{tail}"
        ) from e


def remove_noise(
    audio: np.ndarray,
    rate: int,
    progress_cb=None,
) -> np.ndarray:
    """
    Estimate noise from the first 0.5 s of audio (typically room tone / silence)
    and apply spectral subtraction via noisereduce.

    For files longer than 10 minutes, processes in 5-minute chunks so the UI
    never looks frozen for more than ~30 seconds at a stretch.
    """
    CHUNK_SECS = 300          # 5-minute chunks
    chunk_samples = CHUNK_SECS * rate
    noise_sample_frames = int(0.5 * rate)
    noise_clip = audio[:noise_sample_frames]

    total_duration = len(audio) / rate

    # Short file — process in one pass
    if total_duration <= CHUNK_SECS:
        if progress_cb:
            progress_cb("Removing background noise...")
        reduced = nr.reduce_noise(
            y=audio, sr=rate, y_noise=noise_clip,
            prop_decrease=0.85, stationary=False,
        )
        return reduced.astype(np.float32)

    # Long file — chunk with progress updates
    chunks_out = []
    total_chunks = int(np.ceil(len(audio) / chunk_samples))

    for i in range(total_chunks):
        start = i * chunk_samples
        end   = min(start + chunk_samples, len(audio))
        chunk = audio[start:end]
        pct   = int((i / total_chunks) * 100)
        mins  = int(start / rate // 60)

        if progress_cb:
            progress_cb(f"Removing noise... {pct}% (chunk {i+1}/{total_chunks}, at {mins}m)")

        reduced_chunk = nr.reduce_noise(
            y=chunk, sr=rate, y_noise=noise_clip,
            prop_decrease=0.85, stationary=False,
        )
        chunks_out.append(reduced_chunk.astype(np.float32))

    if progress_cb:
        progress_cb("Noise removal complete.")

    return np.concatenate(chunks_out)


def normalize_loudness(audio: np.ndarray, rate: int, target_lufs: float = -16.0) -> np.ndarray:
    """Normalize audio to target LUFS (podcast standard is -16 LUFS)."""
    meter = pyln.Meter(rate)
    current_lufs = meter.integrated_loudness(audio)
    if np.isinf(current_lufs) or np.isnan(current_lufs):
        return audio  # silent/near-silent file, skip
    normalized = pyln.normalize.loudness(audio, current_lufs, target_lufs)
    # Hard-limit peaks to -1 dBFS to prevent clipping
    peak = np.max(np.abs(normalized))
    if peak > 0.891:  # 0.891 ≈ -1 dBFS
        normalized = normalized * (0.891 / peak)
    return normalized.astype(np.float32)


# Function words that are commonly stuttered ("I-I", "the the", "we we").
# Restricted set so we never cut rhetorical repeats like "no no no" / "very very".
_STUTTER_WORDS = {
    "i", "a", "the", "and", "but", "so", "you", "we", "it", "it's", "that",
    "to", "of", "is", "was", "like", "my", "he", "she", "they", "this", "im",
    "i'm", "well", "just", "in", "on", "for",
}


def _norm_word(w: str) -> str:
    return w.strip().lower().strip(".,!?;:'\"—-")


def detect_disfluency_regions(duration: float, words: list[dict]) -> list[tuple[float, float]]:
    """
    Deterministic disfluency detector — returns (start, end) regions to CUT for:
      • Stutters: a function word repeated back-to-back ("the the", "I-I").
      • False starts / restarts: a 2–5 word phrase immediately re-spoken
        ("I think we should— I think we should") → cut the first instance.
      • Dead air: inter-word silence longer than PODCLICK_DEADAIR_SEC, trimmed
        down to a natural ~0.35 s pause.
    Conservative by design (only high-confidence patterns) so real content is
    never cut. Gated by env PODCLICK_CLEANUP_DISFLUENCY (default on);
    dead-air sub-gated by PODCLICK_TRIM_DEADAIR (default on).
    """
    import os as _os
    if _os.getenv("PODCLICK_CLEANUP_DISFLUENCY", "1").lower() in ("0", "false", "no"):
        return []

    regions: list[tuple[float, float]] = []
    n = len(words)
    norms = [_norm_word(w["word"]) for w in words]
    used = [False] * n  # words already claimed by a cut (avoid double/overlap)

    # ── False starts / phrase restarts (longest window first: 5→2) ──
    for k in range(5, 1, -1):
        i = 0
        while i + 2 * k <= n:
            if any(used[j] for j in range(i, i + 2 * k)):
                i += 1
                continue
            first = norms[i:i + k]
            second = norms[i + k:i + 2 * k]
            if first and first == second and all(first):
                gap = words[i + k]["start"] - words[i + k - 1]["end"]
                if gap <= 1.6:  # restart must follow promptly to count as a fumble
                    s = max(0.0, words[i]["start"] - FILLER_PAD)
                    e = min(duration, words[i + k - 1]["end"] + FILLER_PAD)
                    regions.append((s, e))
                    for j in range(i, i + k):
                        used[j] = True
                    i += k
                    continue
            i += 1

    # ── Stutters: consecutive identical function words → cut earlier copies ──
    i = 0
    while i < n - 1:
        if not used[i] and norms[i] and norms[i] == norms[i + 1] and norms[i] in _STUTTER_WORDS:
            s = max(0.0, words[i]["start"] - FILLER_PAD)
            e = min(duration, words[i]["end"] + FILLER_PAD)
            regions.append((s, e))
            used[i] = True
        i += 1

    # ── Dead air: trim long silences between words to a natural pause ──
    if _os.getenv("PODCLICK_TRIM_DEADAIR", "1").lower() not in ("0", "false", "no"):
        dead_thresh = float(_os.getenv("PODCLICK_DEADAIR_SEC", "1.2"))
        keep_pause = 0.35
        for a, b in zip(words, words[1:]):
            gap = b["start"] - a["end"]
            if gap > dead_thresh:
                cut_s = a["end"] + keep_pause
                cut_e = b["start"] - 0.05
                if cut_e - cut_s > 0.1:
                    regions.append((cut_s, cut_e))

    return regions


def build_keep_segments(
    duration: float,
    word_timestamps: list[dict],
) -> list[tuple[float, float]]:
    """
    Given a list of {word, start, end} dicts, return a list of (start, end)
    segments to KEEP (i.e., everything that is NOT a filler word OR a detected
    disfluency — stutter / false start / dead air).
    Adjacent keep segments are merged when the gap between them is < FILLER_PAD*2.
    """
    # Mark filler regions
    filler_regions: list[tuple[float, float]] = []
    for w in word_timestamps:
        word = w["word"].strip().lower().strip(".,!?;:'\"")
        if word in FILLER_WORDS:
            start = max(0.0, w["start"] - FILLER_PAD)
            end = min(duration, w["end"] + FILLER_PAD)
            filler_regions.append((start, end))

    # Add disfluency regions (stutters, false starts, dead air) — same cut machinery
    filler_regions.extend(detect_disfluency_regions(duration, word_timestamps))

    if not filler_regions:
        return [(0.0, duration)]

    # Merge overlapping filler regions
    filler_regions.sort()
    merged: list[tuple[float, float]] = [filler_regions[0]]
    for s, e in filler_regions[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Invert to get keep segments
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for fs, fe in merged:
        if cursor < fs:
            keep.append((cursor, fs))
        cursor = fe
    if cursor < duration:
        keep.append((cursor, duration))

    # Drop segments shorter than 0.1 s (artifacts from back-to-back fillers)
    return [(s, e) for s, e in keep if e - s >= 0.1]


def splice_audio(
    audio: np.ndarray,
    rate: int,
    keep_segments: list[tuple[float, float]],
) -> np.ndarray:
    """Concatenate keep segments with a short linear crossfade between them."""
    fade_samples = int(CROSSFADE * rate)
    chunks = []

    for s, e in keep_segments:
        start_sample = int(s * rate)
        end_sample = int(e * rate)
        chunk = audio[start_sample:end_sample].copy()
        chunks.append(chunk)

    if not chunks:
        return audio

    if len(chunks) == 1:
        return chunks[0]

    # Apply crossfade between adjacent chunks
    result = chunks[0]
    for chunk in chunks[1:]:
        fade_len = min(fade_samples, len(result), len(chunk))
        if fade_len > 0:
            fade_out = np.linspace(1.0, 0.0, fade_len)
            fade_in = np.linspace(0.0, 1.0, fade_len)
            result[-fade_len:] = result[-fade_len:] * fade_out + chunk[:fade_len] * fade_in
            result = np.concatenate([result, chunk[fade_len:]])
        else:
            result = np.concatenate([result, chunk])

    return result


def export_mp3(audio: np.ndarray, rate: int, output_path: str):
    """Write audio array to MP3 at 192 kbps via ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = tmp.name

    try:
        sf.write(tmp_wav, audio, rate)
        (
            ffmpeg
            .input(tmp_wav)
            .output(output_path, audio_bitrate="192k", acodec="libmp3lame")
            .overwrite_output()
            .run(quiet=True)
        )
    finally:
        os.unlink(tmp_wav)


def process_audio(
    input_path: str,
    output_dir: str,
    word_timestamps: Optional[list[dict]] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Full audio processing pipeline. Returns a dict with paths to:
      - processed_wav: cleaned WAV (for Whisper if not already transcribed)
      - output_mp3: final MP3 ready for upload
      - fillers_removed: count of filler words cut
      - duration_saved: seconds saved by filler removal
    """
    out = Path(output_dir)
    stem = Path(input_path).stem
    wav_path = str(out / f"{stem}_processed.wav")
    mp3_path = str(out / f"{stem}_final.mp3")

    # Step 1 — Convert to WAV
    _log("Converting to WAV...", progress_cb)
    convert_to_wav(input_path, wav_path)

    # Step 2 — Load
    audio, rate = sf.read(wav_path)
    audio = audio.astype(np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)  # stereo → mono
    original_duration = len(audio) / rate

    # Step 3 — Noise removal
    audio = remove_noise(audio, rate, progress_cb=progress_cb)

    # Step 4 — Loudness normalization
    _log("Normalizing loudness to -16 LUFS...", progress_cb)
    audio = normalize_loudness(audio, rate)

    # Step 5 — Filler word removal
    fillers_removed = 0
    duration_saved = 0.0
    if word_timestamps:
        _log("Removing filler words (um, uh, er, ah...)...", progress_cb)
        keep_segments = build_keep_segments(original_duration, word_timestamps)
        # Count fillers
        for w in word_timestamps:
            word = w["word"].strip().lower().strip(".,!?;:'\"")
            if word in FILLER_WORDS:
                fillers_removed += 1
                duration_saved += w["end"] - w["start"]
        audio = splice_audio(audio, rate, keep_segments)
        _log(f"Removed {fillers_removed} filler words ({duration_saved:.1f}s saved).", progress_cb)

    # Step 6 — Re-normalize after splicing (levels may shift slightly)
    if word_timestamps and fillers_removed > 0:
        audio = normalize_loudness(audio, rate)

    # Step 7 — Write processed WAV (used as Whisper input if needed)
    sf.write(wav_path, audio, rate)

    # Step 8 — Export MP3
    _log("Exporting MP3 at 192 kbps...", progress_cb)
    export_mp3(audio, rate, mp3_path)

    return {
        "processed_wav": wav_path,
        "output_mp3": mp3_path,
        "fillers_removed": fillers_removed,
        "duration_saved": round(duration_saved, 2),
        "final_duration": round(len(audio) / rate, 2),
    }
