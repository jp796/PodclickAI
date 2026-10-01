"""
Test-only stand-in for lane A's agent contract (AGENTS_HUB_SPEC §2.1).

Lane C's runners import ``services.agents.contract``. Until lane A merges, that
module does not exist, so ``install()`` registers a minimal stand-in under that
name — but ONLY when the real module is absent. Once lane A lands, the real
contract is used and these tests exercise the runners against it.

``FakeCtx`` implements the frozen AgentContext surface the runners depend on:
step / call_route / brand_context / media / output_dir / add_output / warn /
check_cancelled. Routes are served from a dict so every test states exactly
which internal calls it expects, and ``calls`` records every one made.

Nothing here is imported by production code.
"""
import dataclasses
import importlib
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _build_contract_module() -> types.ModuleType:
    mod = types.ModuleType("services.agents.contract")

    @dataclasses.dataclass
    class Output:
        id: str
        kind: str
        label: str
        schema: Optional[str] = None
        value: Any = None
        file: Optional[str] = None
        url: Optional[str] = None
        meta: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @dataclasses.dataclass
    class CommitPlan:
        summary: str

    @dataclasses.dataclass
    class AgentResult:
        outputs: List[Any] = dataclasses.field(default_factory=list)
        commit: Optional[CommitPlan] = None

    mod.Output = Output
    mod.CommitPlan = CommitPlan
    mod.AgentResult = AgentResult
    mod.__standin__ = True
    return mod


def install() -> bool:
    """Register the stand-in if lane A's contract is missing. Returns True if used."""
    try:
        importlib.import_module("services.agents.contract")
        return False
    except ModuleNotFoundError as exc:
        if exc.name not in ("services.agents", "services.agents.contract"):
            raise
    sys.modules["services.agents.contract"] = _build_contract_module()
    return True


def output_to_dict(o: Any) -> Dict[str, Any]:
    if isinstance(o, dict):
        return dict(o)
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    return {k: getattr(o, k, None) for k in ("id", "kind", "label", "schema", "value", "file", "url", "meta")}


RouteHandler = Callable[[Optional[dict]], Tuple[int, Any]]


class FakeCtx:
    """AgentContext stand-in. ``routes`` maps "METHOD /path" -> handler(body) or (status, json)."""

    def __init__(self, routes: Optional[Dict[str, Any]] = None, output_dir: Optional[Path] = None,
                 job_id: str = "job-test-1", location_id: str = "11111111-1111-1111-1111-111111111111",
                 media: Optional[Dict[str, Any]] = None):
        self.routes = routes or {}
        self.job_id = job_id
        self.location_id = location_id
        self.initiator = "user"
        self.output_dir = output_dir or Path(".")
        self.outputs: List[Any] = []
        self.warnings: List[str] = []
        self.steps: List[Dict[str, Any]] = []
        self.calls: List[Tuple[str, str, Any]] = []
        self._media = media or {}
        self.cancelled = False

    @asynccontextmanager
    async def step(self, key: str, label: str):
        rec = {"key": key, "label": label, "status": "running", "error": None}
        self.steps.append(rec)
        try:
            yield rec
        except BaseException as exc:
            rec["status"] = "failed"
            rec["error"] = str(exc)
            raise
        else:
            rec["status"] = "completed"

    async def call_route(self, path: str, body: Any = None, method: str = "POST"):
        key = f"{method} {path}"
        self.calls.append((method, path, body))
        if key not in self.routes:
            raise AssertionError(f"unexpected internal call: {key}")
        handler = self.routes[key]
        result = handler(body) if callable(handler) else handler
        return result

    async def brand_context(self, task_type, topic=None):
        raise AssertionError("brand_context not expected in this test")

    def media(self, capability: str):
        return self._media.get(capability)

    def add_output(self, output: Any) -> None:
        self.outputs.append(output)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("cancelled")

    def step_status(self, key: str) -> Optional[str]:
        for s in self.steps:
            if s["key"] == key:
                return s["status"]
        return None

    def output(self, oid: str) -> Optional[Dict[str, Any]]:
        for o in self.outputs:
            d = output_to_dict(o)
            if d.get("id") == oid:
                return d
        return None

    def as_job(self, **extra) -> Dict[str, Any]:
        job = {"id": self.job_id, "status": "needs_approval",
               "outputs": [output_to_dict(o) for o in self.outputs]}
        job.update(extra)
        return job
