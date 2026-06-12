# PodClick Studio — Descript-Style Editor Build Spec

**For:** Claude Code
**Goal:** Add a text-based ("edit the transcript, edit the video") editing layer to PodClick Studio, modeled on Descript's workflow.
**Audience for this doc:** the build agent. Read the Integration & IA section, then the Core Paradigm section. Every feature depends on the data model in §1.

---

## Integration & IA — read before anything else

**This is an enhancement, not a rebuild.** Studio already works. Do not redesign existing surfaces. Do not move or rename existing controls. Add a new surface and one entry point into it.

### The two jobs the product serves

1. **Direct-to-podcast/clip/YouTube** — capture or upload → **Publish** → existing audio + AI pipeline (Job Site). Automated handoff, minimal manual editing. **Already built. Leave it alone.**
2. **Single video → quick Descript-style edit → repurpose** — capture or upload → lands in the Video Library as an MP4 → **edit** → export. Manual. **This is what we're adding.**

The front half of both jobs is identical. They share capture, upload, and the **Video Library**. The Library item is the fork.

### Where the editor lives

The Descript-style editor is a **separate area of the UI** — its own surface/route, NOT a third mode inside Studio. Cramming it into the Studio capture screen overloads a screen that is already carrying a teleprompter, capture controls, and a Podcast/Direct-Video toggle.

- **Entry point:** an **"Open in Editor"** action on each **Video Library** item (and on a freshly saved Direct Video / uploaded file). The saved-to-library MP4 that Direct Video already produces *is* the artifact the editor opens — no new capture path needed.
- **Keep the Podcast Episode / Direct Video toggle as-is.** It expresses *format intent at capture*. Do not overload it with the workflow fork.
- **The workflow fork (pipeline vs. manual edit) happens at the Library, not at capture.** From a Library item the user chooses either "Send to Pipeline" (job 1) or "Open in Editor" (job 2).
- **Handoff contract:** Library item → Editor (loads media + transcript) → export back to Library and/or hand to the pipeline. The editor reads and writes Library assets; it does not own its own separate storage.

### Construction-theme nav note (optional)

If the editor gets its own top-nav entry alongside Walk-through / Foundation / Permit / Studio / Job Site / Scout / Blueprint, keep the theme (e.g. "Punch List," "Finish," "Trim"). The nav entry should land on the Library with editable items surfaced — the editor itself is always opened against a specific item. JP's call on naming.

---

## Phase 0 — Prerequisite (fix before building the editor)

**WebM duration bug (`Infinity:NaN`).** Captured recordings show "Infinity:NaN" for duration because `MediaRecorder` WebM has no duration in the container header until finalized — players read it as `Infinity`. The entire word-level model in §1 maps timestamps against a known total duration; if duration is `Infinity`/`NaN`, the EDL math and timeline projection break before they start.

- **Fix on save:** remux captured WebM to write seekable cues + correct duration (`ffmpeg -i in.webm -c copy out.webm`, or a WebM duration-repair pass / `fix-webm-duration`-style library client-side).
- **Acceptance:** every Library item exposes a finite, correct `duration` and seekable timeline before it can be opened in the editor.

This is small but blocking. Do it first.

---

## 0. The one thing to internalize first

Descript is not a timeline editor with a transcript bolted on. **The transcript IS the timeline.** Every word carries a source-media start/end timestamp. Deleting a word deletes that slice of video. Reordering words reorders the video. The visual timeline is a *projection* of the transcript edit list, not the primary surface.

If you build this as "timeline editor + a transcript view that highlights along," you will reproduce the exact thing the source video says makes people struggle. Build the transcript as the source of truth and derive everything else from it.

---

## 1. Core data model (build this before any feature)

### 1.1 Word-level transcript

Every imported clip is transcribed to word-level tokens with timestamps:

```jsonc
{
  "media_id": "src_001",
  "speakers": [{ "id": "spk1", "name": "JP", "voice_model_id": null }],
  "words": [
    {
      "id": "w0001",
      "text": "This",
      "start": 0.00,          // seconds into source media
      "end": 0.21,
      "speaker": "spk1",
      "deleted": false,        // removed from output
      "strikethrough": false,  // soft-delete, still visible/restorable (retake review)
      "corrected_text": null,  // display/caption override; does not change audio
      "regen": null            // {target_text, voice_only|video_too, status}
    }
    // ...
  ]
}
```

### 1.2 Edit Decision List (EDL) — derived, never authored directly

The renderable output is computed by walking `words` in order, skipping `deleted`/`strikethrough`, and coalescing each word's `[start, end]` source range into contiguous segments:

