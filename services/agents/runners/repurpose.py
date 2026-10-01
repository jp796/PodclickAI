"""
Clip Dispatcher ("Repurpose") — turn an episode's best moments into Instagram and TikTok drafts.

AGENTS_HUB_SPEC §1.3.

Build: preview the clips that WILL be sent — rendered, not removed, highest
virality first, capped at max_clips — the same selection distribute-shorts makes.
Nothing reaches GHL in the build phase.

Commit: POST /api/projects/{id}/distribute-shorts {platforms, max_clips}. That
route is already idempotent (legacy_metadata.shorts_distributed), drafts-only,
and uploads through the GHL media library via the adapter (contract #5), so this
runner adds no GHL call of its own and a second approve cannot double-draft.
"""
from typing import Any, Dict, List

from services.agents.contract import AgentResult, CommitPlan, Output, StepError

# Exact internal paths ctx.call_route may hit ("{param}" = one path segment).
ROUTES = (
    "/api/projects/{project_id}/clips",
    "/api/projects/{project_id}/distribute-shorts",
)

PLATFORMS = ("instagram", "tiktok")


def _err(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        msg = data.get("error") or data.get("detail")
        if msg:
            return str(msg)
    return fallback


def _emit(ctx, outputs: List[Any], output: Any) -> None:
    ctx.add_output(output)
    outputs.append(output)


def _settings(inp: Dict[str, Any]):
    pid = str(inp.get("project_id") or "").strip()
    try:
        max_clips = max(1, min(12, int(inp.get("max_clips") or 3)))
    except (TypeError, ValueError):
        max_clips = 3
    platforms = [p for p in (inp.get("platforms") or list(PLATFORMS)) if p in PLATFORMS]
    return pid, max_clips, platforms or list(PLATFORMS)


async def run(ctx, inp: Dict[str, Any]) -> AgentResult:
    pid, max_clips, platforms = _settings(inp)
    if not pid:
        raise StepError("Pick the episode whose clips you want sent.")

    outputs: List[Any] = []
    async with ctx.step("clips", "Picking the best moments"):
        status, clips = await ctx.call_route(f"/api/projects/{pid}/clips", None, method="GET")
        if status != 200 or not isinstance(clips, list):
            raise StepError(_err(clips, "Couldn't load that project's clips."))
        ready = [c for c in clips if c.get("rendered_url") and c.get("status") != "removed"]
        ready.sort(key=lambda c: float(c.get("virality_score") or 0), reverse=True)
        picks = ready[:max_clips]
        if not picks:
            raise StepError("No rendered clips on that project yet — run Click Studio first.")

    cards = []
    for c in picks:
        caption = (c.get("clip_caption") or "").strip()
        cards.append({
            "clip_id": c.get("id"),
            "video_url": f"/api/projects/{pid}/clips/{c.get('id')}/video.mp4",
            "caption": caption or (c.get("hook_text") or ""),
            "raw_hook": not caption,
            "virality_score": c.get("virality_score"),
        })
    if any(card["raw_hook"] for card in cards):
        ctx.warn("Some clips have a raw hook only — run Click Studio first for captions in your voice.")

    _emit(ctx, outputs, Output(id="clips", kind="cards", label="Clips to send", schema="clip", value=cards))
    noun = "clip" if len(cards) == 1 else "clips"
    summary = f"Draft {len(cards)} {noun} to {' + '.join(p.title() if p != 'tiktok' else 'TikTok' for p in platforms)} in GHL"
    return AgentResult(outputs=outputs, commit=CommitPlan(summary=summary))


async def commit(ctx, job: Dict[str, Any], edits: Dict[str, Any]) -> Dict[str, Any]:
    stored = job.get("commit_result")
    if stored:
        return stored
    pid, max_clips, platforms = _settings(job.get("input") or {})
    if not pid:
        raise StepError("This work order lost its project — run it again.")
    status, data = await ctx.call_route(f"/api/projects/{pid}/distribute-shorts",
                                        {"platforms": platforms, "max_clips": max_clips})
    if status != 200 or not isinstance(data, dict):
        raise StepError(_err(data, "GHL didn't take the drafts."))
    return {
        "created": data.get("created") or [],
        "skipped": data.get("skipped") or [],
        "ghl_planner": data.get("planner_url") or "https://app.gohighlevel.com/",
        "summary": f"{len(data.get('created') or [])} drafts in the GHL planner",
    }
