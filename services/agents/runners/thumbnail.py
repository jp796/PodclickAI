"""
Painter — click-worthy thumbnails or episode posters with your face on them.

AGENTS_HUB_SPEC §1.4.
  1. Concepts: POST /api/yt/cover-forge (Foundation-voiced already) → 3 variants
     with thumbnail_text, colors and image_prompt.
  2. Render, which makes Cover Forge's staged mock real:
     - youtube_thumbnail (1280x720): services.agents.render_thumbnail composites
       the persona photo + thumbnail text over a plate. The plate comes from a
       text_to_image media provider when one is configured (fail-soft: any
       provider trouble falls back with a warning), else from the concept's own
       colors. A provider submit is never retried — resubmitting can double-charge.
     - episode_poster (1080x1080): main._generate_episode_poster with the
       project's episode number, guest and headshots — one per concept, using
       the concept's thumbnail text as the poster line.
Outputs land under ctx.output_dir and are served only by the job files route.
"""
import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.agents.contract import AgentResult, Output, StepError

# Exact internal paths ctx.call_route may hit ("{param}" = one path segment).
ROUTES = (
    "/api/yt/cover-forge",
    "/api/projects/{project_id}",
)

FORMATS = ("youtube_thumbnail", "episode_poster")
PROVIDER_POLL_S = 3.0
PROVIDER_CEILING_S = 5 * 60


def _err(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        msg = data.get("error") or data.get("detail")
        if msg:
            return str(msg)
    return fallback


def _emit(ctx, outputs: List[Any], output: Any) -> None:
    ctx.add_output(output)
    outputs.append(output)


def _persona_photos() -> List[Dict[str, Any]]:
    import main  # lazy, same reason as _dispatch_generator
    return [p for p in main._load_ai_persona().get("photos", []) if Path(p.get("path", "")).exists()]


def _guests() -> List[Dict[str, Any]]:
    import main
    return main._load_guests()


def _poster_tools():
    import main
    return main._generate_episode_poster, main._resolve_headshot


def _file_url(ctx, name: str) -> str:
    return f"/api/agents/jobs/{ctx.job_id}/files/{name}"


async def _provider_plate(ctx, provider: Any, prompt: str, dest: Path) -> Optional[str]:
    """One text_to_image generation through lane D's MediaProvider. Never resubmits."""
    from services.media.base import GenerationRequest  # lane D; absent → caller falls back
    job = await provider.submit(GenerationRequest(capability="text_to_image", prompt=prompt, aspect="16:9"))
    started = time.monotonic()
    while str(getattr(job, "status", "")).lower() not in ("done", "completed", "succeeded", "failed", "error"):
        if time.monotonic() - started > PROVIDER_CEILING_S:
            raise StepError("image provider took too long")
        ctx.check_cancelled()
        await asyncio.sleep(PROVIDER_POLL_S)
        job = await provider.poll(job)
    if str(getattr(job, "status", "")).lower() in ("failed", "error"):
        raise StepError("image provider reported a failed render")
    asset = await provider.fetch(job, dest)
    return str(getattr(asset, "path", dest))


async def run(ctx, inp: Dict[str, Any]) -> AgentResult:
    from services.agents.render_thumbnail import render_thumbnail

    fmt = str(inp.get("format") or "youtube_thumbnail")
    if fmt not in FORMATS:
        raise StepError("Format has to be a YouTube thumbnail or an episode poster.")
    title = str(inp.get("title") or "").strip()
    if not title:
        raise StepError("Give me the video title.")
    out_dir = Path(ctx.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    photos = _persona_photos() if fmt == "youtube_thumbnail" else []
    wanted = str(inp.get("persona_photo") or "")
    persona = next((p for p in photos if p.get("id") == wanted), None) or (photos[0] if photos else None)

    outputs: List[Any] = []
    async with ctx.step("concepts", "Sketching three concepts"):
        status, concepts = await ctx.call_route("/api/yt/cover-forge", {
            "topic": title,
            "market": str(inp.get("market") or ""),
            "reference": str(inp.get("reference_url") or ""),
            "persona_photos": [{"id": p.get("id"), "shot_type": p.get("shot_type", "photo")} for p in photos],
            "selected_persona_photo_id": persona.get("id") if persona else "",
        })
        if status != 200 or not isinstance(concepts, dict):
            raise StepError(_err(concepts, "Cover Forge didn't answer."))
        variants = [v for v in (concepts.get("variants") or []) if isinstance(v, dict)][:3]
        if not variants:
            raise StepError("Cover Forge came back with no concepts — run it again.")

    loop = asyncio.get_event_loop()
    images: List[Dict[str, Any]] = []
    async with ctx.step("render", "Painting the variants"):
        if fmt == "youtube_thumbnail":
            if persona is None:
                ctx.warn("No AI Persona photos yet — thumbnails render text-only. "
                         "Upload headshots in Click Studio → Cover Forge.")
            provider = ctx.media("text_to_image")
            for i, v in enumerate(variants, 1):
                ctx.check_cancelled()
                plate = None
                if provider is not None and v.get("image_prompt"):
                    try:
                        plate = await _provider_plate(ctx, provider, v["image_prompt"], out_dir / f"plate_{i}.png")
                    except Exception as exc:
                        ctx.warn(f"Background {i} fell back to a color plate — the image provider didn't come "
                                 f"through ({exc}).")
                name = f"thumbnail_{i}.png"
                await loop.run_in_executor(None, lambda v=v, name=name, plate=plate: render_thumbnail(
                    str(out_dir / name), v.get("thumbnail_text") or title,
                    background_color=v.get("background_color"), text_color=v.get("text_color"),
                    persona_path=persona.get("path") if persona else None, plate_path=plate))
                images.append({"file": name, "url": _file_url(ctx, name),
                               "label": v.get("thumbnail_text") or f"Variant {i}"})
        else:
            pid = str(inp.get("project_id") or "").strip()
            if not pid:
                raise StepError("Poster mode needs the episode project.")
            status, project = await ctx.call_route(f"/api/projects/{pid}", None, method="GET")
            if status != 200 or not isinstance(project, dict):
                raise StepError(_err(project, "Can't find that episode on the Job Site."))
            by_id = {g.get("id"): g for g in _guests()}
            guest = next((by_id[g] for g in (project.get("guest_ids") or []) if g in by_id), None) or {}
            gname = guest.get("name") or "Guest"
            tagline = guest.get("company") or guest.get("title") or "GUEST"
            make_poster, resolve_headshot = _poster_tools()
            headshot = resolve_headshot(gname)
            for i, v in enumerate(variants, 1):
                ctx.check_cancelled()
                name = f"poster_{i}.png"
                ok, note = await loop.run_in_executor(
                    None, make_poster, str(out_dir / name), project.get("episode_number"),
                    v.get("thumbnail_text") or project.get("title") or title, gname, headshot, tagline)
                if not ok:
                    raise StepError(f"Poster {i} didn't render: {note}")
                if note:
                    ctx.warn(f"Poster {i}: {note}.")
                images.append({"file": name, "url": _file_url(ctx, name),
                               "label": v.get("thumbnail_text") or f"Poster {i}"})

    _emit(ctx, outputs, Output(id="variants", kind="images", label="Variants", value=images))
    _emit(ctx, outputs, Output(id="concepts", kind="json", label="Concepts", value=concepts))
    return AgentResult(outputs=outputs, commit=None)
