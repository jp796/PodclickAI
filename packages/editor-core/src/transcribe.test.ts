// Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { mapWhisperXToWords, type WhisperXResult } from "./transcribe";
import { compileEDL } from "./edl";

test("maps word_segments to Word[] with seq 0..n and trimmed text", () => {
  const result: WhisperXResult = {
    word_segments: [
      { word: " This", start: 0.0, end: 0.2, speaker: "SPEAKER_00" },
      { word: "video", start: 0.2, end: 0.5, speaker: "SPEAKER_00" },
      { word: "shows", start: 0.5, end: 0.9, speaker: "SPEAKER_00" },
    ],
  };
  const words = mapWhisperXToWords(result, { mediaId: "m1" });
  assert.equal(words.length, 3);
  assert.deepEqual(words.map((w) => w.seq), [0, 1, 2]);
  assert.equal(words[0].text, "This"); // leading space trimmed
  assert.equal(words[0].speaker, "SPEAKER_00");
  assert.equal(words[0].mediaId, "m1");
});

test("fills missing timings for unaligned tokens (stays renderable + contiguous)", () => {
  const result: WhisperXResult = {
    word_segments: [
      { word: "in", start: 0.0, end: 0.2 },
      { word: "2014.", /* no start/end — aligner skipped it */ },
      { word: "we", start: 0.6, end: 0.8 },
    ],
  };
  const words = mapWhisperXToWords(result, { mediaId: "m1" });
  // every word must have a finite, positive span
  for (const w of words) {
    assert.ok(Number.isFinite(w.start) && Number.isFinite(w.end) && w.end > w.start, `bad span: ${JSON.stringify(w)}`);
  }
  // the filled token sits between its neighbours
  assert.equal(words[1].start, 0.2);
  assert.equal(words[1].end, 0.6);
  // and since nothing is deleted, it compiles to ONE segment (no spurious cut)
  const edl = compileEDL(words);
  assert.equal(edl.segments.length, 1);
  assert.deepEqual(edl.segments[0], { mediaId: "m1", in: 0.0, out: 0.8 });
});

test("falls back to segments[].words and inherits segment speaker", () => {
  const result: WhisperXResult = {
    segments: [
      {
        start: 0,
        end: 0.5,
        text: "hello there",
        speaker: "SPEAKER_01",
        words: [
          { word: "hello", start: 0.0, end: 0.2 },
          { word: "there", start: 0.2, end: 0.5 },
        ],
      },
    ],
  };
  const words = mapWhisperXToWords(result, { mediaId: "m2" });
  assert.equal(words.length, 2);
  assert.equal(words[0].speaker, "SPEAKER_01"); // inherited from segment
});

test("applies defaultSpeaker when none present", () => {
  const result: WhisperXResult = {
    word_segments: [{ word: "hi", start: 0, end: 0.2 }],
  };
  const words = mapWhisperXToWords(result, { mediaId: "m1", defaultSpeaker: "host" });
  assert.equal(words[0].speaker, "host");
});

test("empty result yields empty Word[]", () => {
  assert.deepEqual(mapWhisperXToWords({}, { mediaId: "m1" }), []);
});
