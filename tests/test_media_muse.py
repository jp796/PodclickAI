"""
Meta Muse adapter (services/media/muse.py). All HTTP is an in-process
httpx.MockTransport; any request a test did not expect fails the test, so
"zero network calls" is asserted, not assumed. No real credits are spent.
Each guard below was proven by breaking the code it protects.
"""

import base64
import json
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

from services.media import base, registry
from services.media.base import (
    GenerationRequest,
    MediaProviderError,
    ProviderAuthError,
    ProviderCapExceeded,
    ProviderError,
    ProviderNotConfigured,
    ProviderQuotaError,
    ProviderRateLimited,
    ProviderUncertainError,
)
from services.media.muse import MuseProvider

KEY = "mk-SECRET-12345"


class Net:
    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.handler: Optional[Callable[[httpx.Request], httpx.Response]] = None

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler is None:
            raise AssertionError(f"unexpected network call: {request.method} {request.url}")
        return self.handler(request)


@pytest.fixture(autouse=True)
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(base, "HTTP_TRANSPORT", httpx.MockTransport(n._dispatch))
    return n


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    values: Dict[str, Any] = {}

    def fake_setting(name, default=None):
        v = values.get(name)
        return default if v is None or v == "" else v

    monkeypatch.setattr(base, "setting", fake_setting)
    return values


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    rec: List[float] = []

    async def fake_sleep(s):
        rec.append(s)

    monkeypatch.setattr(base, "sleep", fake_sleep)
    return rec


@pytest.fixture
def keyed(cfg):
    cfg["meta_model_api_key"] = KEY
    return cfg


