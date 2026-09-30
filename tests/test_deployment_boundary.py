"""Exercise the outer ASGI gate without running application lifespan/providers."""

import pytest

from services.deployment_boundary import DeploymentBoundary


def scope(path="/api/projects", method="GET", client="127.0.0.1", host="localhost:8765",
          scheme="http", kind="http", extra=(), server_port=8765):
    value = {
        "type": kind, "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": scheme, "path": path,
        "raw_path": path.encode(), "root_path": "", "query_string": b"",
        "client": (client, 52111), "server": ("127.0.0.1", server_port),
        "headers": [(b"host", host.encode())] + list(extra),
    }
    return value


async def run(request, mode="locked"):
    delivered, messages = [], []

    async def inner(current, receive, send):
        delivered.append(current)
        if current["type"] == "http":
            await send({"type": "http.response.start", "status": 209, "headers": []})
            await send({"type": "http.response.body", "body": b"private app"})
        elif current["type"] == "websocket":
            await send({"type": "websocket.accept"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await DeploymentBoundary(inner, mode=mode)(request, receive, send)
    return delivered, messages


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["locked", "owner_beta", "public", "LOCAL", "", None, False, {}])
@pytest.mark.parametrize("path", ["/", "/projects", "/api/projects", "/media/secret.mp3", "/static/project.html", "/docs"])
async def test_default_unknown_and_unimplemented_modes_deny_every_private_route(mode, path):
    delivered, messages = await run(scope(path), mode)
    assert not delivered
    assert messages[0]["status"] == 503
    assert b"private app" not in messages[-1]["body"]


@pytest.mark.asyncio
async def test_constructor_default_is_locked():
    gate = DeploymentBoundary(None)
    assert gate.mode == "locked"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_public_health_is_minimal_and_never_invokes_application(method):
    delivered, messages = await run(scope("/health", method, client="203.0.113.8", host="unknown.example"))
    assert not delivered
    assert messages[0]["status"] == 200
    assert messages[-1]["body"] == (b"" if method == "HEAD" else b'{"status":"ok"}')
    assert (b"cache-control", b"no-store") in messages[0]["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,method", [
    ("/health", "POST"), ("/health/", "GET"), ("/health/deep", "GET"),
    ("/api/billing/webhook/", "POST"), ("/api/billing/webhook/status", "POST"),
    ("/api/billing/webhook", "GET"), ("/api/billing/webhook", "OPTIONS"),
    ("/api/billing/webhook", "HEAD"), ("/api/billing/checkout", "POST"),
])
async def test_public_exceptions_do_not_match_prefixes_other_methods_or_checkout(path, method):
    delivered, messages = await run(scope(path, method))
    assert not delivered
    assert messages[0]["status"] == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("path,raw", [
    ("/health", b"/%68ealth"),
    ("/api/billing/webhook", b"/api/billing/%77ebhook"),
    ("/api/billing/webhook", b"//api/billing/webhook"),
])
async def test_encoded_public_path_aliases_stay_locked(path, raw):
    request = scope(path, "GET" if path == "/health" else "POST")
    request["raw_path"] = raw
    delivered, messages = await run(request)
    assert not delivered
    assert messages[0]["status"] == 503


@pytest.mark.asyncio
async def test_only_exact_webhook_post_reaches_independent_signature_verifier():
    request = scope("/api/billing/webhook", "POST", client="203.0.113.8", host="podclick.example")
    delivered, messages = await run(request)
    assert delivered == [request]
    assert messages[0]["status"] == 209
    # Passing to the router is not signature verification; billing API tests
    # separately prove invalid/missing Stripe signatures do not process events.


@pytest.mark.asyncio
@pytest.mark.parametrize("client,host", [
    ("127.0.0.1", "localhost:8765"), ("127.0.0.1", "127.0.0.1:8765"),
    ("::1", "[::1]:8765"), ("127.0.0.2", "localhost:8765"),
])
async def test_explicit_local_mode_allows_real_loopback_with_trusted_authority(client, host):
    delivered, messages = await run(scope("/media/test.mp3", client=client, host=host), "local")
    assert delivered
    assert messages[0]["status"] == 209


@pytest.mark.asyncio
@pytest.mark.parametrize("client", ["203.0.113.8", "192.168.1.5", "::ffff:127.0.0.1", "invalid", "::1%lo0"])
async def test_spoofed_local_host_never_overrides_remote_or_invalid_socket(client):
    delivered, messages = await run(scope(client=client), "local")
    assert not delivered
    assert messages[0]["status"] == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("host", [
    "evil.example:8765", "localhost.evil.example:8765", "localhost.:8765",
    "127.1:8765", "2130706433:8765", "127.0.0.2:8765", "::1:8765",
    "localhost:8765@evil.example", "localhost:8765/path", "localhost:8765 ",
    " localhost:8765", "localhost:8765,evil.example", "localhost:9999", "localhost",
    "localhost:0", "localhost:65536", "localhost:+8765", "localhost:not-a-port",
])
async def test_malformed_rebound_and_wrong_port_authorities_are_rejected(host):
    delivered, messages = await run(scope(host=host), "local")
    assert not delivered
    assert messages[0]["status"] == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [
    (b"host", b"localhost:8765"), (b"forwarded", b"for=127.0.0.1"), (b"x-forwarded", b"127.0.0.1"),
    (b"x-forwarded-for", b"127.0.0.1"), (b"x-forwarded-host", b"localhost:8765"),
    (b"x-forwarded-proto", b"http"), (b"x-real-ip", b"127.0.0.1"),
    (b"X-Forwarded-Port", b"8765"), (b"x-original-url", b"/health"),
    (b"cf-connecting-ip", b"127.0.0.1"),
])
async def test_duplicate_host_and_proxy_headers_fail_closed_even_on_loopback(header):
    delivered, messages = await run(scope(extra=[header]), "local")
    assert not delivered
    assert messages[0]["status"] == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [
    "null", "https://evil.example", "http://localhost:9999", "https://localhost:8765",
    "http://127.0.0.1:8765", "http://localhost:8765/", "http://localhost:8765?x=1",
    "http://localhost:8765#x", "http://localhost:8765@evil.example",
    "http://localhost:8765 http://evil.example", "", " http://localhost:8765",
])
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_cross_origin_and_null_origins_cannot_access_private_http(origin, method):
    delivered, messages = await run(scope(method=method, extra=[(b"origin", origin.encode())]), "local")
    assert not delivered
    assert messages[0]["status"] == 403


