"""
GHLAdapter.update_post — promoting an existing planner post without duplicating it.

`publish()` and `schedule()` both CREATE a post, so there was no way to move a
draft to scheduled; doing it with publish() would have put a second copy of every
clip in the planner. update_post() does a read-modify-write against GHL's PUT.

Three live-API facts drive the shape of this code, all verified 2026-10-01 and all
counter-intuitive enough to re-break without tests:

  1. PUT replaces the post, so untouched fields must be carried forward or the
     media, caption and tiktokPostDetails are silently lost.
  2. GHL is asymmetric on media type: POST accepts {"type": "video"} and GET
     returns {"type": "video"}, but PUT 422s unless it is a MIME type.
  3. Omitting the media key is not an escape — PUT 422s with "media must be an
     array with media objects or an empty array".

A 2xx from GHL does not prove a schedule took; the stored `displayDate` does.
"""

import pytest

from services.ghl_adapter import GHLAdapter, _to_mime_media


# ── _to_mime_media: the PUT-vs-GET type asymmetry ─────────────────────────────

@pytest.mark.parametrize("url,stored,expected", [
    ("https://cdn/x/a.mp4",  "video", "video/mp4"),
    ("https://cdn/x/a.m4v",  "video", "video/x-m4v"),
    ("https://cdn/x/a.mov",  "video", "video/quicktime"),
    ("https://cdn/x/a.webm", "video", "video/webm"),
    ("https://cdn/x/a.png",  "image", "image/png"),
    ("https://cdn/x/a.jpg",  "image", "image/jpeg"),
    ("https://cdn/x/a.jpeg", "image", "image/jpeg"),
    ("https://cdn/x/a.gif",  "image", "image/gif"),
])
def test_extension_drives_the_mime_type(url, stored, expected):
    assert _to_mime_media({"url": url, "type": stored})["type"] == expected


def test_query_string_does_not_defeat_extension_detection():
    """Signed CDN URLs carry query strings; a naive endswith would miss the type."""
    got = _to_mime_media({"url": "https://cdn/x/a.mp4?sig=abc&exp=1", "type": "video"})
    assert got["type"] == "video/mp4"


def test_an_already_mime_type_is_left_alone():
    got = _to_mime_media({"url": "https://cdn/x/a.mp4", "type": "video/mp4"})
    assert got["type"] == "video/mp4"


def test_unknown_extension_falls_back_to_the_stored_kind():
    assert _to_mime_media({"url": "https://cdn/blob", "type": "video"})["type"] == "video/mp4"
    assert _to_mime_media({"url": "https://cdn/blob", "type": "image"})["type"] == "image/jpeg"


def test_bare_video_type_is_never_sent_through():
    """The exact 422 this helper exists to prevent."""
    out = _to_mime_media({"url": "https://cdn/x/a.mp4", "type": "video"})
    assert out["type"] != "video", "a bare 'video' type 422s on GHL's PUT"
    assert "/" in out["type"]


def test_other_media_keys_are_preserved():
    got = _to_mime_media({"url": "https://cdn/x/a.mp4", "type": "video", "thumbnail": "t.jpg"})
    assert got["thumbnail"] == "t.jpg"


def test_input_is_not_mutated():
    original = {"url": "https://cdn/x/a.mp4", "type": "video"}
    _to_mime_media(original)
    assert original["type"] == "video", "the stored post must not be edited in place"


# ── update_post payload construction ──────────────────────────────────────────

class _Capture:
    """Stand-in for httpx: records the PUT body, returns a success envelope."""

    def __init__(self):
        self.payload = None
        self.url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def put(self, url, headers=None, json=None):
        self.url, self.payload = url, json

        class _R:
            status_code = 200
            text = "{}"

            @staticmethod
            def json():
                return {"success": True, "post": {"status": json.get("status"),
                                                  "displayDate": json.get("scheduleDate")}}
        return _R()


STORED = {
    "type": "post",
    "accountIds": ["acct_ig"],
    "summary": "a Foundation-voiced caption",
    "status": "draft",
    "media": [{"url": "https://cdn/x/clip.mp4", "type": "video"}],
    "tiktokPostDetails": {"privacyLevel": "PUBLIC_TO_EVERYONE", "enableComment": True},
    # server-owned keys GHL's GET returns and its PUT rejects
    "_id": "abc123",
    "insights": {"views": 0},
    "createdAt": "2026-10-01T01:38:42.243Z",
    "previewLink": "https://preview",
    "locationId": "loc_1",
    "deleted": False,
}


