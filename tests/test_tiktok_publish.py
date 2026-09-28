"""
Wave 2.5 — TikTok clip publishing.

Covers the two things that silently broke automated posting before:
  1. privacy_level was hard-coded PUBLIC_TO_EVERYONE, which an unaudited TikTok
     app is not permitted to use;
  2. the refresh token was stored but never used, so posting worked for ~24h
     after authorizing and then 401'd forever.

No network. No real tokens. Every external call is injected.
"""

import time

import pytest

from pipeline import tiktok


# ── privacy negotiation (pure) ────────────────────────────────────────────────

def test_requested_privacy_is_honored_when_allowed():
    allowed = ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]
    assert tiktok.negotiate_privacy_level("PUBLIC_TO_EVERYONE", allowed) == "PUBLIC_TO_EVERYONE"


def test_unaudited_app_degrades_to_self_only_instead_of_failing():
    """An app that has not cleared TikTok audit only gets SELF_ONLY back."""
    assert tiktok.negotiate_privacy_level("PUBLIC_TO_EVERYONE", ["SELF_ONLY"]) == "SELF_ONLY"


def test_falls_back_in_preference_order_not_list_order():
    allowed = ["SELF_ONLY", "FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS"]
    # MUTUAL_FOLLOW_FRIENDS outranks the others in PRIVACY_FALLBACK_ORDER even
    # though SELF_ONLY appears first in the creator's list.
    assert tiktok.negotiate_privacy_level("PUBLIC_TO_EVERYONE", allowed) == "MUTUAL_FOLLOW_FRIENDS"


def test_no_allow_list_passes_request_through():
    assert tiktok.negotiate_privacy_level("PUBLIC_TO_EVERYONE", None) == "PUBLIC_TO_EVERYONE"
    assert tiktok.negotiate_privacy_level("PUBLIC_TO_EVERYONE", []) == "PUBLIC_TO_EVERYONE"


def test_unknown_allow_list_takes_first_option():
    assert tiktok.negotiate_privacy_level("PUBLIC_TO_EVERYONE", ["SOMETHING_NEW"]) == "SOMETHING_NEW"


# ── token state ───────────────────────────────────────────────────────────────

def _set_tokens(monkeypatch, access="", refresh="", expires_at=None, refresh_expires_at=None):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", access)
    monkeypatch.setenv("TIKTOK_REFRESH_TOKEN", refresh)
    monkeypatch.setenv("TIKTOK_TOKEN_EXPIRES_AT", "" if expires_at is None else str(expires_at))
    monkeypatch.setenv(
        "TIKTOK_REFRESH_EXPIRES_AT",
        "" if refresh_expires_at is None else str(refresh_expires_at),
    )


def test_no_access_token_reports_unauthorized(monkeypatch):
    _set_tokens(monkeypatch)
    state = tiktok.token_state()
    assert state["authorized"] is False
    assert state["access_valid"] is False
    assert "authorize" in state["reason"]


def test_live_token_is_valid_and_not_refreshed(monkeypatch):
    now = int(time.time())
    _set_tokens(monkeypatch, access="a", refresh="r", expires_at=now + 86400)
    state = tiktok.token_state(now=now)
    assert state["access_valid"] is True
    assert state["needs_refresh"] is False
    assert state["expires_in"] == 86400


def test_token_inside_skew_window_needs_refresh(monkeypatch):
    """A token with 60s left must refresh — a long upload would outlive it."""
    now = int(time.time())
    _set_tokens(monkeypatch, access="a", refresh="r", expires_at=now + 60)
    state = tiktok.token_state(now=now)
    assert state["needs_refresh"] is True
    assert state["access_valid"] is False


def test_token_without_recorded_expiry_needs_refresh(monkeypatch):
    """Tokens issued before expiry tracking cannot be proven live."""
    _set_tokens(monkeypatch, access="a", refresh="r", expires_at=None)
    state = tiktok.token_state()
    assert state["needs_refresh"] is True
    assert "no recorded expiry" in state["reason"]


def test_expired_refresh_token_is_not_usable(monkeypatch):
    now = int(time.time())
    _set_tokens(monkeypatch, access="a", refresh="r", expires_at=now - 1, refresh_expires_at=now - 1)
    assert tiktok.token_state(now=now)["refresh_valid"] is False


