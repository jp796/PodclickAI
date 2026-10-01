"""
Click Studio — take a recorded project from raw take to a finished, review-ready episode.

AGENTS_HUB_SPEC §1.4. Steps, each through the app's own routes:
  1. auto_edit  -> POST /api/projects/{id}/auto-edit (blocks until the re-encode
     finishes — verified in main.py, it awaits the render in an executor).
     SKIPPED when legacy_metadata.manual_cut_regions already exists and
     force_reedit is off: auto-edit composes on the current take, so running it
     twice cuts twice. The raw take stays on disk either way.
  2. ship_it    -> POST /api/projects/{id}/ship-it, then poll GET /api/projects/{id}
     until review|failed (15 s, 45 min ceiling).
  3. broll      -> POST /api/projects/{id}/auto-broll, then poll its GET.

Guard: a project already in 'processing' is refused outright ("already on the
line"). The Ship It lock lives inside main.py's request handlers and can't be
taken from here, so status is the guard (spec §2.3 / §4.6 #5).
Foundation is inherited: Ship It's show notes, captions and title already go
through get_brand_context. This runner generates nothing itself.
"""
import asyncio
import time
from typing import Any, Dict, List

from services.agents.contract import AgentResult, Output, StepError

# Exact internal paths ctx.call_route may hit ("{param}" = one path segment).
ROUTES = (
    "/api/projects/{project_id}",
    "/api/projects/{project_id}/clips",
    "/api/projects/{project_id}/auto-edit",
    "/api/projects/{project_id}/ship-it",
    "/api/projects/{project_id}/auto-broll",
)

ALLOWED_START = ("recording_done", "review", "failed")
POLL_S = 15.0
SHIP_IT_CEILING_S = 45 * 60
BROLL_CEILING_S = 30 * 60


def _err(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        msg = data.get("error") or data.get("detail")
        if msg:
            return str(msg)
    return fallback


def _flag(inp: Dict[str, Any], key: str, default: bool) -> bool:
    v = inp.get(key)
    if v is None or v == "":
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _emit(ctx, outputs: List[Any], output: Any) -> None:
    ctx.add_output(output)
    outputs.append(output)


async def _get_project(ctx, pid: str) -> Dict[str, Any]:
    status, data = await ctx.call_route(f"/api/projects/{pid}", None, method="GET")
    if status == 404:
        raise StepError("Can't find that project on the Job Site.")
    if status != 200 or not isinstance(data, dict):
        raise StepError(_err(data, "Couldn't load the project."))
    return data


async def _sleep(ctx, started: float, ceiling: float, what: str) -> None:
    if time.monotonic() - started > ceiling:
        raise StepError(f"{what} ran past {int(ceiling // 60)} minutes — check the project page.")
    ctx.check_cancelled()
    await asyncio.sleep(POLL_S)


async def run(ctx, inp: Dict[str, Any]) -> AgentResult:
    pid = str(inp.get("project_id") or "").strip()
    if not pid:
        raise StepError("Pick a project to work on.")
    do_edit = _flag(inp, "auto_edit", True)
    do_ship = _flag(inp, "ship_it", True)
    do_broll = _flag(inp, "broll", False)
    force = _flag(inp, "force_reedit", False)

    async with ctx.step("check", "Checking the project"):
        project = await _get_project(ctx, pid)
        status = project.get("status")
        if status == "processing":
            raise StepError("That project's already on the line.")
        if status not in ALLOWED_START:
            raise StepError(f"That project is '{status}' — Click Studio only works recorded, "
                               "in-review, or stalled projects.")

    if do_edit:
        cuts = (project.get("legacy_metadata") or {}).get("manual_cut_regions")
        if cuts and not force:
            ctx.warn("Skipped auto-edit — this take already has cuts, and running it again would cut "
                     "twice. Turn on \"Re-edit anyway\" to redo it from the current take.")
        else:
            async with ctx.step("auto_edit", "Auto-editing the take"):
                ctx.check_cancelled()
                code, data = await ctx.call_route(f"/api/projects/{pid}/auto-edit", {})
                if code != 200:
                    raise StepError(_err(data, "Auto-edit didn't finish."))

    if do_ship:
        async with ctx.step("ship_it", "Running Ship It"):
            ctx.check_cancelled()
            code, data = await ctx.call_route(f"/api/projects/{pid}/ship-it", {})
            if code != 200:
                raise StepError(_err(data, "Ship It didn't start."))
            started = time.monotonic()
            while True:
                await _sleep(ctx, started, SHIP_IT_CEILING_S, "Ship It")
                project = await _get_project(ctx, pid)
                if project.get("status") == "review":
                    break
                if project.get("status") == "failed":
                    raise StepError("Ship It stalled — open the project to see where it stopped.")

    if do_broll:
        async with ctx.step("broll", "Adding b-roll"):
            ctx.check_cancelled()
            code, data = await ctx.call_route(f"/api/projects/{pid}/auto-broll", {})
            if code != 200:
                raise StepError(_err(data, "B-roll didn't start."))
            started = time.monotonic()
            while True:
                await _sleep(ctx, started, BROLL_CEILING_S, "B-roll")
                code, job = await ctx.call_route(f"/api/projects/{pid}/auto-broll", None, method="GET")
                state = (job or {}).get("status") if isinstance(job, dict) else None
                if state == "done":
                    break
                if state == "done_none":
                    ctx.warn("B-roll found no good cutaway moments — the episode ships without it.")
                    break
                if state == "failed":
                    raise StepError(_err(job, "B-roll stalled."))

    outputs: List[Any] = []
    project = await _get_project(ctx, pid)
    _, clips = await ctx.call_route(f"/api/projects/{pid}/clips", None, method="GET")
    clip_count = sum(1 for c in (clips if isinstance(clips, list) else [])
                     if c.get("rendered_url") and c.get("status") != "removed")

    _emit(ctx, outputs, Output(id="project", kind="link", label="Open the project", url=f"/project/{pid}"))
    _emit(ctx, outputs, Output(id="editor", kind="link", label="Open the editor", url=f"/project/{pid}/edit"))
    if do_ship:
        _emit(ctx, outputs, Output(id="final", kind="video", label="Finished episode",
                                   url=f"/api/projects/{pid}/download-final"))
    _emit(ctx, outputs, Output(id="clips", kind="file", label="Clips rendered", value=clip_count))
    if project.get("show_notes"):
        _emit(ctx, outputs, Output(id="show_notes", kind="text", label="Show notes",
                                   value=project.get("show_notes")))
    return AgentResult(outputs=outputs, commit=None)
