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


# ── the schedule must survive every update (found in review, 2026-10-01) ──────
#
# The original STORED fixture was a DRAFT with no `displayDate`, so it could not
# represent the dangerous case at all: a post that is already scheduled. PUT
# replaces the post, and the stored time comes back from GHL as `displayDate` —
# which is not a key the PUT accepts — so any update that did not restate it sent
# `status="scheduled"` with no time. GHL takes that silently and then either
# publishes immediately or never. On a real Instagram account both are wrong.

SCHEDULED = dict(
    STORED,
    status="scheduled",
    displayDate="2026-10-02T14:00:00.000Z",
)


@pytest.fixture
def scheduled(monkeypatch):
    cap = _Capture()
    monkeypatch.setattr("services.ghl_adapter.httpx.AsyncClient", lambda *a, **k: cap)
    adapter = GHLAdapter()
    monkeypatch.setattr(adapter, "_get_token", lambda loc: "tok")
    monkeypatch.setattr(adapter, "_get_location", lambda loc: "loc_1")

    async def _get_post(location_id, post_id):
        return dict(SCHEDULED)

    monkeypatch.setattr(adapter, "get_post", _get_post)
    return adapter, cap


@pytest.mark.asyncio
async def test_caption_only_edit_keeps_the_existing_schedule(scheduled):
    """The regression this section exists for: editing text must not move the post."""
    adapter, cap = scheduled
    await adapter.update_post("loc_1", "p1", caption="revised wording")
    assert cap.payload["status"] == "scheduled"
    assert cap.payload["scheduleDate"] == "2026-10-02T14:00:00.000Z", \
        "the stored displayDate must be restated as scheduleDate"
    assert cap.payload["summary"] == "revised wording"


@pytest.mark.asyncio
async def test_promoting_a_draft_without_a_time_is_refused(scheduled, monkeypatch):
    """`status="scheduled"` and nothing else is one forgotten argument from disaster."""
    from services.social_service import SocialPublishError

    adapter, _ = scheduled

    async def _draft(location_id, post_id):
        return dict(STORED)          # a draft: no displayDate to fall back on

    monkeypatch.setattr(adapter, "get_post", _draft)
    with pytest.raises(SocialPublishError, match="no date"):
        await adapter.update_post("loc_1", "p1", status="scheduled")


@pytest.mark.asyncio
async def test_an_explicit_time_overrides_the_stored_one(scheduled):
    adapter, cap = scheduled
    await adapter.update_post("loc_1", "p1", scheduled_at="2026-10-09T14:00:00Z")
    assert cap.payload["scheduleDate"] == "2026-10-09T14:00:00Z"


@pytest.mark.asyncio
async def test_unscheduling_to_draft_drops_the_time(scheduled):
    adapter, cap = scheduled
    await adapter.update_post("loc_1", "p1", status="draft")
    assert cap.payload["status"] == "draft"
    assert "scheduleDate" not in cap.payload


# ── timezone, published posts, and empty media ────────────────────────────────

@pytest.mark.asyncio
async def test_an_unzoned_time_is_refused(scheduled):
    """Springfield is UTC-5/-6, so letting GHL pick is a multi-hour publish error."""
    from services.social_service import SocialPublishError

    adapter, _ = scheduled
    with pytest.raises(SocialPublishError, match="timezone-qualified"):
        await adapter.update_post("loc_1", "p1", status="scheduled",
                                  scheduled_at="2026-10-02T14:00:00")


@pytest.mark.asyncio
async def test_an_offset_time_is_accepted(scheduled):
    adapter, cap = scheduled
    await adapter.update_post("loc_1", "p1", scheduled_at="2026-10-02T09:00:00-05:00")
    assert cap.payload["scheduleDate"] == "2026-10-02T09:00:00-05:00"


