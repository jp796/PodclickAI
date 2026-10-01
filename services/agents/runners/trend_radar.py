"""
Trend Radar — pick 8 local video topics that are about to peak, mapped to pillars.

AGENTS_HUB_SPEC §1.1. Wraps POST /api/yt/content-calendar, which now pulls the
Foundation (Blueprint audience, pillars, voice) — the contract #3 fix lives in
the route, so the agent and the old Click Studio panel produce the same thing.
An optional finished Market Scout job feeds its report in as competitor_insights.
"""
from typing import Any, Dict, Iterable, List, Optional

from services.agents.contract import AgentResult, Output

ALLOWED_ROUTES = (
    "POST /api/yt/content-calendar",
    "GET /api/agents/jobs/{job_id}",
)


def _err(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        msg = data.get("error") or data.get("detail")
        if msg:
            return str(msg)
    return fallback


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _find_output(job: Dict[str, Any], oid: str) -> Optional[Any]:
    for o in job.get("outputs") or []:
        if _get(o, "id") == oid:
            return o
    return None


async def _load_ref_job(ctx, job_id: str, agents: Iterable[str]) -> Dict[str, Any]:
    status, job = await ctx.call_route(f"/api/agents/jobs/{job_id}", None, method="GET")
    if status != 200 or not isinstance(job, dict):
        raise RuntimeError("Couldn't find that work order.")
    if job.get("agent_id") not in tuple(agents):
        raise RuntimeError("That work order came from the wrong crew member.")
    if job.get("status") != "done":
        raise RuntimeError("That work order isn't built yet — wait for it to finish.")
    return job


def _emit(ctx, outputs: List[Any], output: Any) -> None:
    ctx.add_output(output)
    outputs.append(output)


async def run(ctx, inp: Dict[str, Any]) -> AgentResult:
    city = str(inp.get("city") or "").strip()
    audience = str(inp.get("audience") or "Relocation Buyers").strip()
    scout_job = str(inp.get("scout_job") or "").strip()
    if not city:
        raise RuntimeError("Give me a city for the radar.")

    insights: Dict[str, Any] = {}
    if scout_job:
        async with ctx.step("scout", "Reading the Market Scout report"):
            job = await _load_ref_job(ctx, scout_job, ("market_scout",))
            report = _find_output(job, "scout_report")
            insights = dict(_get(report, "value") or {}) if report is not None else {}

    outputs: List[Any] = []
    async with ctx.step("topics", "Pulling trend topics"):
        status, data = await ctx.call_route("/api/yt/content-calendar", {
            "city": city, "audience": audience, "competitor_insights": insights,
        })
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(_err(data, "Trend Radar didn't answer."))
        topics = list(data.get("calendar") or [])
        if not topics:
            raise RuntimeError("Trend Radar came back empty — run it again.")

    if data.get("_foundation_thin"):
        ctx.warn(f"Thin Foundation ({data.get('_sample_count', 0)} samples) — output will sound less like you.")

    _emit(ctx, outputs, Output(id="topics", kind="cards", label="Topics", schema="topic", value=topics))
    return AgentResult(outputs=outputs, commit=None)
