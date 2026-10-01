"""The Crew — /agents page and /api/agents routes (AGENTS_HUB_SPEC §2.4).

Mounted WITHOUT a prefix (`app.include_router(agents_router)`), so every path here
is absolute. Route ORDER matters: the /api/agents/jobs… and /api/agents/uploads
routes are declared before /api/agents/{agent_id} so "jobs" and "uploads" can never
be captured as an agent id.

Handlers are thin: parse, call services.agents.jobs, map AgentError to its status.
No secret ever appears in a response — provider state is booleans/names only.
"""
from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from services.agents import jobs
from services.agents.contract import CATEGORIES

router = APIRouter()

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

MAX_UPLOAD_FILES = 25
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
_VIDEO_EXT = {".mp4", ".mov"}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _err(exc: jobs.AgentError) -> JSONResponse:
    return JSONResponse(exc.payload(), status_code=exc.status_code)


def _location_id() -> str:
    from config import get_current_location_id

    return get_current_location_id()


@router.on_event("startup")
async def _recover_agent_jobs() -> None:
    # Restart recovery (§2.3). jobs also loads lazily on first use, so this is
    # belt-and-braces for the case where nothing touches the store for a while.
    jobs.recover_jobs()


# ── page ──────────────────────────────────────────────────────────────────────

@router.get("/agents")
async def agents_page():
    page = FRONTEND / "agents.html"
    if not page.is_file():
        return Response("The Crew page isn't on site yet.", status_code=404,
                        media_type="text/plain")
    return FileResponse(page)


# ── roster ────────────────────────────────────────────────────────────────────

@router.get("/api/agents")
async def list_agents():
    loc = _location_id()
    tier = await jobs.current_tier(loc)
    fs = await jobs.foundation_summary(loc)
    categories: List[Dict[str, Any]] = []
    for cat_id, label in CATEGORIES:
        agents = [jobs.serialize_agent(s) for s in jobs.all_specs() if s.category == cat_id]
        categories.append({"id": cat_id, "label": label, "agents": agents})
    return {
        "permit": {"current_tier": tier},
        "foundation": {"tier": fs.get("tier"), "sample_count": fs.get("sample_count")},
        "categories": categories,
    }


# ── jobs (declared BEFORE /api/agents/{agent_id}) ────────────────────────────

@router.get("/api/agents/jobs")
async def list_agent_jobs(
    agent_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(25),
):
    agent_ids = [a for a in (agent_id or "").split(",") if a.strip()] or None
    statuses = [s.strip() for s in (status or "").split(",") if s.strip()] or None
    return {"jobs": jobs.list_jobs(agent_id=agent_ids, statuses=statuses, limit=limit)}


@router.get("/api/agents/jobs/{job_id}")
async def get_agent_job(job_id: str):
    try:
        return jobs.get_job(job_id)
    except jobs.AgentError as exc:
        return _err(exc)


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.post("/api/agents/jobs/{job_id}/approve")
async def approve_agent_job(job_id: str, request: Request):
    body = await _json_body(request)
    try:
        job = await jobs.approve(job_id, body.get("edits"))
    except jobs.AgentError as exc:
        return _err(exc)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except Exception as exc:
        try:
            job = jobs.get_job(job_id)
        except jobs.AgentError:
            job = None
        msg = (job or {}).get("error") or "The commit hit a snag ({}).".format(type(exc).__name__)
        return JSONResponse({"error": msg, "job": job}, status_code=500)
    return {"ok": True, "job": job}


@router.post("/api/agents/jobs/{job_id}/reject")
async def reject_agent_job(job_id: str, request: Request):
    body = await _json_body(request)
    try:
        job = await jobs.reject(job_id, body.get("reason"))
    except jobs.AgentError as exc:
        return _err(exc)
    return {"ok": True, "job": job}


@router.post("/api/agents/jobs/{job_id}/cancel")
async def cancel_agent_job(job_id: str):
    try:
        job = await jobs.cancel(job_id)
    except jobs.AgentError as exc:
        return _err(exc)
    return {"ok": True, "job": job}