@pytest.mark.asyncio
async def test_a_stored_time_is_trusted_without_the_strict_check(monkeypatch):
    """Validation applies to caller input only — a stored value must never hard-fail
    an ordinary caption edit, even if GHL omits the offset."""
    cap = _Capture()
    monkeypatch.setattr("services.ghl_adapter.httpx.AsyncClient", lambda *a, **k: cap)
    adapter = GHLAdapter()
    monkeypatch.setattr(adapter, "_get_token", lambda loc: "tok")
    monkeypatch.setattr(adapter, "_get_location", lambda loc: "loc_1")

    async def _get_post(location_id, post_id):
        return dict(STORED, status="scheduled", displayDate="2026-10-02T14:00:00")

    monkeypatch.setattr(adapter, "get_post", _get_post)
    await adapter.update_post("loc_1", "p1", caption="x")
    assert cap.payload["scheduleDate"] == "2026-10-02T14:00:00"


@pytest.mark.asyncio
async def test_a_published_post_is_not_blindly_rewritten(scheduled, monkeypatch):
    from services.social_service import SocialPublishError

    adapter, _ = scheduled

    async def _published(location_id, post_id):
        return dict(STORED, status="published")

    monkeypatch.setattr(adapter, "get_post", _published)
    with pytest.raises(SocialPublishError, match="already published"):
        await adapter.update_post("loc_1", "p1", caption="late fix")


@pytest.mark.asyncio
async def test_a_mediafree_read_is_refused_rather_than_stripping_the_video(scheduled, monkeypatch):
    """GHL ACCEPTS an empty media array, so this is the one path with no backstop."""
    from services.social_service import SocialProviderError

    adapter, _ = scheduled

    async def _no_media(location_id, post_id):
        return {k: v for k, v in STORED.items() if k != "media"}

    monkeypatch.setattr(adapter, "get_post", _no_media)
    with pytest.raises(SocialProviderError, match="no media"):
        await adapter.update_post("loc_1", "p1", caption="x")


@pytest.mark.asyncio
async def test_a_time_ghl_stored_differently_is_surfaced(monkeypatch):
    """A 2xx that quietly stored another time must not read as success."""
    from services.social_service import SocialPublishError

    class _Liar(_Capture):
        async def put(self, url, headers=None, json=None):
            self.payload = json

            class _R:
                status_code = 200
                text = "{}"

                @staticmethod
                def json():
                    return {"post": {"status": "scheduled",
                                     "displayDate": "2026-11-30T14:00:00.000Z"}}
            return _R()

    cap = _Liar()
    monkeypatch.setattr("services.ghl_adapter.httpx.AsyncClient", lambda *a, **k: cap)
    adapter = GHLAdapter()
    monkeypatch.setattr(adapter, "_get_token", lambda loc: "tok")
    monkeypatch.setattr(adapter, "_get_location", lambda loc: "loc_1")

    async def _get_post(location_id, post_id):
        return dict(SCHEDULED)

    monkeypatch.setattr(adapter, "get_post", _get_post)
    with pytest.raises(SocialPublishError, match="different time"):
        await adapter.update_post("loc_1", "p1", status="scheduled",
                                  scheduled_at="2026-10-02T14:00:00Z")


# ── one unwrap rule for both read paths ───────────────────────────────────────

def test_unwrap_is_shared_and_consistent():
    """get_post used truthiness, update_post used isinstance, so `{"post": {}}` came
    back as the envelope from one and as {} from the other."""
    from services.ghl_adapter import _unwrap_post

    flat = {"_id": "a", "status": "draft"}
    assert _unwrap_post(flat) == flat
    assert _unwrap_post({"post": {"_id": "b"}}) == {"_id": "b"}
    assert _unwrap_post({"results": {"post": {"_id": "c"}}}) == {"_id": "c"}
    # an empty nested post must not shadow the envelope
    env = {"post": {}, "status": "draft"}
    assert _unwrap_post(env) == env


def test_unwrap_rejects_a_non_object_body():
    from services.ghl_adapter import _unwrap_post
    from services.social_service import SocialProviderError

    with pytest.raises(SocialProviderError):
        _unwrap_post(["not", "an", "object"])


def test_both_comment_spellings_are_whitelisted():
    """_build_payload sends firstComment; GHL's docs say followUpComment. Listing both
    is safe because only keys GHL actually returned are echoed back."""
    assert "firstComment" in GHLAdapter._UPDATABLE_FIELDS
    assert "followUpComment" in GHLAdapter._UPDATABLE_FIELDS
