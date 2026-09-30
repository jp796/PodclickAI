"""Fail-closed perimeter for the existing single-owner PodClick application.

This is not SaaS authentication or tenant isolation. The only enabled mode is
explicit ``local`` access from the actual loopback socket with a local authority.
Run uvicorn with ``--no-proxy-headers``: otherwise middleware can rewrite the
socket peer before this boundary sees it. Do not expose local mode through a
reverse proxy or tunnel, even a proxy running on the same machine.

Wrap the complete ASGI application, outside every route/static mount/middleware.
Lifespan is passed through; the application must separately disable publishing
workers unless a supported deployment mode explicitly allows them.

The sole public application route is the exact billing webhook POST. Its router
must independently validate Stripe's signature, timestamp and request size
before processing any event. The boundary never treats a webhook as a session.
"""

import ipaddress
import re
from urllib.parse import urlsplit


_LOCAL_AUTHORITY = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?$")
_SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))
_FORWARDED_HEADERS = frozenset((
    b"forwarded", b"x-real-ip", b"x-original-host", b"x-original-url",
    b"x-rewrite-url", b"x-host", b"true-client-ip", b"cf-connecting-ip",
))
_HEALTH = b'{"status":"ok"}'
_LOCKED = b'{"error":"PodClick is not available in this deployment mode."}'
_FORBIDDEN = b'{"error":"Local access is required."}'


def _authority(value, default_port):
    """Parse only literal allowed authorities, without permissive URL parsing."""
    if not isinstance(value, str):
        return None
    match = _LOCAL_AUTHORITY.fullmatch(value.lower())
    if not match:
        return None
    port = int(match.group(2)) if match.group(2) else default_port
    if not 1 <= port <= 65535:
        return None
    return match.group(1), port


def _exact_path(scope, expected):
    # Reject encoded aliases/trailing slashes; public exceptions must not grow
    # through a proxy's or router's path normalization.
    return (scope.get("path") == expected
            and scope.get("raw_path", expected.encode("ascii")) == expected.encode("ascii")
            and not scope.get("root_path"))


def _local_request(scope):
    """Return whether the request meets the narrow trusted-local contract."""
    try:
        client = scope.get("client")
        if not client or not isinstance(client[0], str) or "%" in client[0]:
            return False
        if not ipaddress.ip_address(client[0]).is_loopback:
            return False

        headers = {}
        for name, value in scope.get("headers", ()):
            name = name.lower()
            if name in _FORWARDED_HEADERS or name == b"x-forwarded" or name.startswith(b"x-forwarded-"):
                return False
            headers.setdefault(name, []).append(value)
        hosts = headers.get(b"host", ())
        if len(hosts) != 1:
            return False

        scheme = scope.get("scheme")
        if scheme not in ("http", "https", "ws", "wss"):
            return False
        origin_scheme = "https" if scheme in ("https", "wss") else "http"
        default_port = 443 if origin_scheme == "https" else 80
        authority = _authority(hosts[0].decode("ascii"), default_port)
        server = scope.get("server")
        if not authority or not server or authority[1] != server[1]:
            return False

        origins = headers.get(b"origin", ())
        if len(origins) > 1:
            return False
        if origins:
            origin = origins[0].decode("ascii")
            parsed = urlsplit(origin)
            if (parsed.scheme != origin_scheme or parsed.path or parsed.query
                    or parsed.fragment or origin != "{}://{}".format(parsed.scheme, parsed.netloc)):
                return False
            if _authority(parsed.netloc, default_port) != authority:
                return False
        elif scope["type"] == "websocket":
            # Browsers always send Origin on WebSocket handshakes. Non-browser
            # clients must supply the same local Origin explicitly as well.
            return False

        if scope["type"] == "websocket" or scope.get("method") not in _SAFE_METHODS:
            sites = headers.get(b"sec-fetch-site", ())
            if len(sites) > 1 or (sites and sites[0] not in (b"same-origin", b"none")):
                return False
        return True
    except (ValueError, TypeError, AttributeError, IndexError, UnicodeError):
        # Malformed authorities/scopes never downgrade into local access.
        return False


class DeploymentBoundary:
    """Pure ASGI wrapper; omitted, unknown, and owner_beta modes remain locked."""

    def __init__(self, app, mode="locked"):
        self.app = app
        self.mode = "local" if mode == "local" else "locked"

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "http":
            if scope.get("method") in ("GET", "HEAD") and _exact_path(scope, "/health"):
                await self._respond(scope, send, 200, _HEALTH)
                return
            if scope.get("method") == "POST" and _exact_path(scope, "/api/billing/webhook"):
                await self.app(scope, receive, send)
                return

        if self.mode == "local" and _local_request(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        status, body = (503, _LOCKED) if self.mode == "locked" else (403, _FORBIDDEN)
        await self._respond(scope, send, status, body)

    @staticmethod
    async def _respond(scope, send, status, body):
        await send({
            "type": "http.response.start", "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ],
        })
        await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else body})
