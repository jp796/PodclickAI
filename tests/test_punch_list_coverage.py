"""
Every action Brick can propose must be reviewable before it is approved.

Twice now a wave added backend action types and left the punch list rendering
only Brick's one-line rationale for them — Wave 3 added eight, Wave 4 added twenty.
A reviewer clicking Approve on a GC-tier action with no inputs shown is approving
blind, so this is a build-failing check rather than a habit.
"""

import re
from pathlib import Path

import pytest

from services.brick_agent import ACTION_TIER_MAP, GENERATOR_ACTIONS

WALKTHROUGH = Path(__file__).resolve().parent.parent / "frontend" / "walkthrough.html"

# Two deliberate omissions, each with a reason:
#   guest_asset_package renders its own far richer preview (recipient, asset
#     manifest, Drive status, the editable email body)
#   send_guest_email carries no payload — it only points at the review route
EXEMPT = {"guest_asset_package", "send_guest_email"}


def _js_object_keys(name):
    html = WALKTHROUGH.read_text()
    m = re.search(r"const " + name + r" = \{(.*?)\n\};", html, re.S)
    assert m, f"{name} not found in walkthrough.html"
    # Keys are bare identifiers (foo_bar:) or quoted colon-bearing ids
    # ('agent_run:some-id':). Agent ids may carry digits/hyphens/underscores.
    pairs = re.findall(
        r"^\s*(?:(['\"])([a-z_]+:[a-z0-9_-]+)\1|([a-z_]+)):", m.group(1), re.M)
    return {quoted or bare for _q, quoted, bare in pairs}


def test_every_action_type_has_a_consequence_line():
    """
    What approving this will actually do, in the action's own terms.

    guest_asset_package is exempt here too: its dedicated preview carries a
    stronger statement than a one-liner could ("Nothing has been sent yet. Read it
    below, then hit Approve & Send - that's the only thing that emails the guest").
    """
    known = _js_object_keys("ACTION_CONSEQUENCE") | EXEMPT
    missing = sorted(a for a in ACTION_TIER_MAP if a not in known)
    assert not missing, (
        "no consequence line for: " + ", ".join(missing) +
        " — a reviewer would see only Brick's rationale"
    )


def test_every_action_type_has_preview_fields():
    known = _js_object_keys("ACTION_FIELDS") | EXEMPT
    missing = sorted(a for a in ACTION_TIER_MAP if a not in known)
    assert not missing, "no preview fields for: " + ", ".join(missing)


def test_the_exemptions_are_still_justified():
    """
    guest_asset_package must still have its dedicated branch, and
    send_guest_email must still be payload-free — otherwise the exemption is stale.
    """
    html = WALKTHROUGH.read_text()
    assert "action.action_type === 'guest_asset_package'" in html, (
        "guest_asset_package lost its dedicated preview — it is no longer exempt"
    )
    import inspect

    from services.brick_agent import BrickAgent

    src = inspect.getsource(BrickAgent._dispatch_send_guest_email)
    assert "needs_approve_send" in src, (
        "send_guest_email now does something with a payload — give it preview fields"
    )


def test_generators_that_spend_a_resource_warn_in_their_consequence():
    """
    yt_competitor_spy burns YouTube quota and yt_cover_forge generates images. A
    reviewer should see that before approving, not discover it on the bill.
    """
    html = WALKTHROUGH.read_text()
    m = re.search(r"const ACTION_CONSEQUENCE = \{(.*?)\n\};", html, re.S)
    block = m.group(1)
    for action, spec in GENERATOR_ACTIONS.items():
        if not spec.get("cost"):
            continue
        entry = re.search(re.escape(action) + r":\s*'([^']*)'", block)
        assert entry, f"{action} has no consequence entry"
        assert "\\u26a0" in entry.group(1) or "⚠" in entry.group(1), (
            f"{action} spends {spec['cost']} but its consequence line carries no warning"
        )


def test_no_generator_consequence_claims_anything_is_saved():
    """Generators persist nothing — the copy must not imply otherwise."""
    html = WALKTHROUGH.read_text()
    m = re.search(r"const ACTION_CONSEQUENCE = \{(.*?)\n\};", html, re.S)
    block = m.group(1)
    for action in GENERATOR_ACTIONS:
        entry = re.search(re.escape(action) + r":\s*'([^']*)'", block)
        assert entry, f"{action} has no consequence entry"
        assert "Nothing is saved or published" in entry.group(1), (
            f"{action} does not state that nothing is persisted"
        )
