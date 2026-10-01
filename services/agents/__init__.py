"""The Crew — PodClick's agent roster (AGENTS_HUB_SPEC.md).

Kept deliberately light: brick_agent imports `services.agents.registry` at module
load to register `agent_run:{id}` tier gates, and Python runs this file first. So
the job store is imported lazily inside `run_agent`, never at package import.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.agents.registry import REGISTRY, get_agent

__all__ = ["REGISTRY", "get_agent", "run_agent"]


async def run_agent(
    agent_id: str,
    input: Optional[Dict[str, Any]] = None,
    initiator: str = "user",
    location_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a work order. Thin wrapper over services.agents.jobs.start."""
    from services.agents import jobs

    return await jobs.start(agent_id, input or {}, initiator=initiator, location_id=location_id)
