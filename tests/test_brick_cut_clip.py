"""
Wave 2.5 — Brick's `cut_clip` action (foreman tier).

These are the first tests for services/brick_agent.py. No DB, no network: the
session and the TikTok uploader are both injected. The point is the decision
logic — which clip gets picked, what happens when there isn't one, and whether
an expired TikTok connection is reported as "reconnect me" rather than "the clip
failed".
"""

import uuid

import pytest

from db.models import Clip
from pipeline import tiktok
from services.brick_agent import BrickAgent


LOCATION_ID = uuid.uuid4()
OTHER_LOCATION_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()


class FakeAction:
    """Stands in for a BrickAction row."""

    def __init__(self, location_id=LOCATION_ID):
        self.id = uuid.uuid4()
        self.location_id = location_id
        self.action_type = "cut_clip"


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """Serves `get()` from a dict and `execute()` from a fixed row list."""

    def __init__(self, by_id=None, query_rows=None):
        self._by_id = by_id or {}
        self._query_rows = query_rows or []
        self.executed = 0

    async def get(self, _model, pk):
        return self._by_id.get(str(pk))

    async def execute(self, _stmt):
        self.executed += 1
        return FakeResult(self._query_rows)


def make_clip(rendered_url="/tmp/clip.mp4", location_id=LOCATION_ID, score=5.0,
              caption="A caption", start=0.0, end=30.0, status="rendered"):
    clip = Clip(
        id=uuid.uuid4(),
        project_id=PROJECT_ID,
        location_id=location_id,
        source_start_seconds=start,
        source_end_seconds=end,
        hook_text="A hook",
        clip_caption=caption,
        virality_score=score,
        rendered_url=rendered_url,
        status=status,
    )
    return clip


@pytest.fixture
def agent():
    return BrickAgent()


async def test_payload_without_clip_or_project_is_rejected(agent):
    with pytest.raises(ValueError, match="clip_id or project_id"):
        await agent._dispatch_cut_clip(FakeAction(), {}, FakeSession())


async def test_clip_from_another_location_is_refused(agent):
    """Tenant boundary: a clip_id must belong to the action's own location."""
    foreign = make_clip(location_id=OTHER_LOCATION_ID)
    session = FakeSession(by_id={str(foreign.id): foreign})
    with pytest.raises(PermissionError, match="another location"):
        await agent._dispatch_cut_clip(
            FakeAction(), {"clip_id": str(foreign.id)}, session
        )


async def test_no_rendered_clip_on_project_is_skipped_not_failed(agent):
    result = await agent._dispatch_cut_clip(
        FakeAction(), {"project_id": str(PROJECT_ID)}, FakeSession(query_rows=[])
    )
    assert result["status"] == "skipped"
    assert "no rendered clip" in result["reason"]


async def test_unrendered_clip_is_skipped(agent):
    clip = make_clip(rendered_url=None)
    session = FakeSession(by_id={str(clip.id): clip})
    result = await agent._dispatch_cut_clip(
        FakeAction(), {"clip_id": str(clip.id)}, session
    )
    assert result["status"] == "skipped"
    assert "not been rendered" in result["reason"]
    assert result["clip_id"] == str(clip.id)


async def test_successful_post_reports_publish_id_and_actual_privacy(agent, monkeypatch):
    clip = make_clip()
    session = FakeSession(by_id={str(clip.id): clip})
    seen = {}

    async def fake_upload(**kwargs):
        seen.update(kwargs)
        return {
            "publish_id": "pub_123",
            "url": "https://www.tiktok.com/",
            "privacy_level": "SELF_ONLY",
            "requested_privacy_level": "PUBLIC_TO_EVERYONE",
        }

    monkeypatch.setattr(tiktok, "upload_clip", fake_upload)

    result = await agent._dispatch_cut_clip(
        FakeAction(), {"clip_id": str(clip.id)}, session
    )

    assert result["status"] == "posted"
    assert result["publish_id"] == "pub_123"
    # The honest part: we report what TikTok actually did, not what we asked for.
    assert result["privacy_level"] == "SELF_ONLY"
    assert result["requested_privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert seen["video_path"] == "/tmp/clip.mp4"
    assert seen["duration_sec"] == 30.0


async def test_caption_is_preferred_over_hook_for_the_title(agent, monkeypatch):
    clip = make_clip(caption="Foundation caption")
    session = FakeSession(by_id={str(clip.id): clip})
    seen = {}

    async def fake_upload(**kwargs):
        seen.update(kwargs)
        return {"publish_id": "p", "privacy_level": "SELF_ONLY"}

    monkeypatch.setattr(tiktok, "upload_clip", fake_upload)
    await agent._dispatch_cut_clip(FakeAction(), {"clip_id": str(clip.id)}, session)
    assert seen["title"] == "Foundation caption"


async def test_falls_back_to_hook_when_no_caption(agent, monkeypatch):
    clip = make_clip(caption=None)
    session = FakeSession(by_id={str(clip.id): clip})
    seen = {}

    async def fake_upload(**kwargs):
        seen.update(kwargs)
        return {"publish_id": "p", "privacy_level": "SELF_ONLY"}

    monkeypatch.setattr(tiktok, "upload_clip", fake_upload)
    await agent._dispatch_cut_clip(FakeAction(), {"clip_id": str(clip.id)}, session)
    assert seen["title"] == "A hook"


async def test_expired_tiktok_connection_reports_needs_tiktok(agent, monkeypatch):
    """
    An expired connection is a human task, not a broken clip. If this returned a
    bare failure, the punch list would blame the clip and JP would never learn he
    needs to reconnect.
    """
    clip = make_clip()
    session = FakeSession(by_id={str(clip.id): clip})

    async def fake_upload(**_kwargs):
        raise tiktok.TikTokAuthError("re-authorize at /api/tiktok/auth")

    monkeypatch.setattr(tiktok, "upload_clip", fake_upload)

    result = await agent._dispatch_cut_clip(
        FakeAction(), {"clip_id": str(clip.id)}, session
    )
    assert result["status"] == "needs_tiktok"
    assert "/api/tiktok/auth" in result["reason"]
    assert result["clip_id"] == str(clip.id)


async def test_project_path_uses_the_query_and_posts_top_clip(agent, monkeypatch):
    top = make_clip(score=9.1, caption="Best clip")
    session = FakeSession(query_rows=[top])

    async def fake_upload(**kwargs):
        return {"publish_id": "p9", "privacy_level": "PUBLIC_TO_EVERYONE"}

    monkeypatch.setattr(tiktok, "upload_clip", fake_upload)

    result = await agent._dispatch_cut_clip(
        FakeAction(), {"project_id": str(PROJECT_ID)}, session
    )
    assert session.executed == 1
    assert result["status"] == "posted"
    assert result["clip_id"] == str(top.id)


async def test_explicit_privacy_level_is_passed_through(agent, monkeypatch):
    clip = make_clip()
    session = FakeSession(by_id={str(clip.id): clip})
    seen = {}

    async def fake_upload(**kwargs):
        seen.update(kwargs)
        return {"publish_id": "p", "privacy_level": "SELF_ONLY"}

    monkeypatch.setattr(tiktok, "upload_clip", fake_upload)
    await agent._dispatch_cut_clip(
        FakeAction(), {"clip_id": str(clip.id), "privacy_level": "SELF_ONLY"}, session
    )
    assert seen["privacy_level"] == "SELF_ONLY"