```jsonc
{
  "segments": [
    { "src": "src_001", "in": 0.00, "out": 4.83 },
    { "src": "src_001", "in": 6.10, "out": 9.40 }   // gap = deleted words / shortened pause
  ]
}
```

Render = compile EDL → `ffmpeg` cut + concat (use the concat demuxer or a filtergraph; re-encode once at export, use stream-copy for previews where keyframes allow).

### 1.3 Project tree

```
Project
├─ scenes[]                 // segments of the video (see §2.3)
│   ├─ word_range           // start_word_id .. end_word_id
│   ├─ layout_id            // applied per scene
│   ├─ captions_config
│   ├─ transition_in
│   └─ overlay_tracks[]     // b-roll, graphics, elements scoped to this scene
├─ audio_tracks[]           // music + SFX, with start/end/volume/fades (global, not scene-scoped)
├─ base_media[]             // source clips + their word-level transcripts
└─ brand_kit                // colors, fonts, logo, saved layouts
```

**Key invariant:** layouts, B-roll, captions, and transitions are applied **per scene**. The video source already says this explicitly — bake it into the schema so a scene is the unit those things attach to.

### 1.4 What PodClick already has — reconcile, don't rebuild

Map these against existing PodClick modules before writing new code:
- **Clip generation / reframing pipeline** (the Opus Clip-style 9:16 reframer) → reuse for §6 "Create Clips." Same timestamp math.
- **Caption styling + two-speaker stacked layouts** → these are §2.4 layouts and §3.6 captions. Don't fork; extend.
- **Render pipeline + QC guards** → the EDL compiler in §1.2 feeds straight into this. QC guards should validate the compiled EDL (no zero-length segments, no out-of-bounds timestamps, scene boundaries land on word boundaries).
- Pull the current architecture from `PodClick_Master_Architecture_Spec.md` and tag each feature below with ✅ / 🔧 / 🔴 against what exists.

---

## 2. Phase 1 — Foundation (must ship first)

### 2.1 Import + transcription
- Accept video/audio upload or drag-drop. Also support in-app record (screen + cam, or audio only) → §3.5.
- On import, transcribe to word-level timestamps. **Dependency:** speech-to-text with word timings — Deepgram, AssemblyAI, or WhisperX (whisper alone gives segment timings; you need forced alignment for word-level). Persist per §1.1.
- Show a title field while transcription runs (non-blocking UX).

### 2.2 Text-based editing (the core surface)
- **Delete:** select words → delete → marks `deleted:true` → EDL recompiles → preview updates.
- **Cut / move:** select → cut (⌘X) → paste elsewhere → reorders the word list (this physically reorders source segments).
- **Strikethrough soft-delete:** removes from output but keeps the word visible and restorable. This is the default for retake review (§3.2) so the user can bring a take back.
- Editing must feel instant: recompile EDL + refresh preview without a full re-render. Use proxy/stream-copy previews; full encode only at export.

### 2.3 Scenes
- Typing `//` in the transcript inserts a scene break. A scene = a contiguous `word_range`.
- Scenes are the attach point for layouts, B-roll, captions, transitions (§1.3 invariant).
- Timeline shows scene boundaries; user can also split a scene in the timeline.

### 2.4 Layouts (per scene)
- Layout = a composition template: solo speaker, media-beside-speaker, two-person, intro/title, captions-style, lower-third / name plate.
- Ship a default **layout pack**; allow custom layouts; allow **save current scene as new layout** into a named (private) pack with optional **smart-fill** (auto-populates title/text fields).
- Layouts respect the **brand kit** (colors, fonts, background). Changing brand kit restyles all layouts.
- **Reuse PodClick's existing two-speaker stacked layout here** rather than authoring a new one.

### 2.5 Timeline primitives
- `S` = split clip at playhead. `B` = blade/razor. Drag to trim/move clips and overlays.
- Timeline edits and transcript edits operate on the same EDL — a split in the timeline must reflect back as a boundary the transcript view understands, and vice versa.

### 2.6 Export
- **Local export:** max-resolution file (this is the YouTube path). Single final encode from the compiled EDL.
- **Web link export:** shareable hosted link with optional transcript display (client review path).
- **Subtitles:** export SRT/VTT, built from `corrected_text ?? text` per word, grouped into caption cues. (This is why §3.4 correction matters.)

---

## 3. Phase 2 — AI cleanup + production tools

These are the "AI tools panel" features. Most are **deterministic** (timestamp math + ffmpeg) — do these before anything ML-heavy.

