// PodClick Studio — transcript-driven editor: core data model.
//
// PRINCIPLE: the transcript is the source of truth. The EDL and the visual
// timeline are DERIVED from the ordered word list (see edl.ts). Do not author
// the EDL by hand and do not treat the timeline as primary.

export type WordId = string;
export type MediaId = string;
export type SpeakerId = string;

export interface Speaker {
  id: SpeakerId;
  name: string;
  /** Set only after voice-clone enrollment (spec §4.2). Gates "Regenerate". */
  voiceModelId: string | null;
}

export interface RegenInfo {
  targetText: string;
  mode: "voice_only" | "voice_and_video";
  status: "pending" | "ready" | "failed";
}

/**
 * A single transcribed word.
 *
 * Order within Project.words IS the edit order — cut/paste reorders this array.
 * Each word retains its SOURCE location (mediaId + start/end) so reordering
 * never loses the link back to the original footage.
 *
 * `seq` is the STABLE original transcription index within a media. It is
 * assigned once at ingest and MUST NOT be renumbered by edits (delete, reorder,
 * cut/paste). The EDL compiler uses (mediaId, seq) — not timestamps — to decide
 * whether two kept words are source-adjacent. This is what lets it preserve
 * natural pauses while still cutting cleanly at deletions and reorder seams.
 */
export interface Word {
  id: WordId;
  seq: number;             // stable original index within its media (set at ingest)
  text: string;
  mediaId: MediaId;        // which source clip this word came from
  start: number;           // source in-point, seconds
  end: number;             // source out-point, seconds
  speaker: SpeakerId;
  deleted: boolean;        // hard-removed from output
  strikethrough: boolean;  // soft-removed (retake review): restorable, excluded from output
  correctedText: string | null; // caption/subtitle override; does NOT change audio
  regen: RegenInfo | null;
}

export interface SourceMedia {
  id: MediaId;
  uri: string;             // path / URL to the DURATION-FIXED media (see media.ts / Phase 0)
  duration: number;        // seconds — MUST be finite. Reject Infinity/NaN before editing.
  hasAudio: boolean;
  hasVideo: boolean;
}

// ---------------------------------------------------------------------------
// Phase 2+ scaffolding (declared now so the schema is forward-compatible).
// Not required for the Phase 1 cut-and-export loop.
// ---------------------------------------------------------------------------

export interface CaptionsConfig {
  enabled: boolean;
  fontFamily: string;
  fontSizePx: number;
  color: string;
}

/**
 * A scene = a contiguous range of the word list. Layouts, B-roll, captions and
 * transitions all attach PER SCENE (spec §1.3 invariant). A "//" in the
 * transcript inserts a scene break.
 */
export interface Scene {
  id: string;
  startWordId: WordId;
  endWordId: WordId;
  layoutId: string | null;
  captions: CaptionsConfig | null;
  transitionIn: string | null;
}

export interface BrandKit {
  colors: Record<string, string>;
  fonts: Record<string, string>;
  logoUri: string | null;
}

export interface Project {
  id: string;
  title: string;
  /** Source clips referenced by words. Keyed lookup by SourceMedia.id. */
  media: SourceMedia[];
  speakers: Speaker[];
  /** THE source of truth. Ordered. Editing this drives everything. */
  words: Word[];
  scenes: Scene[];         // Phase 2+
  brandKit: BrandKit | null;
}
