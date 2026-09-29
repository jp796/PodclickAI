"""
Wave 0 — the permit gate is Brick's entire safety story, so it gets tested.

`services/brick_agent.py` had zero tests before Wave 2.5. The tier comparison is
what stands between "Brick suggests a post" and "Brick publishes to your audience",
and it is enforced twice: once when planning proposes an action, once when
`execute_action` runs an approved one. These tests cover the comparison itself and
the integrity of the action→tier table it reads.
"""

import pytest

from services.brick_agent import (
    ACTION_TIER_MAP,
    TIER_ORDER,
    _tier_allows,
    _tier_rank,
)


# ── the ladder ────────────────────────────────────────────────────────────────

def test_ladder_order_is_the_documented_one():
    assert TIER_ORDER == [
        "owner_builder",
        "draftsman",
        "bricklayer",
        "foreman",
        "gc",
    ]


def test_rank_increases_with_autonomy():
    ranks = [_tier_rank(t) for t in TIER_ORDER]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(TIER_ORDER)


@pytest.mark.parametrize("tier", TIER_ORDER)
def test_every_tier_allows_itself(tier):
    assert _tier_allows(tier, tier) is True


def test_higher_tier_allows_lower_work():
    assert _tier_allows("gc", "draftsman") is True
    assert _tier_allows("foreman", "bricklayer") is True


def test_lower_tier_is_refused_higher_work():
    assert _tier_allows("draftsman", "foreman") is False
    assert _tier_allows("owner_builder", "draftsman") is False
    assert _tier_allows("foreman", "gc") is False


def test_owner_builder_can_do_nothing_above_itself():
    for tier in TIER_ORDER[1:]:
        assert _tier_allows("owner_builder", tier) is False


def test_gc_can_do_everything():
    for tier in TIER_ORDER:
        assert _tier_allows("gc", tier) is True


# ── fail-closed behavior ──────────────────────────────────────────────────────

def test_unknown_current_tier_fails_closed():
    """A corrupt permit row must not grant autonomy."""
    assert _tier_allows("superuser", "draftsman") is False
    assert _tier_allows("", "draftsman") is False
    assert _tier_allows(None, "draftsman") is False


def test_unknown_required_tier_is_refused_at_every_level(caplog):
    """
    The inversion this guards against: _tier_rank maps anything unknown to 0, so a
    typo'd requirement would otherwise be permitted at *every* level — turning the
    strictest possible gate into no gate.
    """
    with caplog.at_level("ERROR"):
        for tier in TIER_ORDER:
            assert _tier_allows(tier, "architekt") is False
    assert "Unknown required tier" in caplog.text


def test_empty_required_tier_is_refused():
    assert _tier_allows("gc", "") is False


# ── the action table ──────────────────────────────────────────────────────────

def test_every_action_maps_to_a_real_tier():
    """A new action with a bogus tier would be silently ungated without this."""
    for action, tier in ACTION_TIER_MAP.items():
        assert tier in TIER_ORDER, f"{action} maps to unknown tier {tier!r}"


def test_no_action_requires_owner_builder():
    """owner_builder is the 'Brick does nothing' floor, not a work tier."""
    assert "owner_builder" not in ACTION_TIER_MAP.values()


def test_publishing_actions_are_foreman_or_above():
    """Anything that reaches an audience must not be a draftsman-tier action."""
    for action in ("publish_post", "send_guest_email"):
        assert _tier_allows("bricklayer", ACTION_TIER_MAP[action]) is False


def test_drafting_actions_are_available_at_draftsman():
    for action in ("draft_post", "suggest_post_idea"):
        assert _tier_allows("draftsman", ACTION_TIER_MAP[action]) is True


def test_cut_clip_requires_foreman():
    """Wave 2.5 posts clips to TikTok under this gate — pin it."""
    assert ACTION_TIER_MAP["cut_clip"] == "foreman"
    assert _tier_allows("bricklayer", "foreman") is False
    assert _tier_allows("foreman", "foreman") is True


def test_money_and_strategy_actions_are_gc_only():
    for action in ("pitch_sponsor", "adjust_vyral_mix", "replan_calendar"):
        assert ACTION_TIER_MAP[action] == "gc"
        assert _tier_allows("foreman", ACTION_TIER_MAP[action]) is False
