"""One front door: '/' -> /projects; the legacy Episode builder lives at
/legacy/episode-builder; the orphaned /editor/{id} stub redirects to /studio.

Driven against main.studio_app (the app inside the DeploymentBoundary perimeter,
which has its own tests) through httpx ASGITransport. No lifespan runs, so these
page routes touch no DB/Redis.
"""
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
NAV = FRONTEND / "static" / "podclick-nav.js"


@pytest.fixture
async def client():
    import main

    transport = httpx.ASGITransport(app=main.studio_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_root_redirects_to_job_site(client):
    r = await client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/projects"


async def test_legacy_episode_builder_serves_index(client):
    r = await client.get("/legacy/episode-builder")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert r.text == (FRONTEND / "index.html").read_text()
    assert "Legacy Episode builder" in r.text
    assert 'href="/projects"' in r.text


async def test_editor_stub_redirects_to_studio(client):
    r = await client.get("/editor/abc", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/studio"


async def test_projects_still_serves(client):
    r = await client.get("/projects")
    assert r.status_code == 200


def test_editor_stub_file_is_gone():
    assert not (FRONTEND / "editor.html").exists()


def test_legacy_builder_websockets_follow_page_protocol():
    src = (FRONTEND / "index.html").read_text()
    assert "new WebSocket(`ws://" not in src
    assert src.count("location.protocol === 'https:' ? 'wss:' : 'ws:'") >= 2


def test_nav_has_one_of_each_destination_and_no_root_entry():
    import re

    src = NAV.read_text()
    hrefs = re.findall(r"\{\s*href:\s*'([^']+)'", src)
    assert "/" not in hrefs
    assert "/projects" in hrefs
    assert "/legacy/episode-builder" in hrefs
    assert len(hrefs) == len(set(hrefs)), hrefs
    assert "'/editor/'" not in src


def test_no_internal_links_to_root():
    import re

    pat = re.compile(r"""href=['"]/['"]|location\.href\s*=\s*['"]/['"]""")
    offenders = []
    for p in list(FRONTEND.glob("*.html")) + [ROOT / "main.py"]:
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if pat.search(line):
                offenders.append("%s:%d" % (p.name, i))
    assert not offenders, offenders
