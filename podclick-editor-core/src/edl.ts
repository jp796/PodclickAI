// EDL compiler — the heart of the editor.
//
// Walks the ordered word list and produces a list of source segments to
// render. Everything (delete, cut/paste reorder, retake strikethrough) is just
// a mutation of the word list; this function turns that into cuts.
//
// Adjacency is decided by (mediaId, seq), NOT by timestamps. Two kept words
// extend the same segment iff they are from the same media and their original
// transcription indices are consecutive (seq + 1). Consequences:
//   - Deleting a word makes the next kept word's seq jump  -> a cut.
//   - A long natural pause between two undeleted words      -> preserved
//     (still seq+1, so the segment just extends across it).
//   - Reordering breaks seq-consecutiveness at the seams    -> cuts.
//   - Different source clip                                 -> a cut.

import type { Word, MediaId } from "./types";

export interface Segment {
  mediaId: MediaId;
  in: number;  // source in-point, seconds
  out: number; // source out-point, seconds (always > in)
}

export interface EDL {
  segments: Segment[];
  duration: number; // total output duration, seconds
}

/** A word is excluded from output if hard-deleted or struck through. */
export function isExcluded(w: Word): boolean {
  return w.deleted || w.strikethrough;
}

/** A word is malformed (and unrenderable) if it has no positive duration. */
export function isMalformed(w: Word): boolean {
  return !Number.isFinite(w.start) || !Number.isFinite(w.end) || !(w.end > w.start);
}

export function compileEDL(words: Word[]): EDL {
  const segments: Segment[] = [];
  let cur: Segment | null = null;
  let prevKept: Word | null = null;

  for (const w of words) {
    if (isExcluded(w) || isMalformed(w)) continue;

    const contiguous =
      cur !== null &&
      prevKept !== null &&
      w.mediaId === prevKept.mediaId &&
      w.seq === prevKept.seq + 1;

    if (contiguous && cur) {
      cur.out = Math.max(cur.out, w.end); // extend; preserves natural inter-word pauses
    } else {
      if (cur) segments.push(cur);
      cur = { mediaId: w.mediaId, in: w.start, out: w.end };
    }
    prevKept = w;
  }
  if (cur) segments.push(cur);

  const duration = segments.reduce((acc, s) => acc + (s.out - s.in), 0);
  return { segments, duration };
}

/**
 * QC guard (spec §7). Throws on any invariant violation. Run before render.
 * `mediaDurations` maps mediaId -> finite source duration (from media.ts).
 */
export function validateEDL(edl: EDL, mediaDurations: Record<MediaId, number>): void {
  edl.segments.forEach((s, i) => {
    if (!(s.out > s.in)) {
      throw new Error(`Segment ${i}: zero/negative length (${s.in} -> ${s.out})`);
    }
    const dur = mediaDurations[s.mediaId];
    if (dur === undefined || !Number.isFinite(dur)) {
      throw new Error(`Segment ${i}: media ${s.mediaId} has no finite duration (Phase 0 not satisfied)`);
    }
    if (s.in < 0 || s.out > dur + 0.001) {
      throw new Error(`Segment ${i}: range ${s.in}-${s.out} out of bounds for media duration ${dur}`);
    }
  });
}
