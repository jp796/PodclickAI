"""
Wave 3 (prerequisite) — the track record has to be true, because Wave 1 made it
the promotion gate.

Two defects this covers, both harmless until eligibility started reading these
numbers:

1. `execute_action` hardcoded `outcome="success"` for whatever `_dispatch_action`
   returned. An unknown action_type that fell through to `no_op`, or a skip for a
   missing precondition, counted toward the ladder exactly like a published post —
   so Brick could climb to GC on work he never did.

2. A raised dispatch wrote no track record at all, so `success_rate` was
   structurally 100%: a failure could not lower it.
"""

import pytest

from services.brick_agent import NON_WORK_STATUSES, BrickAgent, TIER_REQUIREMENTS


# ── the taxonomy ──────────────────────────────────────────────────────────────

def test_no_op_is_never_credited():
    """
    The unknown-action_type fall-through. Before Wave 3 this recorded a success,
    which meant a typo in a planning response could earn ladder progress.
    """
    assert "no_op" in NON_WORK_STATUSES


def test_skipped_preconditions_are_never_credited():
    """cut_clip with no rendered clip did no work — it is not a success."""
    assert "skipped" in NON_WORK_STATUSES


def test_reconnect_states_are_neither_success_nor_failure():
    """
    An expired TikTok or Gmail token is a human task, not Brick's mistake.
    Crediting it would inflate the ladder; penalising it would punish him for
    someone else's revoked OAuth grant.
    """
    for status in ("needs_tiktok", "needs_gmail", "needs_account"):
        assert status in NON_WORK_STATUSES


@pytest.mark.parametrize("status", ["posted", "draft", "delivered", "scheduled", "updated"])
def test_real_work_is_credited(status):
    assert status not in NON_WORK_STATUSES


def test_taxonomy_is_a_frozenset():
    """Shared module state — it must not be mutable by a caller."""
    assert isinstance(NON_WORK_STATUSES, frozenset)


# ── why it matters: the gate reads these numbers ──────────────────────────────

def _record(total, success, rate, days_clean=None):
    return {
        "total_actions": total, "success_count": success, "failure_count": 0,
        "rejected_count": 0, "success_rate": rate,
        "last_setback_at": None, "days_since_setback": days_clean,
    }


def test_crediting_no_ops_would_have_bought_a_tier():
    """
    Five no-ops used to be five 'successes' — exactly the bricklayer threshold.
    This is the concrete exploit the fix closes, stated as a test so the link
    between the taxonomy and the ladder is not just a comment.
    """
    req = TIER_REQUIREMENTS["bricklayer"]
    as_if_credited = _record(total=5, success=5, rate=100.0)
    assert BrickAgent._unmet_requirements(req, as_if_credited) == [], (
        "five credited no-ops would have satisfied bricklayer"
    )

    # With the fix, those five write no rows at all.
    honestly_recorded = _record(total=0, success=0, rate=0.0)
    assert BrickAgent._unmet_requirements(req, honestly_recorded) != []


def test_unrecorded_failures_would_have_kept_the_rate_at_100():
    """
    The second half: a dispatch that raised wrote nothing, so a run of failures
    left success_rate untouched at 100% and foreman stayed reachable.
    """
    req = TIER_REQUIREMENTS["foreman"]
    # 20 actions, 5 of which failed but were never recorded
    inflated = _record(total=15, success=15, rate=100.0, days_clean=30)
    assert BrickAgent._unmet_requirements(req, inflated) == []

    # Recorded honestly, the same history does not reach foreman.
    honest = _record(total=20, success=15, rate=75.0, days_clean=0)
    unmet = BrickAgent._unmet_requirements(req, honest)
    assert any("success rate" in u for u in unmet)


# ── the implementation actually branches on it ────────────────────────────────

def test_execute_action_consults_the_taxonomy():
    """
    Guard against the fix being reverted to a hardcoded outcome. The source must
    reference NON_WORK_STATUSES and must not reassert outcome="success"
    unconditionally.
    """
    import ast
    import inspect
    import textwrap

    import services.brick_agent as mod

    src = inspect.getsource(mod.BrickAgent.execute_action)
    assert "NON_WORK_STATUSES" in src, "execute_action no longer consults the taxonomy"
    # the failure path must exist
    assert 'outcome="failure"' in src, "execute_action no longer records failures"
    # and the success write must be conditional, not straight-line
    tree = ast.parse(textwrap.dedent(src))
    has_if = any(isinstance(n, ast.If) for n in ast.walk(tree))
    has_try = any(isinstance(n, ast.Try) for n in ast.walk(tree))
    assert has_if, "the track-record write is unconditional again"
    assert has_try, "the dispatch call is no longer wrapped for failure recording"


def test_execute_action_reraises_after_recording_a_failure():
    """The caller must still see the error — recording it is not swallowing it."""
    import inspect

    import services.brick_agent as mod

    src = inspect.getsource(mod.BrickAgent.execute_action)
    body = src[src.index("except Exception as dispatch_err"):]
    assert "raise" in body, "execute_action swallows dispatch failures"
