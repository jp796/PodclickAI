"""
Wave 1 — the trust ladder actually costs something.

Before this, `promote()` was a bare one-step increment: three clicks took Brick
from Owner-Builder to GC with zero completed work behind him. The ladder had a
label and a ceremony modal and no trust mechanism. These tests pin the gate.

The aggregation and the gate both live in BrickAgent, so `/permit` and
`/eligibility` cannot disagree about the numbers — that shared path is asserted
here too.
"""

import pytest

from services.brick_agent import (
    ACTION_TIER_MAP,
    TIER_ORDER,
    TIER_REQUIREMENTS,
    BrickAgent,
)


def record(total=0, success=0, failure=0, rejected=0, rate=0.0, days_clean=None):
    return {
        "total_actions": total,
        "success_count": success,
        "failure_count": failure,
        "rejected_count": rejected,
        "success_rate": rate,
        "last_setback_at": None,
        "days_since_setback": days_clean,
    }


# ── the ladder's shape ────────────────────────────────────────────────────────

def test_every_tier_has_requirements():
    for tier in TIER_ORDER:
        assert tier in TIER_REQUIREMENTS, f"{tier} has no requirements entry"
        req = TIER_REQUIREMENTS[tier]
        for key in ("min_actions", "min_success_rate", "clean_days", "rationale"):
            assert key in req, f"{tier} missing {key}"
        assert req["rationale"], f"{tier} has an empty rationale"


def test_requirements_never_loosen_going_up():
    """A higher tier must never be cheaper than a lower one."""
    prev = None
    for tier in TIER_ORDER:
        req = TIER_REQUIREMENTS[tier]
        if prev is not None:
            assert req["min_actions"] >= prev["min_actions"], f"{tier} needs fewer actions"
            assert req["min_success_rate"] >= prev["min_success_rate"], f"{tier} needs a lower rate"
            assert req["clean_days"] >= prev["clean_days"], f"{tier} needs fewer clean days"
        prev = req


def test_draftsman_is_free():
    """
    Draftsman only suggests and drafts. Gating it would deadlock the ladder —
    Brick cannot build a track record without being allowed to act at all.
    """
    req = TIER_REQUIREMENTS["draftsman"]
    assert req["min_actions"] == 0
    assert req["min_success_rate"] == 0.0
    assert BrickAgent._unmet_requirements(req, record()) == []


def test_owner_builder_is_always_reachable():
    """The floor is how you take autonomy away — it can never be gated."""
    assert BrickAgent._unmet_requirements(TIER_REQUIREMENTS["owner_builder"], record()) == []


def test_audience_reaching_tiers_require_a_recency_window():
    """
    Foreman is the first tier that reaches an audience; GC spends the user's name.
    Neither should be handed over days after a failure.
    """
    assert TIER_REQUIREMENTS["foreman"]["clean_days"] > 0
    assert TIER_REQUIREMENTS["gc"]["clean_days"] > TIER_REQUIREMENTS["foreman"]["clean_days"]
    for tier in ("draftsman", "bricklayer"):
        assert TIER_REQUIREMENTS[tier]["clean_days"] == 0


# ── the gate ──────────────────────────────────────────────────────────────────

def test_zero_record_cannot_reach_bricklayer():
    unmet = BrickAgent._unmet_requirements(TIER_REQUIREMENTS["bricklayer"], record())
    assert unmet
    assert "5 more completed actions" in unmet[0]


def test_enough_clean_actions_reaches_bricklayer():
    assert BrickAgent._unmet_requirements(
        TIER_REQUIREMENTS["bricklayer"], record(total=5, success=5, rate=100.0)
    ) == []


def test_action_count_met_but_success_rate_short():
    unmet = BrickAgent._unmet_requirements(
        TIER_REQUIREMENTS["bricklayer"], record(total=10, success=7, rejected=3, rate=70.0)
    )
    assert len(unmet) == 1
    assert "success rate 70.0% is below 80.0%" in unmet[0]


def test_recent_setback_blocks_foreman_even_with_the_numbers():
    unmet = BrickAgent._unmet_requirements(
        TIER_REQUIREMENTS["foreman"],
        record(total=30, success=28, failure=2, rate=93.3, days_clean=2),
    )
    assert len(unmet) == 1
    assert "5 more clean days" in unmet[0]


def test_clean_window_elapsed_unblocks_foreman():
    assert BrickAgent._unmet_requirements(
        TIER_REQUIREMENTS["foreman"],
        record(total=30, success=28, failure=2, rate=93.3, days_clean=9),
    ) == []


def test_never_any_setback_satisfies_the_recency_gate():
    """days_since_setback is None when nothing has ever gone wrong."""
    assert BrickAgent._unmet_requirements(
        TIER_REQUIREMENTS["gc"],
        record(total=50, success=50, rate=100.0, days_clean=None),
    ) == []


def test_multiple_shortfalls_are_all_reported():
    """The permit screen shows WHY — one reason at a time would be a worse UI."""
    unmet = BrickAgent._unmet_requirements(
        TIER_REQUIREMENTS["gc"],
        record(total=10, success=6, failure=4, rate=60.0, days_clean=1),
    )
    assert len(unmet) == 3


