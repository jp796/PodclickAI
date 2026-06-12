// Render reference — compile an EDL into a single ffmpeg invocation.
//
// Uses a trim+concat filtergraph (not the concat demuxer) so it handles
// arbitrary in/out points and multiple source clips in one pass. This is the
// Phase 1 export path: transcript edits -> EDL -> this -> final file.
//
// Preview can use a lighter path (seek + stream-copy), but a single accurate
// encode here is what "export a correct cut" depends on.

import type { EDL } from "./edl";
import type { MediaId } from "./types";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const pexec = promisify(execFile);

/**
 * Build the ffmpeg argv that renders `edl` to `outputPath`.
 * `mediaUriById` maps every MediaId referenced by the EDL to a file path/URL.
 */
export function buildRenderArgs(
  edl: EDL,
  mediaUriById: Record<MediaId, string>,
  outputPath: string,
): string[] {
  // Stable input index per media.
  const inputOrder: MediaId[] = [];
  for (const seg of edl.segments) {
    if (!inputOrder.includes(seg.mediaId)) inputOrder.push(seg.mediaId);
  }
  const inputIdx = new Map<MediaId, number>(inputOrder.map((m, i) => [m, i]));

  const args: string[] = ["-y"];
  for (const m of inputOrder) {
    const uri = mediaUriById[m];
    if (!uri) throw new Error(`No URI provided for media ${m}`);
    args.push("-i", uri);
  }

  // One trimmed [v]/[a] pair per segment, then concat them all.
  const filters: string[] = [];
  const concatInputs: string[] = [];
  edl.segments.forEach((s, i) => {
    const j = inputIdx.get(s.mediaId)!;
    filters.push(`[${j}:v]trim=start=${s.in}:end=${s.out},setpts=PTS-STARTPTS[v${i}]`);
    filters.push(`[${j}:a]atrim=start=${s.in}:end=${s.out},asetpts=PTS-STARTPTS[a${i}]`);
    concatInputs.push(`[v${i}][a${i}]`);
  });
  const n = edl.segments.length;
  filters.push(`${concatInputs.join("")}concat=n=${n}:v=1:a=1[outv][outa]`);

  args.push(
    "-filter_complex", filters.join(";"),
    "-map", "[outv]", "-map", "[outa]",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    "-c:a", "aac", "-b:a", "192k",
    "-movflags", "+faststart",
    outputPath,
  );
  return args;
}

export async function render(
  edl: EDL,
  mediaUriById: Record<MediaId, string>,
  outputPath: string,
): Promise<void> {
  if (edl.segments.length === 0) throw new Error("Empty EDL: nothing to render");
  await pexec("ffmpeg", buildRenderArgs(edl, mediaUriById, outputPath));
}
