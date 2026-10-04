"""POST /api/calendar/posts validation (no database needed: all cases fail before any write)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.studio_app)


def test_route_registered():
    import main
    paths = {(m, r.path) for r in main.studio_app.routes for m in getattr(r, "methods", set())}
    assert ("POST", "/api/calendar/posts") in paths


@pytest.mark.parametrize("body,fragment", [
    ({}, "content is required"),
    ({"content": "   "}, "content is required"),
    ({"content": "x" * 10001}, "too long"),
    ({"content": "hi", "date": "not-a-date"}, "ISO-8601"),
    ({"content": "hi", "date": "2026-10-05T10:00:00"}, "timezone"),
])
def test_rejects_bad_input_before_writing(client, body, fragment):
    r = client.post("/api/calendar/posts", json=body)
    assert r.status_code == 400
    assert fragment in r.json()["error"]


def test_social_studio_posts_to_real_calendar():
    from pathlib import Path
    html = Path("frontend/social-studio.html").read_text()
    assert "/api/calendar/posts" in html
    assert "/api/social/calendar" not in html
