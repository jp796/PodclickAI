"""The Crew — the common agent contract (AGENTS_HUB_SPEC §2.1).

Pure data and a small runtime context. Nothing here imports main or brick_agent:
the registry and the runners depend on this module, so it has to stay a leaf.

Python 3.9: Optional/List/Dict, no `X | Y` unions, no walrus.
"""
from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

# ── vocabularies ──────────────────────────────────────────────────────────────

CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ("research", "Research"),
    ("plan", "Plan"),
    ("create", "Create"),
    ("publish", "Publish"),
    ("grow", "Grow"),
)
CATEGORY_IDS = tuple(c[0] for c in CATEGORIES)

FIELD_TYPES = frozenset({
    "text", "textarea", "number", "select", "multiselect", "checkbox", "date",
    "project", "job_ref", "persona", "files",
})

OUTPUT_KINDS = frozenset({
    "text", "cards", "table", "json", "audio", "video", "images", "file", "link",
})
EDITABLE_KINDS = frozenset({"text", "cards"})

FOUNDATION_MODES = frozenset({"none", "required", "optional", "inherited"})

REQUIREMENTS = frozenset({
    "youtube_api", "elevenlabs", "higgsfield", "pexels", "ghl", "openai", "anthropic",
})

JOB_STATUSES = frozenset({
    "queued", "running", "needs_approval", "committing",
    "done", "failed", "rejected", "cancelled",
})
TERMINAL_STATUSES = frozenset({"done", "failed", "rejected", "cancelled"})
STEP_STATUSES = frozenset({"pending", "running", "completed", "failed", "skipped"})


# ── specs ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str                      # user-facing, construction vocabulary
    type: str                       # one of FIELD_TYPES
    required: bool = False
    default: Any = None
    options: Tuple[str, ...] = ()   # select / multiselect
    job_ref_agents: Tuple[str, ...] = ()   # job_ref: which agents' finished jobs qualify
    max_len: Optional[int] = None
    help: str = ""
    # Additive to the §2.1 shape: numeric bounds for `number`, count bounds for
    # `files`/`multiselect`. The roster needs them (remix 0–3, max_clips 1–12,
    # photos 4–25) and the alternative was every runner re-validating by hand.
    min_value: Optional[float] = None
    max_value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "options": list(self.options),
            "job_ref_agents": list(self.job_ref_agents),
            "max_len": self.max_len,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "help": self.help,
        }


@dataclass(frozen=True)
class AgentSpec:
    id: str                         # snake_case, stable forever (it is in job files)
    name: str
    category: str                   # one of CATEGORY_IDS
    job: str                        # the one-sentence job
    icon: str
    runner: str                     # dotted module path
    fields: Tuple[FieldSpec, ...]
    run_tier: str = "draftsman"
    spend_tier: Optional[str] = None
    commit_tier: Optional[str] = None      # None = agent never commits
    foundation: str = "none"               # one of FOUNDATION_MODES
    requires: Tuple[str, ...] = ()         # subset of REQUIREMENTS (hard requirements)
    spends: Tuple[str, ...] = ()           # human copy for the run form cost line
    wraps: Tuple[str, ...] = ()            # routes / UI it wraps
    # Additive: "fill in at least one of these" groups (Content Scheduler takes
    # topics_job OR topics; Avatar Video takes script OR topic).
    require_one_of: Tuple[Tuple[str, ...], ...] = ()

    def field(self, name: str) -> Optional[FieldSpec]:
        for f in self.fields:
            if f.name == name:
                return f
        return None


# ── outputs and results ───────────────────────────────────────────────────────

@dataclass
class Output:
    kind: str
    label: str
    value: Any = None
    schema: Optional[str] = None
    file: Optional[str] = None
    url: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"id": self.id, "kind": self.kind, "label": self.label}
        if self.schema is not None:
            d["schema"] = self.schema
        if self.value is not None:
            d["value"] = self.value
        if self.file is not None:
            d["file"] = self.file
        if self.url is not None:
            d["url"] = self.url
        if self.meta:
            d["meta"] = dict(self.meta)
        return d


@dataclass
class CommitPlan:
    """A short human summary of what approving will do. Nothing executable."""
    summary: str


@dataclass
class AgentResult:
    outputs: List[Output] = field(default_factory=list)
    commit: Optional[CommitPlan] = None


# ── errors a runner may raise ─────────────────────────────────────────────────

class JobCancelled(Exception):
    """Raised by ctx.check_cancelled() when the user hit Stop."""


class StepError(Exception):
    """A step failed with a message that is safe to show the user (Brick voice)."""


class RouteNotAllowed(Exception):
    """A runner called a route it did not declare. A programming error."""


# ── input validation ──────────────────────────────────────────────────────────

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, tuple)) and len(value) == 0:
        return True
    return False


