"""
Pillar Planner — lay out a 90-day video plan across your content pillars.

AGENTS_HUB_SPEC §1.2. Wraps POST /api/yt/pillar-plan, which now uses the
Blueprint's pillars, weights and full_name (contract #3 fix in the route). An
empty agent_name is left empty on purpose so the route fills it from the
Blueprint — one source of truth for the default.
"""
from typing import Any, Dict, List

from services.agents.contract import AgentResult, Output

ALLOWED_ROUTES = ("POST /api/yt/pillar-plan",)


def _err(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        msg = data.get("error") or data.get("detail")
        if msg:
            return str(msg)
    return fallback


def _emit(ctx, outputs: List[Any], output: Any) -> None:
    ctx.add_output(output)
    outputs.append(output)


def to_cards(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    cards = []
    for p in plan.get("pillars") or []:
        if not isinstance(p, dict):
            continue
        ideas = [{"title": str(i).strip()} for i in (p.get("video_ideas") or []) if str(i).strip()]
        cards.append({
            "pillar": p.get("name", ""),
            "lead_purpose": p.get("lead_type", ""),
            "frequency": p.get("frequency", ""),
            "ideas": ideas,
        })
    return cards


async def run(ctx, inp: Dict[str, Any]) -> AgentResult:
    market = str(inp.get("market") or "").strip()
    if not market:
        raise RuntimeError("Give me a market to plan for.")
    body = {
        "market": market,
        "agent_name": str(inp.get("agent_name") or "").strip(),
        "months_in_market": str(inp.get("months_in_market") or "Just Starting"),
    }

    outputs: List[Any] = []
    async with ctx.step("plan", "Laying out the 90-day plan"):
        status, data = await ctx.call_route("/api/yt/pillar-plan", body)
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(_err(data, "Pillar Planner didn't answer."))
        cards = to_cards(data)
        if not any(c["ideas"] for c in cards):
            raise RuntimeError("The plan came back with no video ideas — run it again.")

    if data.get("_foundation_thin"):
        ctx.warn(f"Thin Foundation ({data.get('_sample_count', 0)} samples) — output will sound less like you.")

    _emit(ctx, outputs, Output(id="pillar_plan", kind="cards", label="90-day pillar plan",
                               schema="pillar_plan", value=cards))
    return AgentResult(outputs=outputs, commit=None)