@pytest.mark.asyncio
async def test_duplicate_origins_are_rejected():
    origin = (b"origin", b"http://localhost:8765")
    delivered, messages = await run(scope(method="POST", extra=[origin, origin]), "local")
    assert not delivered
    assert messages[0]["status"] == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    [], [(b"origin", b"http://localhost:8765")],
    [(b"origin", b"http://localhost:8765"), (b"sec-fetch-site", b"same-origin")],
])
async def test_local_cli_and_same_origin_browser_writes_work(extra):
    delivered, messages = await run(scope(method="POST", extra=extra), "local")
    assert delivered
    assert messages[0]["status"] == 209


@pytest.mark.asyncio
@pytest.mark.parametrize("site", [b"cross-site", b"same-site"])
async def test_fetch_metadata_blocks_unsafe_missing_origin_requests(site):
    delivered, messages = await run(scope(method="POST", extra=[(b"sec-fetch-site", site)]), "local")
    assert not delivered
    assert messages[0]["status"] == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/ws/job", "/ws/clip/job", "/health", "/api/billing/webhook"])
@pytest.mark.parametrize("mode", ["locked", "owner_beta", "unknown"])
async def test_locked_websockets_include_http_public_exceptions(path, mode):
    delivered, messages = await run(scope(path, kind="websocket", scheme="ws"), mode)
    assert not delivered
    assert messages == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [[], [(b"origin", b"null")], [(b"origin", b"https://evil.example")]])
async def test_local_websocket_requires_same_origin(extra):
    delivered, messages = await run(scope("/ws/job", kind="websocket", scheme="ws", extra=extra), "local")
    assert not delivered
    assert messages == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
@pytest.mark.parametrize("client,extra", [
    ("203.0.113.8", []), ("127.0.0.1", [(b"x-forwarded-for", b"127.0.0.1")]),
])
async def test_websocket_cannot_spoof_socket_or_proxy_identity(client, extra):
    delivered, messages = await run(scope("/ws/job", kind="websocket", scheme="ws", client=client,
                                         extra=[(b"origin", b"http://localhost:8765")] + extra), "local")
    assert not delivered
    assert messages == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme,origin,host,port", [
    ("ws", b"http://localhost:8765", "localhost:8765", 8765),
    ("wss", b"https://[::1]", "[::1]", 443),
])
async def test_local_websocket_accepts_exact_origin_with_correct_scheme(scheme, origin, host, port):
    delivered, messages = await run(scope("/ws/job", kind="websocket", scheme=scheme, host=host,
                                         server_port=port, extra=[(b"origin", origin)]), "local")
    assert delivered
    assert messages == [{"type": "websocket.accept"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["client", "server", "headers"])
async def test_missing_trust_inputs_fail_closed(missing):
    request = scope()
    request.pop(missing)
    delivered, messages = await run(request, "local")
    assert not delivered
    assert messages[0]["status"] == 403


@pytest.mark.asyncio
async def test_lifespan_is_passed_through_without_creating_false_authentication():
    request = {"type": "lifespan"}
    delivered, _ = await run(request)
    assert delivered == [request]
