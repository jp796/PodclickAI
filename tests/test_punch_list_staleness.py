"""Stale auto-suggestions must not pile up on the punch list (found 2026-10-04: 20 items, 9-14 days old)."""
from datetime import datetime, timedelta

import pytest

from services.brick_agent import (
    GREETING_MAX_AGE_HOURS, STALE_SUGGESTION_HOURS, STALE_SUGGESTION_TYPES,
    greeting_is_fresh, is_stale_suggestion,
)

NOW = datetime(2026, 10, 4, 12, 0, 0)


def ago(hours):
    return NOW - timedelta(hours=hours)


@pytest.mark.parametrize("action_type", STALE_SUGGESTION_TYPES)
def test_old_suggestions_are_stale(action_type):
    assert is_stale_suggestion(action_type, ago(STALE_SUGGESTION_HOURS + 1), NOW)
    assert is_stale_suggestion(action_type, ago(24 * 14), NOW)


@pytest.mark.parametrize("action_type", STALE_SUGGESTION_TYPES)
def test_fresh_suggestions_are_kept(action_type):
    assert not is_stale_suggestion(action_type, ago(1), NOW)
    assert not is_stale_suggestion(action_type, ago(STALE_SUGGESTION_HOURS - 1), NOW)


@pytest.mark.parametrize("action_type", [
    "guest_asset_package", "agent_commit", "agent_run:voiceover", "send_guest_email", "publish_post",
])
def test_approvals_a_person_must_decide_never_age_out(action_type):
    # A guest email waiting two weeks for a yes must still be on the list.
    assert not is_stale_suggestion(action_type, ago(24 * 14), NOW)


def test_missing_timestamp_is_not_stale():
    assert not is_stale_suggestion("draft_post", None, NOW)


def test_timezone_aware_timestamps_compare_correctly():
    from datetime import timezone
    aware = (NOW - timedelta(hours=100)).replace(tzinfo=timezone.utc)
    assert is_stale_suggestion("draft_post", aware, NOW)


def test_walkthrough_filters_by_the_same_rule():
    """The endpoint must apply is_stale_suggestion, not a private copy of the rule."""
    from pathlib import Path
    src = Path("main.py").read_text()
    assert "is_stale_suggestion as _is_stale" in src
    assert "expire_stale_suggestions as _expire_stale" in src


def test_greeting_freshness():
    assert greeting_is_fresh(ago(1), NOW)
    assert greeting_is_fresh(ago(GREETING_MAX_AGE_HOURS - 1), NOW)
    assert not greeting_is_fresh(ago(GREETING_MAX_AGE_HOURS + 1), NOW)
    assert not greeting_is_fresh(ago(24 * 9), NOW)   # a Sep 25 'Friday' greeting on Oct 4
    assert not greeting_is_fresh(None, NOW)


def test_walkthrough_uses_greeting_rule():
    from pathlib import Path
    assert "greeting_is_fresh as _greeting_fresh" in Path("main.py").read_text()
