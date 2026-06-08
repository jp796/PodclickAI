// Word-list edit operations — the functions the transcript editing UI calls.
//
// ALL operations are PURE: each returns a NEW words array and clones only the
// words it changes, so they drop straight into React state (setWords(deleteWords(...))).
//
// INVARIANT: nothing here ever changes Word.seq. seq is the stable original
// transcription index the EDL compiler uses to decide source-adjacency. Delete
// flips a flag; reorder changes array position — seq is untouched in both. After
// any of these, recompile with compileEDL(words) to refresh the preview/export.

import type { Word, WordId } from "./types";

function patch(words: Word[], ids: Set<WordId>, fn: (w: Word) => Partial<Word>): Word[] {
  return words.map((w) => (ids.has(w.id) ? { ...w, ...fn(w) } : w));
}

/** Hard-delete: remove from output. */
export function deleteWords(words: Word[], ids: WordId[]): Word[] {
  return patch(words, new Set(ids), () => ({ deleted: true }));
}

/** Bring deleted/struck words back into the output. */
export function restoreWords(words: Word[], ids: WordId[]): Word[] {
  return patch(words, new Set(ids), () => ({ deleted: false, strikethrough: false }));
}

/** Soft-delete for retake review: excluded from output but still shown, restorable. */
export function strikethroughWords(words: Word[], ids: WordId[], on = true): Word[] {
  return patch(words, new Set(ids), () => ({ strikethrough: on }));
}

/** Fix a mis-transcribed word for captions/subtitles. Does NOT change audio. Pass null to clear. */
export function correctWord(words: Word[], id: WordId, correctedText: string | null): Word[] {
  return patch(words, new Set([id]), () => ({ correctedText }));
}

export interface MovePosition {
  /** Insert the selection immediately before this word id. */
  beforeId?: WordId;
  /** Or append the selection to the very end. */
  atEnd?: boolean;
}

/**
 * Cut/paste reorder: lift the selected words (keeping their current relative
 * order) and reinsert them at `pos`. seq is preserved, so the EDL compiler cuts
 * at the new seams and coalesces inside the moved block automatically.
 */
export function moveSelection(words: Word[], selectionIds: WordId[], pos: MovePosition): Word[] {
  const set = new Set(selectionIds);
  const selected = words.filter((w) => set.has(w.id)); // preserves current order
  const rest = words.filter((w) => !set.has(w.id));
  if (selected.length === 0) return words;

  if (pos.atEnd) return [...rest, ...selected];

  if (pos.beforeId === undefined) {
    throw new Error("moveSelection requires beforeId or atEnd");
  }
  if (set.has(pos.beforeId)) {
    throw new Error("Cannot insert a selection before a word that is part of it");
  }
  const idx = rest.findIndex((w) => w.id === pos.beforeId);
  if (idx === -1) throw new Error(`Insertion target ${pos.beforeId} not found`);
  return [...rest.slice(0, idx), ...selected, ...rest.slice(idx)];
}

// ---------------------------------------------------------------------------
// UI conveniences
// ---------------------------------------------------------------------------

/** Word ids in the inclusive range between two ids, in document order (either direction). */
export function idsInRange(words: Word[], startId: WordId, endId: WordId): WordId[] {
  const a = words.findIndex((w) => w.id === startId);
  const b = words.findIndex((w) => w.id === endId);
  if (a === -1 || b === -1) throw new Error("range endpoint not found");
  const [lo, hi] = a <= b ? [a, b] : [b, a];
  return words.slice(lo, hi + 1).map((w) => w.id);
}

/** Text to show for a word (correction wins over raw transcription). */
export function displayText(w: Word): string {
  return w.correctedText ?? w.text;
}

/** Excluded words are rendered struck-through/greyed in the transcript, not removed. */
export function isVisuallyStruck(w: Word): boolean {
  return w.deleted || w.strikethrough;
}