# ── refresh flow ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_valid_access_token_refreshes_when_expired(monkeypatch):
    now = int(time.time())
    _set_tokens(monkeypatch, access="stale", refresh="r", expires_at=now - 10)

    calls = []

    async def fake_refresh():
        calls.append(1)
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "fresh")
        monkeypatch.setenv("TIKTOK_TOKEN_EXPIRES_AT", str(int(time.time()) + 86400))
        return {"access_token": "fresh"}

    monkeypatch.setattr(tiktok, "refresh_access_token", fake_refresh)
    assert await tiktok.get_valid_access_token() == "fresh"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_valid_access_token_does_not_refresh_a_live_token(monkeypatch):
    now = int(time.time())
    _set_tokens(monkeypatch, access="live", refresh="r", expires_at=now + 86400)

    async def boom():
        raise AssertionError("refresh must not be called for a live token")

    monkeypatch.setattr(tiktok, "refresh_access_token", boom)
    assert await tiktok.get_valid_access_token() == "live"


@pytest.mark.asyncio
async def test_no_tokens_at_all_raises_auth_error(monkeypatch):
    _set_tokens(monkeypatch)
    with pytest.raises(tiktok.TikTokAuthError):
        await tiktok.get_valid_access_token()


@pytest.mark.asyncio
async def test_refresh_without_refresh_token_raises_auth_error(monkeypatch):
    _set_tokens(monkeypatch, access="a")
    with pytest.raises(tiktok.TikTokAuthError):
        await tiktok.refresh_access_token()


# ── upload guards ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_rejects_missing_file(monkeypatch, tmp_path):
    async def token():
        return "t"

    monkeypatch.setattr(tiktok, "get_valid_access_token", token)
    with pytest.raises(FileNotFoundError):
        await tiktok.upload_clip(str(tmp_path / "nope.mp4"), "title")


@pytest.mark.asyncio
async def test_upload_rejects_empty_file(monkeypatch, tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")

    async def token():
        return "t"

    monkeypatch.setattr(tiktok, "get_valid_access_token", token)
    with pytest.raises(RuntimeError, match="empty"):
        await tiktok.upload_clip(str(empty), "title")


@pytest.mark.asyncio
async def test_upload_rejects_clip_longer_than_account_cap(monkeypatch, tmp_path):
    clip = tmp_path / "long.mp4"
    clip.write_bytes(b"x" * 1024)

    async def token():
        return "t"

    async def creator_info():
        return {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 60}

    monkeypatch.setattr(tiktok, "get_valid_access_token", token)
    monkeypatch.setattr(tiktok, "query_creator_info", creator_info)

    with pytest.raises(RuntimeError, match="caps posts at 60s"):
        await tiktok.upload_clip(str(clip), "title", duration_sec=90)


@pytest.mark.asyncio
async def test_creator_info_failure_does_not_block_upload(monkeypatch, tmp_path):
    """creator_info is advisory — a transient failure must not kill the post."""
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x" * 16)

    async def token():
        return "t"

    async def creator_info():
        raise RuntimeError("temporarily unavailable")

    monkeypatch.setattr(tiktok, "get_valid_access_token", token)
    monkeypatch.setattr(tiktok, "query_creator_info", creator_info)

    notes = []
    # The upload proceeds past negotiation and only then fails on the network
    # call, which is what we want: the guard did not short-circuit it.
    with pytest.raises(Exception):
        await tiktok.upload_clip(
            str(clip), "title", progress_cb=notes.append, duration_sec=10
        )
    assert any("creator_info unavailable" in n for n in notes)


@pytest.mark.asyncio
async def test_auth_error_from_creator_info_propagates(monkeypatch, tmp_path):
    """An expired connection must surface as auth, not as a generic upload failure."""
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x" * 16)

    async def token():
        return "t"

    async def creator_info():
        raise tiktok.TikTokAuthError("re-authorize")

    monkeypatch.setattr(tiktok, "get_valid_access_token", token)
    monkeypatch.setattr(tiktok, "query_creator_info", creator_info)

    with pytest.raises(tiktok.TikTokAuthError):
        await tiktok.upload_clip(str(clip), "title")