def chat_ok(text="Poured and cured.", usage=None):
    body = {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": usage or {"prompt_tokens": 5, "completion_tokens": 9}}
    return lambda r: httpx.Response(200, json=body)


def sent(net, i=-1) -> Dict[str, Any]:
    return json.loads(net.requests[i].content)


def assert_clean(exc: MediaProviderError):
    blob = " ".join([str(exc), repr(exc), exc.user_message])
    assert KEY not in blob and "Bearer" not in blob and "Traceback" not in blob


# ── configuration ─────────────────────────────────────────────────────────────

async def test_missing_key_is_needs_setup_with_zero_network_calls(net):
    p = MuseProvider()
    assert not p.is_configured()
    with pytest.raises(ProviderNotConfigured) as e:
        await p.complete("hi")
    assert "MODEL_API_KEY" in e.value.user_message
    with pytest.raises(ProviderNotConfigured):
        await p.generate_image("a house")
    assert net.requests == []


def test_env_style_key_name_also_counts(cfg):
    cfg["model_api_key"] = KEY
    assert MuseProvider().is_configured()


def test_registry_lists_muse_and_checks_requirement(cfg):
    assert "muse" in registry.REQUIREMENT_CHECKS
    assert registry.missing_requirements(["muse"]) == ["muse"]
    cfg["meta_model_api_key"] = KEY
    assert registry.missing_requirements(["muse"]) == []
    assert registry.get_provider("text_to_image").name == "muse"


# ── request shape ─────────────────────────────────────────────────────────────

async def test_complete_posts_openai_compatible_with_bearer_and_standard_default(keyed, net):
    net.handler = chat_ok("Hello")
    out = await MuseProvider().complete("Write a line", system="Be brief")
    assert out == "Hello"
    req = net.requests[0]
    assert str(req.url) == "https://api.meta.ai/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = sent(net)
    assert body["model"] == "muse-spark-1.3"
    assert body["messages"] == [{"role": "system", "content": "Be brief"},
                                {"role": "user", "content": "Write a line"}]


async def test_detailed_result_is_flagged_ai_generated_with_usage(keyed, net):
    net.handler = chat_ok(usage={"completion_tokens": 77})
    r = await MuseProvider().complete_detailed("x")
    assert r.ai_generated is True and "human-written" in r.disclosure
    assert r.usage["completion_tokens"] == 77 and r.model == "muse-spark-1.3"


# ── contributor / PII ─────────────────────────────────────────────────────────

async def test_contributor_refused_when_pii_marked_even_if_enabled(keyed, net):
    keyed["meta_muse_allow_contributor"] = "1"
    for kwargs in ({"contributor": True}, {"model": "muse-spark-1.3-contributor"}):
        with pytest.raises(MediaProviderError) as e:
            await MuseProvider().complete("Lead: Jane Doe 555-1212", pii=True, **kwargs)
        assert "contributor" in e.value.user_message.lower()
    assert net.requests == []


async def test_global_contributor_default_downgrades_to_standard_for_pii(keyed, net):
    keyed["meta_muse_allow_contributor"] = "1"
    keyed["meta_muse_model"] = "muse-spark-1.3-contributor"
    net.handler = chat_ok()
    await MuseProvider().complete("Lead: Jane", pii=True)
    assert sent(net)["model"] == "muse-spark-1.3"


async def test_contributor_requires_opt_in_setting(keyed, net):
    with pytest.raises(MediaProviderError):
        await MuseProvider().complete("public blog intro", contributor=True)
    assert net.requests == []


async def test_contributor_allowed_for_non_pii_when_opted_in(keyed, net):
    keyed["meta_muse_allow_contributor"] = "true"
    net.handler = chat_ok()
    await MuseProvider().complete("public blog intro", contributor=True)
    assert sent(net)["model"] == "muse-spark-1.3-contributor"


async def test_standard_is_default_even_when_contributor_enabled(keyed, net):
    keyed["meta_muse_allow_contributor"] = "1"
    net.handler = chat_ok()
    await MuseProvider().complete("hi")
    assert sent(net)["model"] == "muse-spark-1.3"


async def test_bad_model_name_rejected_before_call(keyed, net):
    with pytest.raises(ProviderCapExceeded):
        await MuseProvider().complete("hi", model="gpt-4; drop")
    assert net.requests == []


# ── caps ──────────────────────────────────────────────────────────────────────

async def test_max_tokens_default_and_hard_cap(keyed, net):
    net.handler = chat_ok()
    await MuseProvider().complete("hi")
    assert sent(net)["max_tokens"] == 2048
    await MuseProvider().complete("hi", max_tokens=10_000_000)
    assert sent(net)["max_tokens"] == 8192
    keyed["meta_muse_max_tokens_cap"] = 100
    await MuseProvider().complete("hi", max_tokens=5000)
    assert sent(net)["max_tokens"] == 100


async def test_prompt_cap_enforced_before_call(keyed, net):
    keyed["meta_muse_max_prompt_chars"] = 50
    with pytest.raises(ProviderCapExceeded):
        await MuseProvider().complete("a" * 30, system="b" * 30)
    assert net.requests == []


async def test_timeout_setting_reaches_the_client(keyed, net, monkeypatch):
    seen = {}
    real = base.http_client

    def spy(timeout=60.0):
        seen["t"] = timeout
        return real(timeout)

    monkeypatch.setattr(base, "http_client", spy)
    keyed["meta_muse_timeout_s"] = 7
    net.handler = chat_ok()
    await MuseProvider().complete("hi")
    assert seen["t"] == 7.0


async def test_timeout_after_send_is_uncertain_and_never_retried(keyed, net, sleeps):
    def boom(r):
        raise httpx.ReadTimeout("slow", request=r)

    net.handler = boom
    with pytest.raises(ProviderUncertainError) as e:
        await MuseProvider().complete("hi")
    assert_clean(e.value)
    assert len(net.requests) == 1 and sleeps == []


async def test_connect_error_is_clear_and_clean(keyed, net):
    def boom(r):
        raise httpx.ConnectError("dns fail for key " + KEY, request=r)

    net.handler = boom
    with pytest.raises(ProviderError) as e:
        await MuseProvider().complete("hi")
    assert_clean(e.value)
    assert not isinstance(e.value, ProviderUncertainError)


# ── error mapping ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [401, 403])
async def test_auth_errors_are_clear_unretried_and_leak_nothing(keyed, net, status):
    net.handler = lambda r: httpx.Response(status, text=f"bad key {KEY} Traceback")
    with pytest.raises(ProviderAuthError) as e:
        await MuseProvider().complete("hi")
    assert_clean(e.value)
    assert "MODEL_API_KEY" in e.value.user_message
    assert len(net.requests) == 1


async def test_region_error_gets_region_message_not_key_message(keyed, net):
    net.handler = lambda r: httpx.Response(403, json={"error": {"message": "Not available in your region"}})
    with pytest.raises(ProviderError) as e:
        await MuseProvider().complete("hi")
    assert "US" in e.value.user_message and not isinstance(e.value, ProviderAuthError)
    assert_clean(e.value)


async def test_429_backs_off_then_succeeds(keyed, net, sleeps):
    calls = {"n": 0}

    def h(r):
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] < 3 else chat_ok("ok")(r)

    net.handler = h
    assert await MuseProvider().complete("hi") == "ok"
    assert sleeps == [5.0, 15.0]


async def test_429_gives_up_after_budget(keyed, net, sleeps):
    net.handler = lambda r: httpx.Response(429)
    with pytest.raises(ProviderRateLimited):
        await MuseProvider().complete("hi")
    assert len(net.requests) == 4 and sleeps == [5.0, 15.0, 45.0]


