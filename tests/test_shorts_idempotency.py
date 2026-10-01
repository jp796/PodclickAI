"""
distribute-shorts idempotency — pressing "Send to Instagram + TikTok" twice.

The route had no dedup at all: it sorted the rendered clips by virality, sliced
the top N and drafted every one. A second press produced a second full set of GHL
drafts, indistinguishable from the first, and nothing in the response said so.

These tests are behavioural — they assert what the guard DOES, not that a given
line of source exists. A grep-shaped test for this guard passed earlier in this
build while the guard was disabled, which is why.
"""

import pytest

from services.shorts import SKIP_REASON, merge_distributed, partition_drafted


# ── partition_drafted ─────────────────────────────────────────────────────────

def test_nothing_drafted_yet_lets_every_clip_through():
    pending, skipped = partition_drafted(["a", "b", "c"], ["instagram", "tiktok"], {})
    assert pending == ["a", "b", "c"]
    assert skipped == []


def test_fully_drafted_clip_is_skipped_with_a_reason():
    distributed = {"a": ["instagram", "tiktok"]}
    pending, skipped = partition_drafted(["a", "b"], ["instagram", "tiktok"], distributed)
    assert pending == ["b"]
    assert len(skipped) == 1
    assert skipped[0]["clip_id"] == "a"
    assert skipped[0]["reason"] == SKIP_REASON
    assert "force:true" in skipped[0]["reason"], "the skip must say how to override it"


def test_partially_drafted_clip_still_goes_through():
    """An Instagram-only clip must still be draftable to TikTok."""
    pending, skipped = partition_drafted(["a"], ["instagram", "tiktok"], {"a": ["instagram"]})
    assert pending == ["a"]
    assert skipped == []


def test_asking_for_a_subset_of_what_is_done_is_skipped():
    pending, skipped = partition_drafted(["a"], ["tiktok"], {"a": ["instagram", "tiktok"]})
    assert pending == []
    assert len(skipped) == 1


def test_platform_comparison_ignores_case():
    pending, _ = partition_drafted(["a"], ["TikTok", "Instagram"], {"a": ["tiktok", "instagram"]})
    assert pending == [], "case differences must not defeat the guard"


def test_virality_order_is_preserved():
    """The route sorts by score before partitioning; the guard must not reshuffle."""
    pending, _ = partition_drafted(["hi", "mid", "lo"], ["tiktok"], {"mid": ["tiktok"]})
    assert pending == ["hi", "lo"]


def test_unknown_clip_ids_are_not_treated_as_drafted():
    pending, skipped = partition_drafted(["new"], ["tiktok"], {"old": ["tiktok"]})
    assert pending == ["new"]
    assert skipped == []


def test_empty_platform_request_skips_nothing():
    """A caller asking for no platforms must not silently mark everything done."""
    pending, skipped = partition_drafted(["a"], [], {"a": ["instagram"]})
    assert pending == ["a"]
    assert skipped == []


# ── merge_distributed ─────────────────────────────────────────────────────────

def test_merge_records_each_created_draft():
    merged = merge_distributed({}, [
        {"clip_id": "a", "platform": "instagram"},
        {"clip_id": "a", "platform": "tiktok"},
        {"clip_id": "b", "platform": "tiktok"},
    ])
    assert merged == {"a": ["instagram", "tiktok"], "b": ["tiktok"]}


def test_merge_preserves_platforms_from_earlier_runs():
    merged = merge_distributed({"a": ["instagram"]}, [{"clip_id": "a", "platform": "tiktok"}])
    assert merged["a"] == ["instagram", "tiktok"], "a later run must not erase the first"


def test_merge_returns_a_new_object():
    """JSONB does not persist when mutated in place — the caller needs a fresh dict."""
    original = {"a": ["instagram"]}
    merged = merge_distributed(original, [{"clip_id": "a", "platform": "tiktok"}])
    assert merged is not original
    assert original == {"a": ["instagram"]}, "the stored value must not be mutated"


def test_merge_ignores_entries_missing_a_clip_or_platform():
    merged = merge_distributed({}, [
        {"clip_id": "a"},
        {"platform": "tiktok"},
        {"clip_id": "b", "platform": "tiktok"},
    ])
    assert merged == {"b": ["tiktok"]}


def test_merge_is_idempotent():
    created = [{"clip_id": "a", "platform": "tiktok"}]
    once = merge_distributed({}, created)
    twice = merge_distributed(once, created)
    assert once == twice


# ── the round trip the route performs ─────────────────────────────────────────

def test_second_run_after_recording_drafts_nothing():
    """The whole point: draft, record, run again -> zero clips selected."""
    clips = ["a", "b", "c"]
    platforms = ["instagram", "tiktok"]

    first, _ = partition_drafted(clips, platforms, {})
    assert first == clips
    created = [{"clip_id": c, "platform": p} for c in first for p in platforms]
    record = merge_distributed({}, created)

    second, skipped = partition_drafted(clips, platforms, record)
    assert second == [], "a repeat press must draft nothing"
    assert len(skipped) == 3


def test_route_delegates_to_the_shared_helpers():
    """Guard against the route growing its own private copy of this logic again."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "main.py").read_text()
    body = src.split('@app.post("/api/projects/{project_id}/distribute-shorts")')[1]
    body = body.split("\n@app.")[0]
    assert "partition_drafted" in body
    assert "merge_distributed" in body
