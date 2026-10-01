"""In-process route calls for agent runners (AGENTS_HUB_SPEC §2.1).

Extracted from BrickAgent._dispatch_generator's pattern: call the app's OWN route
through httpx.ASGITransport against `studio_app` (the inner app). Same validation,
same Foundation gate, same model, same output shape — no network hop, no drift.

It bypasses DeploymentBoundary on purpose (the owner's own agent acting
internally), which makes the per-runner allowlist a security boundary. Every call
must match a path the runner declared: exact literals, or templates whose `{param}`
placeholders match exactly one URL-safe segment. Never a prefix, never a wildcard.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Sequence, Tuple

from services.agents.contract import RouteNotAllowed

# Tests set this to a throwaway FastAPI app; production leaves it None and the
# real studio_app is resolved lazily from main (main imports routers at startup,
# so a top-level import here would be circular).
APP: Any = None

_SEGMENT = r"[A-Za-z0-9_.\-]+"


def _template_regex(template: str) -> "re.Pattern[str]":
    parts = re.split(r"(\{[A-Za-z_][A-Za-z0-9_]*\})", template)
    out = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            out.append(_SEGMENT)
        else:
            out.append(re.escape(part))
    return re.compile("^" + "".join(out) + "$")


def route_allowed(path: str, allowed: Sequence[str]) -> bool:
    """True only if `path` matches one declared route exactly (query strings refused)."""
    if not path.startswith("/") or "?" in path or "#" in path or ".." in path:
        return False
    for template in allowed:
        if "*" in template:
            continue  # wildcards are never honoured, even if declared
        if "{" in template:
            if _template_regex(template).match(path):
                return True
        elif path == template:
            return True
    return False


def _resolve_app() -> Any:
    if APP is not None:
        return APP
    import main as _main  # lazy — see APP comment

    app = getattr(_main, "studio_app", None)
    if app is None:
        raise RuntimeError("studio_app not available in this process")
    return app


async def call_route(
    path: str,
    body: Optional[Dict[str, Any]] = None,
    method: str = "POST",
    allowed: Sequence[str] = (),
    timeout: float = 180.0,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Any]:
    """Call one of the app's own routes in-process. Returns (status_code, json)."""
    if not route_allowed(path, allowed):
        raise RouteNotAllowed("{} is not on this runner's route list".format(path))

    import httpx

    app = _resolve_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://agents.internal", timeout=timeout
    ) as client:
        method_u = method.upper()
        if method_u == "GET":
            response = await client.get(path, params=params)
        elif method_u in ("POST", "PUT", "PATCH", "DELETE"):
            response = await client.request(method_u, path, json=body, params=params)
        else:
            raise ValueError("unsupported method {}".format(method))

    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text[:2000]}
    return response.status_code, data
