// Transcription adapter layer.
//
// Everything downstream wants a Word[] (the source of truth, see types.ts).
// This file defines a vendor-neutral `Transcriber` interface and a WhisperX
// implementation. Swapping engines later (e.g. Deepgram) = implement the same
// interface; nothing in the editor changes.
//
// The mapping (`mapWhisperXToWords`) is a PURE function so it's unit-testable
// without WhisperX installed. The CLI shell-out lives in `WhisperXTranscriber`.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, mkdtemp } from "node:fs/promises";
import { join, basename, extname } from "node:path";
import os from "node:os";
import type { Word, MediaId, SpeakerId } from "./types";

const pexec = promisify(execFile);

export interface TranscribeOptions {
  /** The SourceMedia.id these words belong to. */
  mediaId: MediaId;
  /** Used when diarization is off / a word has no speaker. Default "spk1". */
  defaultSpeaker?: SpeakerId;
}

export interface Transcriber {
  transcribe(audioOrVideoPath: string, opts: TranscribeOptions): Promise<Word[]>;
}

// ---------------------------------------------------------------------------
// WhisperX raw output shapes (subset we consume).
// ---------------------------------------------------------------------------

export interface WhisperXWord {
  word: string;
  start?: number;   // may be ABSENT for tokens the aligner can't place ("2014.", "£13")
  end?: number;
  score?: number;
  speaker?: string; // "SPEAKER_00" etc. when --diarize was used
}

export interface WhisperXSegment {
  start: number;
  end: number;
  text: string;
  words?: WhisperXWord[];
  speaker?: string;
}

export interface WhisperXResult {
  segments?: WhisperXSegment[];
  word_segments?: WhisperXWord[]; // flat list of all words; preferred when present
}

// ---------------------------------------------------------------------------
// Pure mapper: WhisperX JSON -> Word[]
// ---------------------------------------------------------------------------

/**
 * Map a WhisperX result into the editor's Word[].
 *
 * - `seq` is assigned 0..n-1 in transcription order and is the stable index the
 *   EDL compiler relies on. Never renumber it after this.
 * - Tokens WhisperX couldn't align (no start/end) get neighbour-derived timings
 *   so they stay renderable and seq-contiguous (otherwise the EDL would cut
 *   around them). See fillMissingTimings.
 */
export function mapWhisperXToWords(result: WhisperXResult, opts: TranscribeOptions): Word[] {
  const defaultSpeaker = opts.defaultSpeaker ?? "spk1";

  const raw: WhisperXWord[] =
    result.word_segments && result.word_segments.length > 0
      ? result.word_segments
      : (result.segments ?? []).flatMap((s) =>
          (s.words ?? []).map((w) => ({ ...w, speaker: w.speaker ?? s.speaker })),
        );

  const words: Word[] = raw.map((w, i) => ({
    id: `${opts.mediaId}:w${i}`,
    seq: i,
    text: (w.word ?? "").trim(),
    mediaId: opts.mediaId,
    start: typeof w.start === "number" ? w.start : NaN,
    end: typeof w.end === "number" ? w.end : NaN,
    speaker: w.speaker ?? defaultSpeaker,
    deleted: false,
    strikethrough: false,
    correctedText: null,
    regen: null,
  }));

  fillMissingTimings(words);
  return words;
}

/**
 * Forward-fill timings for words missing start/end, keeping spans monotonic and
 * strictly positive so none get dropped as malformed by the EDL compiler.
 */
function fillMissingTimings(words: Word[]): void {
  let lastEnd = 0;
  for (let i = 0; i < words.length; i++) {
    const w = words[i];

    if (Number.isFinite(w.start) && Number.isFinite(w.end)) {
      if (!(w.end > w.start)) w.end = w.start + 0.01;
      lastEnd = w.end;
      continue;
    }

    let nextStart: number | null = null;
    for (let j = i + 1; j < words.length; j++) {
      if (Number.isFinite(words[j].start)) { nextStart = words[j].start; break; }
    }

    const s = Number.isFinite(w.start) ? w.start : lastEnd;
    let e = Number.isFinite(w.end)
      ? w.end
      : nextStart !== null && nextStart > s
        ? nextStart
        : s + 0.01;
    if (!(e > s)) e = s + 0.01;

    w.start = s;
    w.end = e;
    lastEnd = e;
  }
}

// ---------------------------------------------------------------------------
// WhisperX CLI implementation (the free, self-hosted default).
// ---------------------------------------------------------------------------

export interface WhisperXConfig {
  /** Whisper model. "large-v3-turbo" is a good speed/accuracy default. */
  model?: string;
  /** "cpu" or "cuda". On Apple-Silicon Macs use "cpu" + int8 (no NVIDIA = no cuda). */
  device?: "cpu" | "cuda";
  /** "int8" for CPU, "float16" for GPU. */
  computeType?: string;
  /** Speaker diarization. Requires hfToken + accepting the pyannote license. */
  diarize?: boolean;
  hfToken?: string;
  language?: string;
  /** Path to the whisperx executable. Default "whisperx" (must be on PATH). */
  binary?: string;
}

export class WhisperXTranscriber implements Transcriber {
  constructor(private cfg: WhisperXConfig = {}) {}

  async transcribe(audioOrVideoPath: string, opts: TranscribeOptions): Promise<Word[]> {
    const outDir = await mkdtemp(join(os.tmpdir(), "whisperx-"));

    const args = [
      audioOrVideoPath,
      "--model", this.cfg.model ?? "large-v3-turbo",
      "--device", this.cfg.device ?? "cpu",
      "--compute_type", this.cfg.computeType ?? "int8",
      "--output_format", "json",
      "--output_dir", outDir,
    ];
    if (this.cfg.language) args.push("--language", this.cfg.language);
    if (this.cfg.diarize) {
      if (!this.cfg.hfToken) {
        throw new Error("WhisperX diarize=true requires hfToken (Hugging Face) and acceptance of the pyannote license.");
      }
      args.push("--diarize", "--hf_token", this.cfg.hfToken);
    }

    await pexec(this.cfg.binary ?? "whisperx", args, { maxBuffer: 64 * 1024 * 1024 });

    const stem = basename(audioOrVideoPath, extname(audioOrVideoPath));
    const jsonPath = join(outDir, `${stem}.json`);
    const result = JSON.parse(await readFile(jsonPath, "utf8")) as WhisperXResult;

    return mapWhisperXToWords(result, opts);
  }
}
