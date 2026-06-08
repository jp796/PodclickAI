// Phase 0 — media intake. Blocking prerequisite for the editor.
//
// THE BUG: browser MediaRecorder writes WebM with no duration in the container
// header (it's only known when recording stops and the file is finalized).
// Players read this as Infinity -> the Studio card shows "Infinity:NaN" and the
// whole word-level model breaks, because the EDL maps timestamps against a
// known total duration.
//
// FIX: rewrite the container so duration + seek cues exist, then probe to
// guarantee a finite duration before the asset is allowed into the editor.
//
// Requires ffmpeg + ffprobe on PATH.

import { execFile } from "node:child_process";
import { promisify } from "node:util";

const pexec = promisify(execFile);

/**
 * Rewrite a captured WebM so it has a real duration and seek cues.
 * `-c copy` is fast (no re-encode). If a given MediaRecorder stream still
 * reports a bad duration after this, fall back to remuxToMp4() which always
 * produces correct timing.
 */
export async function fixWebmDuration(input: string, output: string): Promise<void> {
  await pexec("ffmpeg", ["-y", "-fflags", "+genpts", "-i", input, "-c", "copy", output]);
}

/**
 * Remux/transcode to MP4. PodClick's library already stores MP4, so doing this
 * on save both standardizes the asset and guarantees correct duration.
 * Use this as the reliable path for assets that will be edited.
 */
export async function remuxToMp4(input: string, output: string): Promise<void> {
  await pexec("ffmpeg", [
    "-y", "-i", input,
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    "-c:a", "aac", "-b:a", "192k",
    "-movflags", "+faststart",
    output,
  ]);
}

/** Return a guaranteed-finite duration in seconds, or throw. */
export async function probeDuration(file: string): Promise<number> {
  const { stdout } = await pexec("ffprobe", [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    file,
  ]);
  const d = parseFloat(stdout.trim());
  if (!Number.isFinite(d) || d <= 0) {
    throw new Error(`Non-finite duration for ${file}: "${stdout.trim()}". Run fixWebmDuration/remuxToMp4 first.`);
  }
  return d;
}

// CLIENT-SIDE NOTE:
// The Studio "Direct Video" preview reads duration straight from the in-memory
// Blob, before any server round-trip — that's where "Infinity:NaN" shows. To fix
// the live preview without a server call, patch the Blob's WebM duration on the
// client (e.g. the `fix-webm-duration` package, or the seek-to-very-end trick:
// set video.currentTime = 1e101, wait for 'durationchange', then read duration).
// The server utilities above remain the authoritative fix for stored assets.