@pytest.fixture
def captured(monkeypatch):
    cap = _Capture()
    monkeypatch.setattr("services.ghl_adapter.httpx.AsyncClient", lambda *a, **k: cap)
    adapter = GHLAdapter()
    monkeypatch.setattr(adapter, "_get_token", lambda loc: "tok")
    monkeypatch.setattr(adapter, "_get_location", lambda loc: "loc_1")

    async def _get_post(location_id, post_id):
        return dict(STORED)

    monkeypatch.setattr(adapter, "get_post", _get_post)
    return adapter, cap


@pytest.mark.asyncio
async def test_schedule_date_is_the_request_key(captured):
    """GHL takes `scheduleDate` and echoes it as `displayDate` — not the reverse."""
    adapter, cap = captured
    await adapter.update_post("loc_1", "p1", status="scheduled",
                              scheduled_at="2026-10-02T14:00:00Z")
    assert cap.payload["scheduleDate"] == "2026-10-02T14:00:00Z"
    assert "displayDate" not in cap.payload, "displayDate is a response field, not a request one"
    assert cap.payload["status"] == "scheduled"


@pytest.mark.asyncio
async def test_media_is_carried_forward_as_mime(captured):
    adapter, cap = captured
    await adapter.update_post("loc_1", "p1", status="scheduled",
                              scheduled_at="2026-10-02T14:00:00Z")
    assert cap.payload["media"] == [{"url": "https://cdn/x/clip.mp4", "type": "video/mp4"}]


@pytest.mark.asyncio
async def test_media_key_is_always_present(captured):
    """PUT 422s on a missing media key even when nothing about media changed."""
    adapter, cap = captured
    await adapter.update_post("loc_1", "p1", status="draft")
    assert "media" in cap.payload


@pytest.mark.asyncio
async def test_caption_and_tiktok_details_survive_a_status_only_change(captured):
    """The whole point of read-modify-write — a reschedule must not strip the post."""
    adapter, cap = captured
    await adapter.update_post("loc_1", "p1", status="scheduled",
                              scheduled_at="2026-10-02T14:00:00Z")
    assert cap.payload["summary"] == "a Foundation-voiced caption"
    assert cap.payload["tiktokPostDetails"]["privacyLevel"] == "PUBLIC_TO_EVERYONE"
    assert cap.payload["accountIds"] == ["acct_ig"]


@pytest.mark.asyncio
async def test_server_owned_fields_are_not_echoed_back(captured):
    """GHL rejects unknown properties, so the GET's own bookkeeping must be dropped."""
    adapter, cap = captured
    await adapter.update_post("loc_1", "p1", status="scheduled",
                              scheduled_at="2026-10-02T14:00:00Z")
    for leaked in ("_id", "insights", "createdAt", "previewLink", "locationId", "deleted"):
        assert leaked not in cap.payload, f"{leaked} is server-owned and PUT rejects it"


@pytest.mark.asyncio
async def test_no_schedule_date_is_sent_when_none_given(captured):
    """Flipping back to draft must not stamp or move a schedule."""
    adapter, cap = captured
    await adapter.update_post("loc_1", "p1", status="draft")
    assert "scheduleDate" not in cap.payload


@pytest.mark.asyncio
async def test_status_defaults_to_the_stored_value(captured):
    adapter, cap = captured
    await adapter.update_post("loc_1", "p1", caption="new words")
    assert cap.payload["status"] == "draft", "an unspecified status must not arm a draft"
    assert cap.payload["summary"] == "new words"


@pytest.mark.asyncio
async def test_it_puts_to_the_single_post_url(captured):
    adapter, cap = captured
    await adapter.update_post("loc_1", "p1", status="draft")
    assert cap.url.endswith("/social-media-posting/loc_1/posts/p1")


# ── contract #5: GHL calls live only in the adapter ───────────────────────────

def test_only_the_adapter_talks_to_the_planner_update_endpoint():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []
    for path in list(root.glob("*.py")) + list((root / "services").glob("*.py")) \
            + list((root / "workers").glob("*.py")):
        if path.name == "ghl_adapter.py":
            continue
        src = path.read_text()
        if "social-media-posting" in src:
            offenders.append(path.name)
    assert not offenders, f"contract #5: GHL calls belong in the adapter, found in {offenders}"
