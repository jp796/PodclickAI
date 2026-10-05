"""Static contract for frontend/calendar.html (the Content Board).

The page is plain HTML + inline JS, so these checks guard what a redesign is most likely
to break silently: the element IDs and functions other code and users depend on, the
endpoints the page must call, the design-system rules (tokens not hex, icons not emoji),
and the construction vocabulary.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PAGE = Path("frontend/calendar.html")


@pytest.fixture(scope="module")
def html():
    return PAGE.read_text(encoding="utf-8")


def _strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"<!--.*?-->", "", src, flags=re.S)


def test_page_is_served():
    import main
    r = TestClient(main.studio_app).get("/calendar")
    assert r.status_code == 200
    assert 'id="view-month"' in r.text


# Hex colours are banned outside comments. `&#123;`-style entities are not colours.
HEX = re.compile(r"(?<![&\w])#[0-9a-fA-F]{3,8}\b")


def test_no_hex_colours(html):
    found = HEX.findall(_strip_comments(html))
    assert not found, f"use design tokens, not hex: {found}"


# Pictographic emoji plus the arrow/dingbat ranges the icon safety net would rewrite.
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿←-⇿⬀-⯿️]")


def test_no_emoji_in_markup(html):
    found = EMOJI.findall(html)
    assert not found, f"use pc-i icons, not emoji: {found}"


REQUIRED_IDS = [
    # header + views
    "cal-title", "nav-prev", "nav-today", "nav-next", "vt-month", "vt-week", "vt-list",
    "view-month", "view-week", "view-list", "week-grid", "week-label",
    # bucket filter chips (old stat-* counter ids are kept)
    "stats-strip", "stat-viral", "stat-brand", "stat-personal", "stat-conversion", "stat-podcast",
    # states
    "cal-skeleton", "empty-state", "error-state",
    # new draft
    "draft-modal", "draft-content", "draft-date", "draft-platform",
    # auto-plan
    "autoplan-modal", "autoplan-loading", "autoplan-actions", "autoplan-status-text",
    # brief
    "brief-modal", "brief-inputs", "brief-topic", "brief-length", "brief-add-board", "brief-loading",
    "brief-result", "brief-meta", "brief-title", "brief-script", "brief-gen-btn", "brief-film-btn",
    "brief-copy-btn", "brief-regen-btn",
    # post detail
    "publish-modal", "modal-date-bucket", "modal-base-caption", "post-timeline", "variant-tab-bar",
    "variant-loading", "variant-caption", "instagram-first-comment", "variant-first-comment",
    "btn-generate-variants", "btn-ship-it", "btn-delete-post", "modal-result", "toast",
]


@pytest.mark.parametrize("element_id", REQUIRED_IDS)
def test_required_ids_present(html, element_id):
    assert f'id="{element_id}"' in html


REQUIRED_FUNCTIONS = [
    "setViewMode", "renderView", "loadCalendar30", "loadGHLAccounts", "updateStatsStrip",
    "renderMonth", "renderWeek", "changeWeek", "renderList", "quickDelete", "quickPublish",
    "onDragStart", "onDragOver", "onDragLeave", "onDrop", "navStep", "navToday", "toggleBucket",
    "openDayInList", "openNewDraft", "submitNewDraft",
    "openAutoPlanModal", "closeAutoPlanModal", "runAutoPlan",
    "openBriefModal", "closeBriefModal", "runDailyBrief", "filmBriefNow", "copyBrief",
    "openPostModal", "closePostModal", "switchVariantTab", "generateVariants", "shipIt",
    "showModalResult", "deletePost",
]


@pytest.mark.parametrize("fn", REQUIRED_FUNCTIONS)
def test_required_functions_defined(html, fn):
    assert re.search(rf"\b(async\s+)?function\s+{fn}\s*\(", html), fn


@pytest.mark.parametrize("fragment", [
    "'/api/calendar?from_date=' + ymd(r.start) + '&to_date=' + ymd(r.end)",  # month navigation
    "'/api/calendar/posts', {",                                                  # New draft POST
    "method: 'PATCH'",                                                           # drag-to-reschedule
    "'/api/calendar/posts/' + movingId",
    "'/api/calendar/auto-plan'",
    "'/variants/generate'",
    "'/publish'",
    "method: 'DELETE'",
    "'/api/studio/re-daily-brief'",
])
def test_calls_the_real_endpoints(html, fragment):
    assert fragment in html


def test_every_cell_view_is_a_drop_target(html):
    # month cells, phone agenda days and week columns all share dropAttrs()
    assert 'ondrop="onDrop(event)"' in html
    assert html.count("${dropAttrs()}") >= 3


def test_ai_text_keeps_its_emoji(html):
    for tid in ("variant-caption", "brief-script", "modal-base-caption"):
        tag = re.search(rf'<[^>]*id="{tid}"[^>]*>', html).group(0)
        assert "data-ai-output" in tag and "data-keep-emoji" in tag, tid


def test_today_and_motion_accessibility(html):
    assert 'aria-current="date"' in html
    assert "prefers-reduced-motion" in html
    assert 'aria-label="Previous month"' in html


BANNED = re.compile(r"\b(dashboard|settings|workflow|leverage|unlock|synergy|ai-powered)\b", re.I)


def test_construction_vocabulary(html):
    found = BANNED.findall(html)
    assert not found, f"banned words: {found}"
