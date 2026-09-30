"""
The Vyral mix — bucket weights and the rotation they produce.

Extracted here because Wave 3 gave Brick two actions that touch it
(`adjust_vyral_mix` and `replan_calendar`) and `main.py` already had the rotation
inline as `_distribute_buckets`. Two copies of a pure function is how they drift,
so there is one.

`vyral_mix` is a flat dict of bucket -> weight stored on `blueprints.vyral_mix`
(JSONB). Nothing in the codebase enforced its shape before this module: the
consumer coerced weights to float and padded or truncated the pool to fit, so a
mix with an unknown bucket key produced Posts that violate the `posts.bucket`
CHECK constraint at insert time — a failure a long way from its cause. Brick can
now rewrite this table at GC tier, so validation stopped being optional.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Must stay in step with the posts.bucket CHECK constraint in db/models.py.
# A weight under any other key would mint Posts the database rejects.
VALID_BUCKETS = ("viral", "brand", "personal", "conversion", "podcast")

DEFAULT_VYRAL_MIX: Dict[str, float] = {
    "viral": 0.4,
    "brand": 0.3,
    "personal": 0.2,
    "conversion": 0.1,
}

# How far a mix may drift from summing to 1.0 before it is rejected. The SOW
# states 1.0 as the convention and `pillars` carries the same note; the rotation
# tolerates any positive weights, but a mix that does not sum to ~1 means the
# author was thinking in different units and the result will not be what they
# intended.
SUM_TOLERANCE = 0.02


def validate_vyral_mix(mix: Any) -> Tuple[bool, List[str]]:
    """
    Check a candidate mix. Returns (ok, problems) — problems is empty when ok.

    Deliberately returns rather than raises: the caller is a Brick dispatch branch
    that should report a bad proposal on the punch list, not crash.
    """
    problems: List[str] = []

    if not isinstance(mix, dict):
        return False, [f"vyral_mix must be an object of bucket -> weight, got {type(mix).__name__}"]
    if not mix:
        return False, ["vyral_mix is empty"]

    unknown = [k for k in mix if k not in VALID_BUCKETS]
    if unknown:
        problems.append(
            f"unknown bucket(s) {sorted(unknown)} — allowed: {list(VALID_BUCKETS)}"
        )

    numeric: Dict[str, float] = {}
    for key, value in mix.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"weight for {key!r} is not a number: {value!r}")
            continue
        if value < 0:
            problems.append(f"weight for {key!r} is negative: {value}")
            continue
        numeric[key] = float(value)

    if numeric and not any(v > 0 for v in numeric.values()):
        problems.append("every weight is zero — nothing would ever be scheduled")

    if numeric and not problems:
        total = sum(numeric.values())
        if abs(total - 1.0) > SUM_TOLERANCE:
            problems.append(
                f"weights sum to {round(total, 3)}, not 1.0 "
                f"(tolerance {SUM_TOLERANCE})"
            )

    return (not problems), problems


def normalize_vyral_mix(mix: Dict[str, Any]) -> Dict[str, float]:
    """Drop zero/absent buckets and coerce to float, preserving relative weight."""
    return {
        k: float(v) for k, v in mix.items()
        if k in VALID_BUCKETS and isinstance(v, (int, float))
        and not isinstance(v, bool) and float(v) > 0
    }


def distribute_buckets(vyral_mix: Dict[str, Any], slot_count: int) -> List[str]:
    """
    Turn weights into a concrete bucket sequence of exactly `slot_count` entries.

    Anti-clumping, stated precisely: no run of three of the same bucket **whenever
    the mix makes that possible** — that is, when no single bucket claims more than
    half the slots. Breaking N items of one bucket into runs of at most two needs
    ceil(N/2) - 1 separators, so an 80/20 mix over 20 slots (16 vs 4) cannot avoid
    long runs and the function degrades instead of dropping slots. Realistic mixes
    (the 0.4 default included) satisfy the condition comfortably.

    Pads with the heaviest bucket when rounding leaves the pool short and truncates
    when it overshoots, which is why the rotation tolerates weights that do not sum
    to 1 even though `validate_vyral_mix` asks for it.
    """
    if slot_count <= 0:
        return []

    usable = normalize_vyral_mix(vyral_mix) or dict(DEFAULT_VYRAL_MIX)

    pool: List[str] = []
    for bucket, weight in usable.items():
        pool.extend([bucket] * int(round(slot_count * weight)))

    heaviest = max(usable, key=lambda k: usable[k])
    while len(pool) < slot_count:
        pool.append(heaviest)
    pool = pool[:slot_count]

    by_bucket: Dict[str, int] = {}
    for bucket in pool:
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1

    # One placement per iteration, always the bucket with the most left that would
    # not create a run of three. Placing a whole sorted pass at a time (the obvious
    # first attempt) exhausts the minority buckets early and clumps the tail —
    # a 40% viral mix over 30 slots ended `… viral viral viral`.
    sequence: List[str] = []
    while len(sequence) < slot_count and any(v > 0 for v in by_bucket.values()):
        candidates = sorted(
            (b for b, n in by_bucket.items() if n > 0),
            key=lambda b: (-by_bucket[b], b),
        )
        pick = next(
            (
                b for b in candidates
                if not (len(sequence) >= 2 and sequence[-1] == sequence[-2] == b)
            ),
            None,
        )
        if pick is None:
            # Only a tripling would fit — genuinely infeasible for this mix (e.g.
            # a single bucket at 100%). Take the run rather than drop slots.
            pick = candidates[0]
        sequence.append(pick)
        by_bucket[pick] -= 1

    return sequence[:slot_count]