@router.get("/api/agents/jobs/{job_id}/files/{name}")
async def agent_job_file(job_id: str, name: str, request: Request):
    try:
        path = jobs.resolve_output_file(job_id, name)
    except jobs.AgentError as exc:
        return _err(exc)

    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    rng = request.headers.get("range")
    if not rng:
        resp = FileResponse(path, media_type=mime)
        resp.headers["Accept-Ranges"] = "bytes"
        return resp

    m = _RANGE_RE.match(rng.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return Response(status_code=416, headers={"Content-Range": "bytes */{}".format(size),
                                                  "Accept-Ranges": "bytes"})
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:  # suffix range: last N bytes
        suffix = int(m.group(2))
        start = max(size - suffix, 0)
        end = size - 1
    end = min(end, size - 1)
    if start >= size or start > end:
        return Response(status_code=416, headers={"Content-Range": "bytes */{}".format(size),
                                                  "Accept-Ranges": "bytes"})

    def _iter():
        remaining = end - start + 1
        with open(path, "rb") as fh:
            fh.seek(start)
            while remaining > 0:
                chunk = fh.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(_iter(), status_code=206, media_type=mime, headers={
        "Content-Range": "bytes {}-{}/{}".format(start, end, size),
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    })


# ── uploads (declared BEFORE /api/agents/{agent_id}) ─────────────────────────

@router.post("/api/agents/uploads")
async def upload_agent_files(files: List[UploadFile] = File(...)):
    if len(files) > MAX_UPLOAD_FILES:
        return JSONResponse({"error": "That's more than {} files — trim the stack."
                             .format(MAX_UPLOAD_FILES)}, status_code=400)
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in _IMAGE_EXT and ext not in _VIDEO_EXT:
            return JSONResponse({"error": "{} isn't a photo or video I can use (jpg, png, "
                                 "webp, heic, mp4, mov).".format(f.filename or "That file")},
                                status_code=400)

    target = jobs.uploads_dir()
    target.mkdir(parents=True, exist_ok=True)
    saved: List[Dict[str, Any]] = []
    written: List[Path] = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        upload_id = uuid.uuid4().hex
        dest = target / (upload_id + ext)
        tmp = target / (".{}.part".format(upload_id))
        total = 0
        too_big = False
        with open(tmp, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    too_big = True
                    break
                out.write(chunk)
        if too_big:
            for p in [tmp] + written:
                try:
                    p.unlink()
                except OSError:
                    pass
            return JSONResponse({"error": "{} is over 50 MB.".format(f.filename or "A file")},
                                status_code=413)
        tmp.replace(dest)
        written.append(dest)
        saved.append({
            "upload_id": upload_id,
            "filename": f.filename,
            "kind": "image" if ext in _IMAGE_EXT else "video",
        })
    return {"uploads": saved}


# ── one agent ─────────────────────────────────────────────────────────────────

@router.get("/api/agents/{agent_id}")
async def get_agent_detail(agent_id: str):
    spec = jobs.get_agent(agent_id)
    if spec is None:
        return JSONResponse({"error": "No crew member by that name."}, status_code=404)
    data = jobs.serialize_agent(spec)
    data["recent_jobs"] = jobs.list_jobs(agent_id=[agent_id], limit=5)
    return data


@router.post("/api/agents/{agent_id}/run")
async def run_agent_route(agent_id: str, request: Request):
    body = await _json_body(request)
    raw = body.get("input")
    if raw is not None and not isinstance(raw, dict):
        return JSONResponse({"error": "input needs to be an object.", "fields": {}},
                            status_code=400)
    try:
        job = await jobs.start(agent_id, raw or {}, initiator="user")
    except jobs.AgentError as exc:
        return _err(exc)
    return JSONResponse({"job_id": job["id"], "status": job["status"]}, status_code=202)
