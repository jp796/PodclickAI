"""Launch perimeter and mounted billing checks, with no live providers or lifespan."""
from unittest.mock import Mock

import httpx
import pytest

import main
from config import settings
from services.deployment_boundary import DeploymentBoundary


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/projects", "/api/projects", "/api/library", "/docs", "/billing", "/frontend/index.html", "/api/billing/plans"])
async def test_real_application_private_paths_are_locked(path):
    locked = DeploymentBoundary(main.studio_app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=locked), base_url="https://podclick.example") as client:
        response = await client.get(path)
    assert response.status_code == 503
    assert "not available" in response.json()["error"]


@pytest.mark.asyncio
async def test_billing_actions_remain_unauthenticated_even_with_forged_ids():
    local = DeploymentBoundary(main.studio_app, mode="local")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local), base_url="http://localhost:8765") as client:
        for path, method in (("status", "GET"), ("checkout", "POST"), ("portal", "POST")):
            response = await client.request(method, "/api/billing/" + path,
                headers={"X-User-Id": "owner", "X-Location-Id": "owner"}, json={"plan": "fake"})
            assert response.status_code == 401


@pytest.mark.asyncio
async def test_local_billing_page_is_honest_and_health_is_minimal():
    local = DeploymentBoundary(main.studio_app, mode="local")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local), base_url="http://localhost:8765") as client:
        page = await client.get("/billing")
        assert page.status_code == 200
        assert "Payment is not collected here" in page.text
        assert (await client.get("/health")).json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_public_webhook_reaches_signature_verification_not_private_routes(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_testonly")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_testonly")
    monkeypatch.setenv("STRIPE_PRICE_IDS", '{"test":"price_testonly"}')
    monkeypatch.setenv("PODCLICK_PUBLIC_URL", "https://podclick.example")
    locked = DeploymentBoundary(main.studio_app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=locked), base_url="https://podclick.example") as client:
        response = await client.post("/api/billing/webhook", content=b"{}")
    assert response.status_code == 400
    assert "signature" in response.json()["detail"]


@pytest.mark.asyncio
async def test_locked_startup_cannot_schedule_owner_jobs(monkeypatch):
    monkeypatch.setattr(settings, "podclick_deployment_mode", "locked")
    dispatch = Mock(side_effect=AssertionError("No publishing tasks in locked deployment"))
    monkeypatch.setattr(main.asyncio, "ensure_future", dispatch)
    await main._startup()
    dispatch.assert_not_called()