def test_singular_plural_reads_correctly():
    one_short = BrickAgent._unmet_requirements(
        TIER_REQUIREMENTS["bricklayer"], record(total=4, success=4, rate=100.0)
    )
    assert "1 more completed action (" in one_short[0]


def test_success_rate_is_not_checked_before_any_actions_exist():
    """
    A brand-new location has rate 0.0 by definition; reporting that as a rate
    failure on top of the action shortfall would be noise.
    """
    unmet = BrickAgent._unmet_requirements(TIER_REQUIREMENTS["gc"], record())
    assert len(unmet) == 1
    assert "completed action" in unmet[0]


# ── the gate matches what the actions actually cost ───────────────────────────

def test_every_action_tier_appears_in_the_ladder():
    """An action gated on a tier the ladder cannot express would be unreachable."""
    for action, tier in ACTION_TIER_MAP.items():
        assert tier in TIER_REQUIREMENTS, f"{action} requires {tier!r}, absent from the ladder"


def test_the_ladder_and_the_action_map_agree_on_ordering():
    """
    cut_clip and publish_post are foreman; send_guest_email and pitch_sponsor are
    gc. The ladder must make gc strictly harder than foreman or the tier split in
    ACTION_TIER_MAP means nothing.
    """
    foreman = TIER_REQUIREMENTS["foreman"]
    gc = TIER_REQUIREMENTS["gc"]
    assert (gc["min_actions"], gc["min_success_rate"], gc["clean_days"]) > (
        foreman["min_actions"], foreman["min_success_rate"], foreman["clean_days"]
    )


# ── the shared aggregation ────────────────────────────────────────────────────

def test_track_record_and_eligibility_are_one_code_path():
    """
    /permit and /eligibility both call BrickAgent.track_record(), so the permit
    screen can never show numbers the gate disagrees with. Pin that they exist as
    methods on the agent rather than being reimplemented per route.
    """
    assert callable(getattr(BrickAgent, "track_record", None))
    assert callable(getattr(BrickAgent, "eligibility", None))
    assert callable(getattr(BrickAgent, "_unmet_requirements", None))


def test_promote_accepts_an_override_flag():
    """
    An operator must be able to overrule a wrong gate — but deliberately, and the
    implementation records it to the audit trail rather than doing it silently.
    """
    import inspect

    sig = inspect.signature(BrickAgent.promote)
    assert "override" in sig.parameters
    assert sig.parameters["override"].default is False


def test_demote_is_not_gated():
    """Taking autonomy away must never require earning anything."""
    import inspect

    assert "override" not in inspect.signature(BrickAgent.demote).parameters


# ── the startup gate (Wave 1, piece E) ────────────────────────────────────────
#
# Brick's crons lived inside a block that returned early for any non-local
# deployment mode, so the planning loop had never run anywhere but a laptop and
# expire_stale_actions() never fired in deployment either. The decision is now a
# pure function so both failure directions are pinned: "nothing runs overnight"
# and "a locked deployment starts doing work."

def _decide(mode="locked", owner=False, disabled=False):
    from main import owner_automation_decision

    return owner_automation_decision(
        deployment_mode=mode, owner_automation=owner, automation_disabled=disabled
    )


def test_local_mode_runs_automation_as_it_always_did():
    run, why = _decide(mode="local")
    assert run is True
    assert "Local mode" in why


def test_locked_deployment_stays_locked_by_default():
    """The fail-closed default must be unchanged by this wave."""
    run, why = _decide(mode="locked", owner=False)
    assert run is False
    assert "PODCLICK_OWNER_AUTOMATION=1" in why


def test_deployed_install_runs_automation_when_opted_in():
    run, why = _decide(mode="production", owner=True)
    assert run is True
    assert "ENABLED in deployed mode" in why
    # The reason must say this does not touch the perimeter — that is the whole
    # argument for why opting in is safe.
    assert "perimeter is unchanged" in why


def test_automation_disabled_beats_everything():
    """Previews and QA must never schedule real work, however else it is configured."""
    for mode in ("local", "production", "locked"):
        for owner in (True, False):
            run, why = _decide(mode=mode, owner=owner, disabled=True)
            assert run is False, f"{mode}/{owner} scheduled work despite the kill switch"
            assert "PODCLICK_AUTOMATION_DISABLED" in why


def test_owner_flag_alone_does_not_unlock_a_disabled_run():
    assert _decide(mode="production", owner=True, disabled=True)[0] is False


def test_unknown_mode_is_treated_as_locked():
    """An unrecognised deployment mode must fail closed, not fall through."""
    run, _ = _decide(mode="", owner=False)
    assert run is False
    run, _ = _decide(mode="something-new", owner=False)
    assert run is False


def test_every_decision_returns_a_reason():
    """A silent gate is how this bug survived for months — the reason is logged."""
    for mode in ("local", "locked", "production"):
        for owner in (True, False):
            for disabled in (True, False):
                run, why = _decide(mode=mode, owner=owner, disabled=disabled)
                assert isinstance(run, bool)
                assert why and isinstance(why, str)