def validate_input(
    spec: AgentSpec,
    raw: Optional[Dict[str, Any]],
    job_ref_ok: Optional[Callable[[str, Tuple[str, ...]], bool]] = None,
    upload_ok: Optional[Callable[[str], bool]] = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Check an input dict against the spec's fields.

    Returns (clean, errors). `errors` maps field name -> user-facing message; empty
    means valid. Unknown keys are dropped so a stray client field can never reach a
    runner. `job_ref_ok` / `upload_ok` let the job store check references it owns.
    """
    raw = raw if isinstance(raw, dict) else {}
    clean: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    for f in spec.fields:
        present = f.name in raw and not _blank(raw.get(f.name))
        if not present:
            if f.required:
                errors[f.name] = "Required."
            elif f.default is not None:
                clean[f.name] = f.default
            continue
        value = raw[f.name]
        err: Optional[str] = None

        if f.type in ("text", "textarea", "project", "persona"):
            if not isinstance(value, str):
                err = "Needs to be text."
            else:
                value = value.strip()
                if f.max_len is not None and len(value) > f.max_len:
                    err = "Too long — keep it under {} characters.".format(f.max_len)

        elif f.type == "number":
            try:
                if isinstance(value, bool):
                    raise ValueError("bool")
                num = float(value)
                value = int(num) if num.is_integer() else num
            except (TypeError, ValueError):
                err = "Needs to be a number."
            else:
                if f.min_value is not None and value < f.min_value:
                    err = "Has to be at least {:g}.".format(f.min_value)
                elif f.max_value is not None and value > f.max_value:
                    err = "Can't be more than {:g}.".format(f.max_value)

        elif f.type == "select":
            if not isinstance(value, str) or (f.options and value not in f.options):
                err = "Pick one of the listed options."

        elif f.type == "multiselect":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                err = "Pick from the listed options."
            elif f.options and any(v not in f.options for v in value):
                err = "Pick from the listed options."
            else:
                value = list(dict.fromkeys(value))
                if f.min_value is not None and len(value) < f.min_value:
                    err = "Pick at least {:g}.".format(f.min_value)
                elif f.max_value is not None and len(value) > f.max_value:
                    err = "Pick no more than {:g}.".format(f.max_value)

        elif f.type == "checkbox":
            if isinstance(value, bool):
                pass
            elif value in ("true", "1", 1, "on"):
                value = True
            elif value in ("false", "0", 0, "off"):
                value = False
            else:
                err = "Needs to be on or off."

        elif f.type == "date":
            if not isinstance(value, str) or not _DATE_RE.match(value):
                err = "Use a date like 2026-10-02."
            else:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    err = "That date doesn't exist."

        elif f.type == "job_ref":
            if not isinstance(value, str):
                err = "Pick a finished work order."
            elif job_ref_ok is not None and not job_ref_ok(value, f.job_ref_agents):
                err = "That work order isn't finished, or it's from the wrong crew member."

        elif f.type == "files":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                err = "Upload the files first."
            elif upload_ok is not None and any(not upload_ok(v) for v in value):
                err = "One of those uploads is missing — upload it again."
            elif f.min_value is not None and len(value) < f.min_value:
                err = "Add at least {:g} files.".format(f.min_value)
            elif f.max_value is not None and len(value) > f.max_value:
                err = "No more than {:g} files.".format(f.max_value)

        else:  # pragma: no cover — FIELD_TYPES is checked by the registry tests
            err = "Unknown field type."

        if err is not None:
            errors[f.name] = err
        else:
            clean[f.name] = value

    for group in spec.require_one_of:
        if not any(not _blank(clean.get(name)) for name in group):
            labels = [spec.field(n).label if spec.field(n) else n for n in group]
            msg = "Fill in one of: " + " or ".join(labels) + "."
            for name in group:
                errors.setdefault(name, msg)

    return clean, errors


# ── runtime context ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentContext:
    """
    What a runner gets. The job store builds it; runners never touch job files.

    `persist` writes the job after every transition. `route_caller` and
    `brand_loader` are injected so the context stays free of main/foundation
    imports at module load and is testable without a server or database.
    """

    def __init__(
        self,
        job: Dict[str, Any],
        output_dir: Path,
        persist: Callable[[], None],
        allowed_routes: Tuple[str, ...] = (),
        route_caller: Optional[Callable[..., Awaitable[Tuple[int, Any]]]] = None,
        brand_loader: Optional[Callable[..., Awaitable[Any]]] = None,
        media_lookup: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._job = job
        self._persist = persist
        self.output_dir = output_dir
        self.allowed_routes = tuple(allowed_routes)
        self._route_caller = route_caller
        self._brand_loader = brand_loader
        self._media_lookup = media_lookup

    # identity
    @property
    def job_id(self) -> str:
        return self._job["id"]

    @property
    def location_id(self) -> str:
        return self._job.get("location_id") or ""

    @property
    def initiator(self) -> str:
        return self._job.get("initiator", "user")

    @property
    def input(self) -> Dict[str, Any]:
        return self._job.get("input") or {}

    # steps
    def _find_step(self, key: str) -> Optional[Dict[str, Any]]:
        for s in self._job["steps"]:
            if s["key"] == key:
                return s
        return None

    def declare_steps(self, steps: List[Tuple[str, str]]) -> None:
        """Pre-populate pending steps so the UI can show the whole plan up front."""
        for key, label in steps:
            if self._find_step(key) is None:
                self._job["steps"].append({
                    "key": key, "label": label, "status": "pending",
                    "error": None, "started_at": None, "ended_at": None,
                })
        self._persist()

    @contextlib.asynccontextmanager
    async def step(self, key: str, label: str):
        self.check_cancelled()
        s = self._find_step(key)
        if s is None:
            s = {"key": key, "label": label, "status": "pending", "error": None,
                 "started_at": None, "ended_at": None}
            self._job["steps"].append(s)
        s["label"] = label
        s["status"] = "running"
        s["started_at"] = _now_iso()
        s["error"] = None
        self._persist()
        try:
            yield s
        except JobCancelled:
            s["status"] = "skipped"
            s["ended_at"] = _now_iso()
            self._persist()
            raise
        except Exception as exc:
            s["status"] = "failed"
            s["error"] = str(exc)[:500] if isinstance(exc, StepError) else (
                "Hit a snag on this step ({}).".format(type(exc).__name__))
            s["ended_at"] = _now_iso()
            self._persist()
            raise
        else:
            s["status"] = "completed"
            s["ended_at"] = _now_iso()
            self._persist()

    def skip_step(self, key: str, label: str, reason: str = "") -> None:
        s = self._find_step(key)
        if s is None:
            s = {"key": key, "label": label, "status": "pending", "error": None,
                 "started_at": None, "ended_at": None}
            self._job["steps"].append(s)
        s["status"] = "skipped"
        s["error"] = reason or None
        s["ended_at"] = _now_iso()
        self._persist()

    # outputs, warnings, usage
    def add_output(self, output: Output) -> Dict[str, Any]:
        if output.kind not in OUTPUT_KINDS:
            raise ValueError("unknown output kind {!r}".format(output.kind))
        if output.id is None:
            output.id = "o{}".format(len(self._job["outputs"]) + 1)
        if output.file and not output.url:
            output.url = "/api/agents/jobs/{}/files/{}".format(self.job_id, output.file)
        d = output.to_dict()
        self._job["outputs"].append(d)
        self._persist()
        return d

    def warn(self, msg: str) -> None:
        if msg not in self._job["warnings"]:
            self._job["warnings"].append(msg)
            self._persist()

    def add_usage(self, provider: str, unit: str, amount: float) -> None:
        self._job["usage"].append({"provider": provider, "unit": unit, "amount": amount})
        self._persist()

    # control
    def check_cancelled(self) -> None:
        if self._job.get("cancel_requested") or self._job.get("status") == "cancelled":
            raise JobCancelled()

    # collaborators
    async def call_route(
        self, path: str, body: Optional[Dict[str, Any]] = None, method: str = "POST",
    ) -> Tuple[int, Any]:
        if self._route_caller is None:
            from services.agents.internal import call_route as _call
            caller = _call
        else:
            caller = self._route_caller
        return await caller(path, body, method=method, allowed=self.allowed_routes)

    async def brand_context(self, task_type: Any, topic: Optional[str] = None) -> Any:
        if self._brand_loader is None:
            raise StepError("Foundation isn't wired in this process.")
        ctx = await self._brand_loader(self.location_id, task_type, topic)
        sample_count = None
        try:
            sample_count = int(ctx.metadata.sample_count)
        except Exception:
            pass
        self._job["foundation"] = {
            "used": True,
            "sample_count": sample_count,
            "tier": foundation_tier(sample_count) if sample_count is not None else None,
        }
        self._persist()
        return ctx

    def media(self, capability: str) -> Any:
        if self._media_lookup is None:
            return None
        try:
            return self._media_lookup(capability)
        except Exception:
            return None


def foundation_tier(sample_count: Optional[int]) -> str:
    """Tier thresholds from docs/API.md: not_ready 0-4, thin 5-14, solid 15-49, deep 50+."""
    if sample_count is None:
        return "unknown"
    if sample_count < 5:
        return "not_ready"
    if sample_count < 15:
        return "thin"
    if sample_count < 50:
        return "solid"
    return "deep"
