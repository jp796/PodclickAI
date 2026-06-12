// Run with: npm test   (uses tsx + node:test)
import { test } from "node:test";
import assert from "node:assert/strict";
import { compileEDL, validateEDL } from "./edl";
import type { Word } from "./types";

// Minimal word factory for tests.
function W(
  seq: number,
  start: number,
  end: number,
  o: Partial<Word> = {},
): Word {
  return {
    id: "w" + seq,
    seq,
    text: "x",
    mediaId: o.mediaId ?? "m1",
    start,
    end,
    speaker: "spk1",
    deleted: o.deleted ?? false,
    strikethrough: o.strikethrough ?? false,
    correctedText: null,
    regen: null,
  };
}

test("no edits -> single contiguous segment", () => {
  const edl = compileEDL([W(0, 0, 0.2), W(1, 0.2, 0.5), W(2, 0.5, 0.9)]);
  assert.equal(edl.segments.length, 1);
  assert.deepEqual(edl.segments[0], { mediaId: "m1", in: 0, out: 0.9 });
});

test("delete middle word -> cut, deleted span excluded", () => {
  const edl = compileEDL([W(0, 0, 0.2), W(1, 0.2, 0.5, { deleted: true }), W(2, 0.5, 0.9)]);
  assert.equal(edl.segments.length, 2);
  assert.equal(edl.segments[0].out, 0.2);
  assert.equal(edl.segments[1].in, 0.5);
});

test("natural pause with nothing deleted -> single segment (pause preserved)", () => {
  const edl = compileEDL([W(0, 0, 0.2), W(1, 0.5, 0.9)]); // 0.3s gap, both kept
  assert.equal(edl.segments.length, 1);
  assert.deepEqual(edl.segments[0], { mediaId: "m1", in: 0, out: 0.9 });
});

test("reorder block -> cut at seams, no source bleed", () => {
  // Block B (seq 2,3) moved before block A (seq 0,1).
  const edl = compileEDL([W(2, 2.0, 2.3), W(3, 2.3, 2.6), W(0, 0, 0.3), W(1, 0.3, 0.6)]);
  assert.equal(edl.segments.length, 2);
  assert.deepEqual(edl.segments[0], { mediaId: "m1", in: 2.0, out: 2.6 });
  assert.deepEqual(edl.segments[1], { mediaId: "m1", in: 0, out: 0.6 });
});

test("cross-media boundary -> cut even if seq looks consecutive", () => {
  const edl = compileEDL([W(0, 0, 0.2, { mediaId: "m1" }), W(1, 5.0, 5.4, { mediaId: "m2" })]);
  assert.equal(edl.segments.length, 2);
});

test("strikethrough excluded same as deleted", () => {
  const edl = compileEDL([W(0, 0, 0.2), W(1, 0.2, 0.5, { strikethrough: true }), W(2, 0.5, 0.9)]);
  assert.equal(edl.segments.length, 2);
});

test("validateEDL rejects out-of-bounds segment", () => {
  const edl = compileEDL([W(0, 0, 0.2), W(1, 0.2, 0.5)]);
  assert.doesNotThrow(() => validateEDL(edl, { m1: 1.0 }));
  assert.throws(() => validateEDL(edl, { m1: 0.3 })); // segment out=0.5 > media 0.3
});

test("validateEDL rejects non-finite media duration (Phase 0 unsatisfied)", () => {
  const edl = compileEDL([W(0, 0, 0.2)]);
  assert.throws(() => validateEDL(edl, { m1: Infinity }));
});