async def test_quota_and_5xx(keyed, net):
    net.handler = lambda r: httpx.Response(402)
    with pytest.raises(ProviderQuotaError):
        await MuseProvider().complete("hi")
    net.requests.clear()
    net.handler = lambda r: httpx.Response(503, text="oops " + KEY)
    with pytest.raises(ProviderError) as e:
        await MuseProvider().complete("hi")
    assert_clean(e.value)
    assert len(net.requests) == 1


async def test_garbage_and_empty_completion_fail_loudly(keyed, net):
    net.handler = lambda r: httpx.Response(200, json={"nope": 1})
    with pytest.raises(ProviderError):
        await MuseProvider().complete("hi")
    net.handler = chat_ok("   ")
    with pytest.raises(ProviderError):
        await MuseProvider().complete("hi")


# ── image + provenance ────────────────────────────────────────────────────────

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))


def image_ok(extra_item=None, extra_body=None, headers=None):
    item = {"b64_json": base64.b64encode(PNG).decode(), **(extra_item or {})}
    body = {"data": [item], **(extra_body or {})}
    return lambda r: httpx.Response(200, json=body, headers=headers or {})


async def test_image_bytes_returned_untouched_with_provenance_kept(keyed, net):
    net.handler = image_ok(
        extra_item={"c2pa_manifest": "abc123", "watermark": {"type": "invisible", "id": "w1"}},
        extra_body={"provenance": {"generator": "muse-image"}},
        headers={"X-Meta-Provenance": "signed", "X-Request-Id": "r1"},
    )
    img = await MuseProvider().generate_image("a craftsman house")
    assert img.data == PNG and img.ai_generated
    assert str(net.requests[0].url) == "https://api.meta.ai/v1/images/generations"
    assert sent(net)["model"] == "muse-image-1.0"
    assert img.provenance["c2pa_manifest"] == "abc123"
    assert img.provenance["watermark"]["id"] == "w1"
    assert img.provenance["provenance"]["generator"] == "muse-image"
    assert img.provenance["response_headers"] == {"x-meta-provenance": "signed"}


async def test_generate_writes_exact_bytes_and_provenance_sidecar(keyed, net, tmp_path):
    net.handler = image_ok(extra_item={"c2pa_manifest": "abc123"})
    dest = tmp_path / "out" / "pic.png"
    asset = await MuseProvider().generate(GenerationRequest("text_to_image", "a house", 0), dest)
    assert dest.read_bytes() == PNG
    side = json.loads((tmp_path / "out" / "pic.png.provenance.json").read_text())
    assert side["provenance"]["c2pa_manifest"] == "abc123" and side["ai_generated"] is True
    assert asset.meta["provenance"]["c2pa_manifest"] == "abc123"
    assert asset.usage == {"provider": "muse", "unit": "images", "amount": 1}


async def test_image_url_download_never_sends_the_key(keyed, net):
    def h(r):
        if r.url.host == "api.meta.ai":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/x.png", "watermark": "w"}]})
        assert "authorization" not in r.headers
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    net.handler = h
    img = await MuseProvider().generate_image("a house")
    assert img.data == PNG and img.provenance["watermark"] == "w"
    assert len(net.requests) == 2


async def test_image_url_must_be_https(keyed, net):
    net.handler = lambda r: httpx.Response(200, json={"data": [{"url": "http://cdn.example/x.png"}]})
    with pytest.raises(ProviderError):
        await MuseProvider().generate_image("a house")
    assert len(net.requests) == 1


async def test_image_auth_error_and_other_capability_unsupported(keyed, net):
    net.handler = lambda r: httpx.Response(401)
    with pytest.raises(ProviderAuthError):
        await MuseProvider().generate_image("a house")
    with pytest.raises(Exception):
        await MuseProvider().generate(GenerationRequest("image_to_video", "x", 5), "x")


async def test_empty_content_from_spent_reasoning_budget_names_the_real_cause(keyed, net):
    """finish_reason=length with null content = tokens spent thinking; 'run it again' would be wrong advice."""
    net.handler = lambda r: httpx.Response(200, json={
        "choices": [{"finish_reason": "length", "message": {"content": None}}],
        "usage": {"completion_tokens": 120, "completion_tokens_details": {"reasoning_tokens": 117}}})
    with pytest.raises(ProviderError) as ei:
        await MuseProvider().complete("hook line", max_tokens=120)
    assert "token cap" in ei.value.user_message
    net.handler = lambda r: httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": None}}]})
    with pytest.raises(ProviderError) as ei2:
        await MuseProvider().complete("hook line")
    assert "token cap" not in ei2.value.user_message
