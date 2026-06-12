// Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  deleteWords, restoreWords, strikethroughWords, correctWord,
  moveSelection, idsInRange, displayText,
} from "./edit";
import { compileEDL } from "./edl";
import type { Word } from "./types";

function W(seq: number, start: number, end: number): Word {
  return {
    id: "w" + seq, seq, text: "word" + seq, mediaId: "m1", start, end,
    speaker: "spk1", deleted: false, strikethrough: false, correctedText: null, regen: null,
  };
}
const base = () => [W(0, 0, 0.2), W(1, 0.2, 0.5), W(2, 0.5, 0.9), W(3, 0.9, 1.3)];

test("deleteWords flags, preserves seq, and does not mutate the input", () => {
  const words = base();
  const out = deleteWords(words, ["w1"]);
  assert.equal(out[1].deleted, true);
  assert.equal(out[1].seq, 1);                 // seq untouched
  assert.equal(words[1].deleted, false);       // original unchanged (immutability)
  assert.notEqual(out, words);
});

test("strikethrough then restore", () => {
  let words = strikethroughWords(base(), ["w2"]);
  assert.equal(words[2].strikethrough, true);
  words = restoreWords(words, ["w2"]);
  assert.equal(words[2].strikethrough, false);
});

test("correctWord sets caption override; displayText prefers it", () => {
  const words = correctWord(base(), "w0", "Word-Zero");
  assert.equal(words[0].correctedText, "Word-Zero");
  assert.equal(displayText(words[0]), "Word-Zero");
  assert.equal(displayText(words[1]), "word1");
});

test("moveSelection reorders array, preserves every seq, and re-cuts the EDL", () => {
  // move [w2,w3] (seq 2,3) to the front
  const words = base();
  const moved = moveSelection(words, idsInRange(words, "w2", "w3"), { beforeId: "w0" });
  assert.deepEqual(moved.map((w) => w.id), ["w2", "w3", "w0", "w1"]);
  assert.deepEqual(moved.map((w) => w.seq), [2, 3, 0, 1]); // seq carried, not renumbered

  // EDL: contiguous [2,3] then contiguous [0,1] = two segments, seam between them
  const edl = compileEDL(moved);
  assert.equal(edl.segments.length, 2);
  assert.deepEqual(edl.segments[0], { mediaId: "m1", in: 0.5, out: 1.3 });
  assert.deepEqual(edl.segments[1], { mediaId: "m1", in: 0.0, out: 0.5 });
});

test("moveSelection atEnd", () => {
  const words = base();
  const moved = moveSelection(words, ["w0"], { atEnd: true });
  assert.deepEqual(moved.map((w) => w.id), ["w1", "w2", "w3", "w0"]);
});

test("moveSelection rejects inserting before a selected word", () => {
  const words = base();
  assert.throws(() => moveSelection(words, ["w1", "w2"], { beforeId: "w2" }));
});

test("idsInRange works in both directions", () => {
  const words = base();
  assert.deepEqual(idsInRange(words, "w1", "w3"), ["w1", "w2", "w3"]);
  assert.deepEqual(idsInRange(words, "w3", "w1"), ["w1", "w2", "w3"]);
});

test("unedited transcript compiles to one segment (sanity)", () => {
  assert.equal(compileEDL(base()).segments.length, 1);
});