### 3.1 Remove filler words — *deterministic*
- Detect "um, uh, like, you know," and immediate word repeats from the transcript.
- Present a **review list** (keep/remove per item) — do NOT auto-nuke; the source explicitly reviews before "remove all."
- Removal = mark words `deleted`.

### 3.2 Remove retakes — *deterministic + similarity*
- Detect re-said passages (near-duplicate adjacent phrases) and keep the last take by default.
- Use `strikethrough` not hard delete, so the user can review and restore (don't "discard all edits"). Detection won't always pick the right take — make every retake decision individually reversible.
- Implementation: sliding-window fuzzy match (normalized token n-grams, Levenshtein/cosine) over adjacent transcript spans.

### 3.3 Shorten word gaps — *deterministic*
- Find inter-word silences above a threshold; user sets "gaps longer than X → shorten to Y" (defaults from source: gaps > 0.5s → 2s... note: confirm the intended target with the user; 2s is what the transcript says but reads like a misspeak for 0.2s — expose both the threshold and the target as user controls and don't hardcode).
- Implementation: gap = `words[i+1].start - words[i].end`; trim source range accordingly in the EDL.

### 3.4 Transcript correction — *deterministic*
- Highlight mis-transcribed words → "correct" → type the right text → writes `corrected_text`. Affects captions/subtitles only, not audio.

### 3.5 Screen/camera recording + zoom-to-highlight — *deterministic*
- In-app record (screen + cam). Inserted recording gets transcribed and dropped into the transcript like any clip.
- **Crop/zoom + reposition** the speaker frame (circle/square/PiP, resizable) over the screen capture to spotlight a region. This is a transform on the layer, keyframable for zoom-in moves.

### 3.6 Captions — *deterministic*
- Apply to: whole video, all scenes, or a single scene.
- Style: font, color, size; respect brand kit. Source from `corrected_text ?? text`.
- **Reuse PodClick's existing caption styling engine.**

### 3.7 Elements / graphics — *deterministic*
- Insert text titles, basic shapes (circle, animated "squiggle" circle), lines, rings, timers, placeholders. Color + position controls. Used heavily with §3.5 for callouts/arrows.

### 3.8 B-roll + media — *deterministic*
- Stock search panel (images, video, GIFs, backgrounds) + drag-in. Scope B-roll to a scene. Resizable; optional transition or hard cut.
- **Dependency:** a stock media provider API (e.g., Pexels/Pixabay free tier, or Giphy for GIFs) — or wire PodClick's existing asset sources.

### 3.9 Audio: music + SFX — *deterministic*
- Music/SFX library; drag onto an audio track; volume control; **fade in/out** by dragging; trim with `S` + delete; offset start.
- SFX use case from source: a "click" sound synced to a text element popping on screen. Support snapping an SFX to an element's appearance time.

### 3.10 Transitions — *deterministic*
- Between scenes: smart, fade, blur, circle wipe, **color dip** (with color picker), adjustable duration. Implement as ffmpeg `xfade` filters + a custom color-dip (fade-to-color-to-next).

### 3.11 Post-production AI (text generation) — *LLM*
- **Add chapters / timestamps** → for YouTube descriptions. Cluster the transcript into topical chapters, emit `mm:ss Title`.
- **Summarize** the video.
- **Draft social post** — platform-specific (specify TikTok/IG/YouTube/X), count, custom instructions.
- These are straight Claude API calls over the transcript. **Reuse PodClick's existing script/social generation agents** rather than new prompts.

### 3.12 Create Clips (Opus-style) — *deterministic + LLM*
- Pick viral moments → generate N short clips of chosen duration, optionally filtered by topic/goal/criteria.
- Clips land as separate **compositions** the user can re-edit with the full editor, then export per platform.
- **This is PodClick's existing clip pipeline.** The only new work is the "compositions" abstraction (a clip = a sub-project pointing at a word_range of the parent) and the moment-selection prompt. Don't rebuild reframing/captioning.

---

## 4. Phase 3 — ML-heavy features (3rd-party APIs; sequence by payoff)

Be honest about cost/quality. These need external models, not in-house ML.

### 4.1 Studio Sound (audio enhancement) — *medium effort, high payoff*
- Cleans noise, normalizes, "studio" voice; **adjustable intensity %** (source runs it ~60%).
- **Dependency:** Adobe Podcast / Auphonic API, or a self-hosted enhancement model. A decent first pass is achievable with ffmpeg/`rnnoise` + loudness normalization + EQ, exposing an intensity blend between dry/wet.

### 4.2 Voice "Regenerate" / Overdub — *high effort, high payoff, gated*
- Change a word in the transcript → AI re-generates that audio **in the speaker's voice**, spliced seamlessly.
- **Dependency:** voice cloning — ElevenLabs (instant voice clone) or similar. Requires per-speaker enrollment: **assign speaker → train/authorize voice model** before regenerate is available (store as `voice_model_id` in §1.1).
- Optional **video/lip regeneration** to match the new word is a separate, much harder model (see §4.4) — ship voice-only first; that's where most of the value is.

### 4.3 Green screen / background removal — *medium effort*
- Remove background, resize/reposition the speaker, place B-roll behind.
- **Dependency:** Robust Video Matting (RVM) or MediaPipe Selfie Segmentation for real-time-ish matting; composite via ffmpeg/WebGL. Genuinely buildable without a paid API.

### 4.4 Lip-sync regeneration — *defer*
- Re-render the mouth to match regenerated/changed words.
- Hard, expensive, quality is hit-or-miss even in Descript. **Defer.** Don't promise it in v1.

### 4.5 Eye-contact / gaze correction — *defer / low priority*
- The source itself calls this "a little wonky." Low ROI. **Defer.**

### 4.6 Quick Design / "Underlord" auto-edit — *build last, as an agent layer*
- Underlord = a **conversational agent** that takes context (topic, audience, platform, goal) and proposes/implements edits (B-roll, text overlays, clips, captions, music), with a yes/approve gate per change.
- Build this as an orchestration layer **on top of** the deterministic tools in Phases 1–2 — it should call those same functions, not have its own private editing path. This fits PodClick's parallel multi-agent architecture directly: each tool in §3 becomes a callable action the agent can invoke.
- Two prompt-design rules pulled straight from the source, worth encoding into the agent's system prompt: (a) treat it as a creative partner you brief with context, not a command line; (b) instruct in terms of what TO do ("make this more conversational"), not what NOT to do ("make this less robotic").
- Add a per-change approval/diff UI so the agent's edits are reviewable before commit (Underlord's habit of over-applying — e.g. captioning the whole video — is a real failure mode; the approval gate is the mitigation).

---

## 5. Suggested build order (dependency-correct)

0. **Phase 0 — WebM duration fix + "Open in Editor" entry point on Library items.** Blocking. The editor opens a Library asset; that asset needs a finite duration first.
1. **§1 data model + §2.1 transcription + §2.2 text editing + §2.6 export.** Nothing works until edit-the-transcript → render works end to end. Get a video in, delete words, export a correct cut.
2. **§2.3 scenes + §2.4 layouts + §2.5 timeline.** Structural editing.
3. **§3.1–3.4** deterministic AI cleanup (filler/retakes/gaps/correction) — biggest time-savings, lowest risk.
4. **§3.6–3.10** captions, elements, B-roll, audio, transitions — the production polish.
5. **§3.11–3.12** text-gen AI + clips — mostly reuse of existing PodClick agents.
6. **§4.1 / 4.3** studio sound + green screen (API/ML, contained scope).
7. **§4.2** voice regenerate (gated on voice-model enrollment).
8. **§4.6** Underlord agent layer last — it's only as good as the tools beneath it.
9. **Defer §4.4 lip-sync, §4.5 gaze.**

---

## 6. External dependencies summary

| Capability | Suggested provider | Notes |
|---|---|---|
| Word-level transcription | Deepgram / AssemblyAI / WhisperX | Need word timings, not segment timings |
| Studio sound | Adobe Podcast API / Auphonic / ffmpeg+rnnoise | Expose intensity blend |
| Voice regenerate | ElevenLabs (or equiv.) | Per-speaker enrollment + consent gate |
| Background removal | RVM / MediaPipe | Self-hostable, no paid API |
| Stock media | Pexels/Pixabay/Giphy or PodClick assets | B-roll + GIFs |
| Text gen (chapters/summary/social/clip-pick) | Claude API | Reuse existing PodClick agents |
| Render | ffmpeg | Concat/cut, xfade transitions, compositing |

---

## 7. Acceptance checks (give these to QC guards)

- Compiled EDL has no zero/negative-length segments and no timestamps outside source bounds.
- Scene boundaries always land on word boundaries.
- Deleting/reordering transcript words produces a byte-correct cut on export (sample-accurate audio, frame-accurate video at keyframes).
- Subtitle export reflects `corrected_text` overrides, not raw ASR.
- Voice regenerate is unavailable until a speaker has an authorized `voice_model_id`.
- Every Underlord-applied change is individually reversible and was shown in the approval diff before commit.
