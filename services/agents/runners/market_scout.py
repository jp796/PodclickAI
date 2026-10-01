"""
Market Scout — find which local real-estate videos beat their channel's size.

AGENTS_HUB_SPEC §1.1. Wraps POST /api/yt/competitor-spy (deterministic YouTube
math, zero LLM) and, optionally, POST /api/yt/scout-remix for the top N concepts
(Foundation-voiced). The spy job runs in main.yt_spy_jobs; this runner reads that
dict directly (lazy ``import main``, the same reason _dispatch_generator does)
rather than polling the GET route.

Quota exhaustion is silent upstream: ``_yt_get`` returns ``{}``, so an empty
quota looks like "a market with no videos" that finished instantly. A run that
completes with zero videos inside QUOTA_WINDOW_S is treated as spent quota.
"""
import asyncio
import time
from typing import Any, Dict, List, Optional

from services.agents.contract import AgentResult, Output

# Exact internal routes this runner may call (call_route allowlist).
ALLOWED_ROUTES = (
    "POST /api/yt/competitor-spy",
    "POST /api/yt/scout-remix",
)

POLL_FIRST_S = 0.25        # fast polls while a quota-empty run would still be finishing
POLL_S = 2.0
FAST_POLL_WINDOW_S = 2.0
TIMEOUT_S = 15 * 60
QUOTA_WINDOW_S = 2.0
QUOTA_MSG = "YouTube quota's spent for today — resets midnight Pacific."

REPORT_KEYS = ("market_demand", "best_format", "opportunity_gap", "hot_searches", "market_standards")


def _spy_jobs() -> Dict[str, Dict[str, Any]]:
    import main  # lazy: main imports the world, and this module must stay importable without it
    return main.yt_spy_jobs


def _err(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        msg = data.get("error") or data.get("detail")
        if msg:
            return str(msg)
    return fallback


def _emit(ctx, outputs: List[Any], output: Any) -> None:
    ctx.add_output(output)
    outputs.append(output)


async def _wait_for_spy(ctx, spy_id: str, started: float):
    while True:
        job = _spy_jobs().get(spy_id)
        if job is None:
            raise RuntimeError("Lost track of the Scout run — run it again.")
        status = job.get("status")
        if status == "complete":
            return job.get("result") or {}, time.monotonic() - started
        if status == "error":
            raise RuntimeError(f"Market Scout stalled: {job.get('error') or 'no reason given'}.")
        elapsed = time.monotonic() - started
        if elapsed > TIMEOUT_S:
            raise RuntimeError("Market Scout ran past 15 minutes — check the YouTube key and run it again.")
        ctx.check_cancelled()
        await asyncio.sleep(POLL_FIRST_S if elapsed < FAST_POLL_WINDOW_S else POLL_S)


async def run(ctx, inp: Dict[str, Any]) -> AgentResult:
    city = str(inp.get("city") or "").strip()
    audience = str(inp.get("audience") or "Relocation Buyers").strip()
    channels = str(inp.get("channels") or "").strip()
    try:
        remix_top = max(0, min(3, int(inp.get("remix_top") or 0)))
    except (TypeError, ValueError):
        remix_top = 0
    if not city:
        raise RuntimeError("Give me a city to scout.")

    outputs: List[Any] = []

    async with ctx.step("scan", "Scanning the local market"):
        status, data = await ctx.call_route(
            "/api/yt/competitor-spy", {"city": city, "audience": audience, "channels": channels})
        if status != 200 or not isinstance(data, dict) or not data.get("job_id"):
            raise RuntimeError(_err(data, "Market Scout didn't start."))
        started = time.monotonic()
        result, elapsed = await _wait_for_spy(ctx, str(data["job_id"]), started)
        videos = list(result.get("top_videos_ranked") or [])
        if not videos and elapsed < QUOTA_WINDOW_S:
            raise RuntimeError(QUOTA_MSG)

    if not videos:
        ctx.warn(f"No local videos turned up for {city} — the report is built from search signals only.")

    report = {k: result.get(k) for k in REPORT_KEYS}
    report["city"] = city
    report["audience"] = audience
    _emit(ctx, outputs, Output(id="scout_report", kind="json", label="Scout report", value=report))
    _emit(ctx, outputs, Output(id="videos", kind="cards", label="Videos beating their channel",
                               schema="video", value=videos))

    if remix_top and videos:
        remixes: List[Dict[str, Any]] = []
        async with ctx.step("remix", "Rewriting the top concepts in your voice"):
            for v in videos[:remix_top]:
                ctx.check_cancelled()
                status, data = await ctx.call_route("/api/yt/scout-remix", {
                    "title": v.get("title", ""), "channel": v.get("channel", ""),
                    "views": v.get("views", 0), "score": v.get("score", 0),
                    "popular": bool(v.get("popular")), "market": city,
                })
                if status == 422 and isinstance(data, dict) and data.get("foundation_not_ready"):
                    ctx.warn("Skipped the remixes — the Foundation isn't poured yet. "
                             "The scout report still shipped.")
                    break
                if status != 200 or not isinstance(data, dict):
                    ctx.warn(f"Couldn't remix \"{v.get('title', '')}\": {_err(data, 'no answer')}.")
                    continue
                remixes.append({
                    "source_title": v.get("title", ""),
                    "hook": data.get("hook", ""), "concept": data.get("concept", ""),
                    "angle": data.get("angle", ""), "cta": data.get("cta", ""),
                })
        if remixes:
            _emit(ctx, outputs, Output(id="remixes", kind="cards", label="Remixed in your voice",
                                       schema="remix", value=remixes))

    return AgentResult(outputs=outputs, commit=None)
