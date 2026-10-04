"""The Crew page: /agents serves the real frontend/agents.html with its key markers."""
import re
from pathlib import Path

import pytest

from tests.test_agents_routes import client, harness  # noqa: F401  (fixtures)

PAGE = Path(__file__).resolve().parent.parent / "frontend" / "agents.html"


async def test_agents_route_serves_crew_page(harness, client):
    r = await client.get("/agents")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    for marker in ("The Crew", "/static/podclick-nav.js?v=20261003-1", "/podclick-design.css",
                   "/api/agents", "Punch list", "Work orders", "Put to work", "loadCrew", "escHtml"):
        assert marker in r.text, marker


def test_page_uses_tokens_only_and_clean_vocabulary():
    src = PAGE.read_text()
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", src)
    assert not re.search(r"ai-powered|leverage|unlock|synergy|settings|dashboard|workflow", src, re.I)
    assert "onclick=" not in src


def test_nav_crew_is_no_longer_soon():
    nav = (PAGE.parent / "static" / "podclick-nav.js").read_text()
    line = [l for l in nav.splitlines() if "'/agents'" in l][0]
    assert "soon" not in line
