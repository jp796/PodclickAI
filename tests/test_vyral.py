"""
Wave 3 — the Vyral mix: validation and rotation.

`vyral_mix` had no validator and no writer before this. Brick can now rewrite it
at GC tier, and the consumer (`_distribute_buckets`) only coerced weights to float
— so a mix with an unknown bucket key minted Posts that violate the posts.bucket
CHECK constraint at auto-plan time, a failure a long way from its cause.
"""

from collections import Counter
from itertools import groupby

import pytest

from services.vyral import (
    DEFAULT_VYRAL_MIX,
    VALID_BUCKETS,
    distribute_buckets,
    normalize_vyral_mix,
    validate_vyral_mix,
)


def longest_run(seq):
    return max((len(list(g)) for _, g in groupby(seq)), default=0)


# ── validation ────────────────────────────────────────────────────────────────

def test_the_default_mix_is_valid():
    ok, problems = validate_vyral_mix(DEFAULT_VYRAL_MIX)
    assert ok, problems


def test_valid_buckets_match_the_post_check_constraint():
    """
    If these drift, a mix the validator accepts produces Posts the database
    rejects. Read the constraint rather than trusting a copied list.
    """
    from db.models import Post

    constraint = next(
        str(c.sqltext) for c in Post.__table__.constraints
        if "bucket IN" in str(getattr(c, "sqltext", ""))
    )
    for bucket in VALID_BUCKETS:
        assert f"'{bucket}'" in constraint, f"{bucket} is not in the posts.bucket CHECK"


def test_unknown_bucket_is_rejected():
    ok, problems = validate_vyral_mix({"viral": 0.5, "memes": 0.5})
    assert not ok
    assert any("unknown bucket" in p for p in problems)


def test_weights_must_sum_to_about_one():
    ok, problems = validate_vyral_mix({"viral": 1.0, "brand": 1.0})
    assert not ok
    assert any("sum" in p for p in problems)


def test_small_rounding_drift_is_tolerated():
    ok, _ = validate_vyral_mix({"viral": 0.33, "brand": 0.33, "personal": 0.34})
    assert ok


def test_negative_weight_is_rejected():
    ok, problems = validate_vyral_mix({"viral": -0.5, "brand": 1.5})
    assert not ok
    assert any("negative" in p for p in problems)


def test_all_zero_is_rejected():
    ok, problems = validate_vyral_mix({"viral": 0.0, "brand": 0.0})
    assert not ok
    assert any("nothing would ever be scheduled" in p for p in problems)


@pytest.mark.parametrize("bad", [None, [], "viral", 42, {}])
def test_non_mapping_input_is_rejected(bad):
    ok, problems = validate_vyral_mix(bad)
    assert not ok
    assert problems


def test_booleans_are_not_accepted_as_weights():
    """True == 1 in Python — a bool here means the caller passed the wrong thing."""
    ok, problems = validate_vyral_mix({"viral": True})
    assert not ok
    assert any("not a number" in p for p in problems)


def test_normalize_drops_zero_and_unknown():
    out = normalize_vyral_mix({"viral": 0.5, "brand": 0.0, "memes": 0.5, "personal": 0.5})
    assert out == {"viral": 0.5, "personal": 0.5}


# ── rotation ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [1, 2, 7, 14, 30, 60])
def test_rotation_returns_exactly_the_slots_asked_for(n):
    assert len(distribute_buckets(DEFAULT_VYRAL_MIX, n)) == n


def test_rotation_of_zero_or_negative_slots_is_empty():
    assert distribute_buckets(DEFAULT_VYRAL_MIX, 0) == []
    assert distribute_buckets(DEFAULT_VYRAL_MIX, -5) == []


def test_rotation_respects_the_proportions():
    seq = distribute_buckets(DEFAULT_VYRAL_MIX, 30)
    counts = Counter(seq)
    assert counts["viral"] == 12
    assert counts["brand"] == 9
    assert counts["personal"] == 6
    assert counts["conversion"] == 3


def test_no_run_of_three_when_the_mix_allows_it():
    """
    The real guarantee. Breaking N of one bucket into runs of <=2 needs
    ceil(N/2)-1 separators, so it holds whenever no bucket claims more than half
    the slots — which every realistic mix satisfies.
    """
    for mix, n in [
        (DEFAULT_VYRAL_MIX, 30),
        (DEFAULT_VYRAL_MIX, 7),
        ({"viral": 0.25, "brand": 0.25, "personal": 0.25, "conversion": 0.25}, 40),
        ({"viral": 0.3, "brand": 0.3, "personal": 0.2, "conversion": 0.1, "podcast": 0.1}, 30),
        ({"viral": 0.5, "brand": 0.5}, 20),
    ]:
        seq = distribute_buckets(mix, n)
        assert longest_run(seq) <= 2, f"{mix} at {n} slots clumped: {seq}"


def test_the_old_tail_clumping_bug_stays_fixed():
    """
    A 40% viral mix over 30 slots used to end `... viral viral viral`: placing a
    whole sorted pass at a time exhausted the minority buckets early.
    """
    seq = distribute_buckets(DEFAULT_VYRAL_MIX, 30)
    assert seq[-3:] != ["viral", "viral", "viral"]
    assert longest_run(seq) <= 2


def test_an_infeasible_mix_degrades_instead_of_dropping_slots():
    """16 of one bucket in 20 slots cannot avoid long runs — but must still fill 20."""
    seq = distribute_buckets({"viral": 0.8, "brand": 0.2}, 20)
    assert len(seq) == 20
    assert Counter(seq)["viral"] == 16


def test_single_bucket_mix_is_allowed_by_the_rotation():
    seq = distribute_buckets({"viral": 1.0}, 5)
    assert seq == ["viral"] * 5


def test_empty_mix_falls_back_to_the_default():
    seq = distribute_buckets({}, 10)
    assert len(seq) == 10
    assert set(seq) <= set(VALID_BUCKETS)


def test_rotation_only_ever_emits_valid_buckets():
    for mix in [DEFAULT_VYRAL_MIX, {"viral": 1.0}, {}, {"memes": 1.0}]:
        for bucket in distribute_buckets(mix, 25):
            assert bucket in VALID_BUCKETS


def test_main_delegates_to_this_module():
    """
    The rotation lived inline in main.py too. Two copies of a pure function is how
    they drift — and the inline one had the tail-clumping bug.
    """
    import inspect

    import main

    assert "from services.vyral import distribute_buckets" in inspect.getsource(
        main._distribute_buckets
    )
