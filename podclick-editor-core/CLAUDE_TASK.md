# Claude Code task — PodClick Studio editor: Phase 0 + Phase 1

**Read first:** `PodClick_Studio_Descript_Feature_Spec.md` (the full spec). This is an
**enhancement, not a rebuild**. Do not redesign Studio. Do not move or rename
existing controls. The editor is a **separate area of the UI**, opened from a
**Video Library** item.

## What's in this scaffold

Stack-agnostic TypeScript core you can drop into the existing Node/Next.js app:

- `src/types.ts` — the data model. The transcript (`Project.words`, ordered) is
  the source of truth. Note `Word.seq`: a stable original index assigned at
  ingest that edits must **never** renumber.
- `src/edl.ts` — `compileEDL()` turns the word list into render segments, plus
  `validateEDL()` QC guard. **Verified against delete / natural-pause /
  reorder / cross-media / strikethrough.** This is the crux; don't reinvent it.
- `src/edl.test.ts` — the tests proving the above. Keep them green.
- `src/transcribe.ts` — `Transcriber` interface + `WhisperXTranscriber` (the
  chosen engine) + a pure `mapWhisperXToWords` mapper into the `Word[]` shape.
- `src/transcribe.test.ts` — mapper tests (seq, unaligned-token timing, etc.).
- `src/edit.ts` — pure word-list edit operations (`deleteWords`, `restoreWords`,
  `strikethroughWords`, `correctWord`, `moveSelection`, `idsInRange`,
  `displayText`). These are the functions the transcript UI calls; they never
  renumber `seq`.
- `src/edit.test.ts` — proves immutability, seq-preservation, and EDL re-cutting.
- `src/media.ts` — Phase 0 WebM duration fix (`fixWebmDuration`,
  `remuxToMp4`, `probeDuration`) + a client-side fix note.
- `src/render.ts` — EDL -> ffmpeg trim+concat filtergraph -> final file.

Run: `npm install && npm test`.

## Build order (do in this sequence)

### Phase 0 — blocking
1. Wire `media.ts` into the capture/upload save path so every Library asset is
   stored with a **finite duration**. Fix the live "Infinity:NaN" Studio
   preview client-side per the note in `media.ts`.
2. Add an **"Open in Editor"** action on each Video Library item (and on a
   freshly saved Direct Video / uploaded file). This routes to the new editor
   area with the asset's `mediaId`. Do **not** add a third mode to Studio.

### Phase 1 — the cut-and-export loop (ship this before any polish)
3. On opening an asset: transcribe to word-level timestamps via the
   `Transcriber` interface in `src/transcribe.ts`. The chosen engine is
   **WhisperX** (`WhisperXTranscriber`) — free, self-hosted, open source. The
   mapper already populates `Project.words` and assigns `seq` in order; you just
   wire the call. Do NOT swap in a different vendor — if you ever need to, add a
   class implementing the same interface; nothing else changes.

   WhisperX setup: `pip install whisperx` + `ffmpeg` on PATH. On JP's iMac
   (no NVIDIA GPU) use `device: "cpu"`, `computeType: "int8"`. Diarization
   (who-spoke labels) is optional and needs a free Hugging Face token +
   accepting the pyannote license; leave it off for the Phase 1 loop.
4. Build the transcript editing surface. Wire UI actions to the pure ops in
   `src/edit.ts` — delete (`deleteWords`), cut/paste (`moveSelection` +
   `idsInRange`), retake soft-delete (`strikethroughWords` / `restoreWords`),
   caption fix (`correctWord`). Render each word with `displayText`; show
   excluded words struck-through via `isVisuallyStruck` rather than removing
   them. After every edit, call `compileEDL(words)` and refresh the preview.
   Do NOT write your own array mutations — use these so `seq` is never renumbered.
5. Wire export: `compileEDL` -> `validateEDL` -> `render` -> save result back to
   the Library.

## Definition of done for this milestone

A user can: pick a Library video → open it in the editor → see the transcript →
delete a sentence and reorder a clause → export → and the exported file is a
**correct, frame/sample-accurate cut** matching the edited transcript, with
natural pauses preserved where nothing was deleted.

## Reuse, don't fork

Map against `PodClick_Master_Architecture_Spec.md` and reuse: the existing clip
reframer, caption styling, two-speaker layouts, and render/QC pipeline. Tag each
spec feature ✅ / 🔧 / 🔴 against what already exists before writing new code.

## Out of scope for this milestone

Filler/retake/gap AI tools, scenes/layouts, captions UI, elements, B-roll,
audio tracks, transitions, studio sound, voice regenerate, green screen,
Underlord. Those are Phases 2–3 in the spec. Get the core loop solid first.
