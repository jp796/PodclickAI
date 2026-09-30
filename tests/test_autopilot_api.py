"""HTTP contract checks against an isolated release service; no lifespan or providers."""
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

import main
from services.podcast_autopilot import PodcastAutopilot, PlanStore, PlanError
from services.deployment_boundary import DeploymentBoundary

LOCAL_APP = DeploymentBoundary(main.studio_app, mode="local")


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PATH = "/api/projects/{}/autopilot".format(PROJECT_ID)


@pytest.fixture
def service(tmp_path, monkeypatch):
    async def inspect(project_id):
        try:
            uuid.UUID(project_id)
        except ValueError:
            raise PlanError("Invalid project ID.")
        if project_id != PROJECT_ID:
            raise PlanError("Project not found.", 404)
        return {"id": project_id, "status": "review", "title": "Test episode", "show_notes": "Prepared show notes",
                "episode_number": 123, "audio_ready": True, "video_ready": True,
                "audio_path": "test.mp3", "video_path": "test.mp4", "audio_version": "1", "video_version": "1",
                "connections": {"buzzsprout": True, "youtube": True}, "existing": {}, "legacy_queued": False}

    value = PodcastAutopilot(PlanStore(tmp_path), inspect, AsyncMock(), AsyncMock(), clock=lambda: 1700000000)
    monkeypatch.setattr(main, "_podcast_autopilot", lambda: value)
    return value


def config(mode="review"):
    return {"mode": mode, "destinations": ["buzzsprout", "youtube"], "scheduled_at": "2030-01-01T15:00:00+00:00"}


@pytest.mark.asyncio
async def test_preflight_is_read_only_and_returns_destination_readiness(service):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=LOCAL_APP), base_url="http://localhost:8765") as client:
        response = await client.post(PATH + "/preflight", json=config())
        assert response.status_code == 200
        data = response.json()
        assert data["readiness"]["ready"] is True
        assert len(data["available_destinations"]) == 2
        assert data["plan"] is None
        assert service.store.load(PROJECT_ID) is None
        service.perform.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_requires_explicit_approval(service):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=LOCAL_APP), base_url="http://localhost:8765") as client:
        saved = await client.put(PATH, json=config())
        assert saved.status_code == 200
        assert saved.json()["plan"]["status"] == "awaiting_approval"
        rejected = await client.post(PATH + "/run", json={"approve": False})
        assert rejected.status_code == 400
        approved = await client.post(PATH + "/run", json={"approve": True})
        assert approved.status_code == 200
        assert approved.json()["plan"]["status"] == "scheduled"
        paused = await client.post(PATH + "/pause")
        assert paused.status_code == 200
        assert paused.json()["plan"]["status"] == "paused"
        service.perform.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_mode_and_destinations_do_not_create_plan(service):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=LOCAL_APP), base_url="http://localhost:8765") as client:
        for payload in (config("silent"), dict(config(), destinations=[]), dict(config(), destinations=["made-up"]), []):
            response = await client.put(PATH, json=payload)
            assert response.status_code == 400
        assert service.store.load(PROJECT_ID) is None


@pytest.mark.asyncio
async def test_unknown_command_and_missing_project_return_client_errors(service):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=LOCAL_APP), base_url="http://localhost:8765") as client:
        assert (await client.post(PATH + "/publish-all")).status_code == 404
        assert (await client.get(PATH.replace(PROJECT_ID, "invalid"))).status_code == 400
        assert (await client.get(PATH.replace(PROJECT_ID, str(uuid.uuid4())))).status_code == 404
        service.perform.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_json_returns_actionable_400(service):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=LOCAL_APP), base_url="http://localhost:8765") as client:
        response = await client.put(PATH, content="{broken", headers={"Content-Type": "application/json"})
        assert response.status_code == 400
        assert response.json().get("error") or response.json().get("detail")
        assert service.store.load(PROJECT_ID) is None
