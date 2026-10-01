"""
The Crew HTTP surface — routers/agents.py mounted on a THROWAWAY FastAPI app.

main.py is lane C's to edit, so these tests never import it: the router is
included exactly the way lane C will (`app.include_router(agents_router)`, no
prefix) and driven through httpx ASGITransport. Job-store collaborators are the
same fakes as test_agents_core.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from routers import agents as agents_router_mod
from services.agents import jobs
from tests.test_agents_core import LOC, install, make_runner, settle


@pytest.fixture
def harness(monkeypatch, tmp_path):
    h = install(monkeypatch, tmp_path)
    import config

    monkeypatch.setattr(config, "get_current_location_id", lambda: LOC)
    return h


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(agents_router_mod.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


# ── roster ───────────────────────────────────────────────────────────────────

async def test_roster_lists_all_categories_and_states(harness, client, monkeypatch):
    make_runner(monkeypatch, "trend_radar")
    make_runner(monkeypatch, "market_scout")
    harness.unmet = {"youtube_api"}
    r = await client.get("/api/agents")
    assert r.status_code == 200
    body = r.json()
    assert body["permit"] == {"current_tier": "draftsman"}
    assert body["foundation"] == {"tier": "solid", "sample_count": 40}
    assert [c["id"] for c in body["categories"]] == ["research", "plan", "create", "publish", "grow"]
    agents = {a["id"]: a for c in body["categories"] for a in c["agents"]}
    assert len(agents) == 12
    assert agents["trend_radar"]["state"] == "ready"
    assert agents["market_scout"]["state"] == "needs_setup"
    assert agents["market_scout"]["missing"] == ["youtube_api"]
    assert agents["voiceover"]["state"] == "not_built"
    assert {f["name"] for f in agents["trend_radar"]["fields"]} == {"city", "audience", "scout_job"}
    assert "key" not in r.text.lower().replace("monkey", "")  # no secret-shaped fields


async def test_agent_detail_and_404(harness, client, monkeypatch):
    make_runner(monkeypatch, "trend_radar")
    r = await client.get("/api/agents/trend_radar")
    assert r.status_code == 200 and r.json()["recent_jobs"] == []
    assert (await client.get("/api/agents/nope")).status_code == 404


async def test_jobs_path_is_never_captured_as_an_agent_id(harness, client):
    r = await client.get("/api/agents/jobs")
    assert r.status_code == 200
    assert r.json() == {"jobs": []}


# ── run ──────────────────────────────────────────────────────────────────────

async def test_run_returns_202_and_the_job_finishes(harness, client, monkeypatch):
    make_runner(monkeypatch, "trend_radar")
    r = await client.post("/api/agents/trend_radar/run", json={"input": {"city": "Springfield"}})
    assert r.status_code == 202
    assert r.json()["status"] == "queued"
    job = await settle(r.json()["job_id"])
    assert job["status"] == "done"
    g = await client.get("/api/agents/jobs/" + job["id"])
    assert g.status_code == 200 and g.json()["outputs"][0]["value"] == "draft text"
    listed = (await client.get("/api/agents/jobs?agent_id=trend_radar&status=done,failed")).json()
    assert [j["id"] for j in listed["jobs"]] == [job["id"]]
    assert "input" not in listed["jobs"][0]


async def test_run_error_shapes(harness, client, monkeypatch):
    make_runner(monkeypatch, "trend_radar")
    make_runner(monkeypatch, "market_scout")

    r = await client.post("/api/agents/trend_radar/run", json={"input": {}})
    assert r.status_code == 400
    assert r.json()["fields"] == {"city": "Required."} and r.json()["error"]

    r = await client.post("/api/agents/ghost/run", json={"input": {}})
    assert r.status_code == 404

    harness.unmet = {"youtube_api"}
    r = await client.post("/api/agents/market_scout/run", json={"input": {"city": "X"}})
    assert r.status_code == 409
    assert r.json()["needs_setup"] is True and r.json()["missing"] == ["youtube_api"]

    harness.foundation = {"tier": "not_ready", "sample_count": 1}
    r = await client.post("/api/agents/trend_radar/run", json={"input": {"city": "X"}})
    assert r.status_code == 422 and r.json()["foundation_not_ready"] is True

    r = await client.post("/api/agents/voiceover/run", json={"input": {"script": "hi"}})
    assert r.status_code == 423 and r.json()["error"] == "not_built"

    r = await client.post("/api/agents/trend_radar/run", json={"input": "city=X"})
    assert r.status_code == 400


async def test_unknown_job_is_404(harness, client):
    assert (await client.get("/api/agents/jobs/does-not-exist")).status_code == 404
    assert (await client.post("/api/agents/jobs/does-not-exist/approve", json={})).status_code == 404


# ── approve / reject / cancel ────────────────────────────────────────────────

async def _awaiting_approval(client, monkeypatch, agent="draftsman"):
    mod = make_runner(monkeypatch, agent, commit=True)
    r = await client.post("/api/agents/{}/run".format(agent), json={"input": {"topic": "rates"}})
    job = await settle(r.json()["job_id"])
    assert job["status"] == "needs_approval"
    return mod, job


async def test_approve_is_idempotent_over_http(harness, client, monkeypatch):
    mod, job = await _awaiting_approval(client, monkeypatch)
    a = await client.post("/api/agents/jobs/{}/approve".format(job["id"]),
                          json={"edits": {"o1": "tightened"}})
    assert a.status_code == 200 and a.json()["ok"] is True
    assert a.json()["job"]["status"] == "done"
    b = await client.post("/api/agents/jobs/{}/approve".format(job["id"]), json={})
    assert b.status_code == 200
    assert b.json()["job"]["commit_result"] == a.json()["job"]["commit_result"]
    assert mod.calls["commit"] == 1
    assert mod.calls["edits"] == {"o1": "tightened"}


async def test_approve_a_finished_non_commit_job_is_409(harness, client, monkeypatch):
    make_runner(monkeypatch, "trend_radar")
    r = await client.post("/api/agents/trend_radar/run", json={"input": {"city": "X"}})
    job = await settle(r.json()["job_id"])
    assert (await client.post("/api/agents/jobs/{}/approve".format(job["id"]), json={})).status_code == 409


async def test_approve_blocked_by_permit_is_403(harness, client, monkeypatch):
    mod, job = await _awaiting_approval(client, monkeypatch)
    harness.tier = "owner_builder"
    r = await client.post("/api/agents/jobs/{}/approve".format(job["id"]), json={})
    assert r.status_code == 403 and mod.calls["commit"] == 0


async def test_failed_commit_is_500_with_the_job(harness, client, monkeypatch):
    make_runner(monkeypatch, "draftsman", commit=True, commit_fail=RuntimeError("x"))
    r = await client.post("/api/agents/draftsman/run", json={"input": {"topic": "rates"}})
    job = await settle(r.json()["job_id"])
    a = await client.post("/api/agents/jobs/{}/approve".format(job["id"]), json={})
    assert a.status_code == 500
    assert a.json()["job"]["status"] == "failed" and a.json()["error"]


async def test_reject_and_cancel(harness, client, monkeypatch):
    _, job = await _awaiting_approval(client, monkeypatch)
    r = await client.post("/api/agents/jobs/{}/reject".format(job["id"]), json={"reason": "no"})
    assert r.status_code == 200 and r.json()["job"]["status"] == "rejected"
    assert (await client.post("/api/agents/jobs/{}/cancel".format(job["id"]))).status_code == 409
    assert (await client.post("/api/agents/jobs/{}/reject".format(job["id"]), json={})).status_code == 409


async def test_cancel_a_running_job_over_http(harness, client, monkeypatch):
    gate = asyncio.Event()
    mod = make_runner(monkeypatch, "trend_radar", gate=gate)
    r = await client.post("/api/agents/trend_radar/run", json={"input": {"city": "X"}})
    jid = r.json()["job_id"]
    for _ in range(50):
        await asyncio.sleep(0)
        if mod.calls["run"]:
            break
    c = await client.post("/api/agents/jobs/{}/cancel".format(jid))
    assert c.status_code == 200 and c.json()["job"]["status"] == "cancelled"
    gate.set()
    assert (await settle(jid))["status"] == "cancelled"
    assert mod.calls["step_two"] == 0


# ── files (traversal + Range) ────────────────────────────────────────────────

async def _job_with_file(client, monkeypatch, tmp_path, content=b"0123456789"):
    make_runner(monkeypatch, "trend_radar")
    r = await client.post("/api/agents/trend_radar/run", json={"input": {"city": "X"}})
    job = await settle(r.json()["job_id"])
    (tmp_path / "agent_outputs" / job["id"] / "tour.mp4").write_bytes(content)
    (tmp_path / "agent_outputs" / "secret.txt").write_text("other job's data")
    return job


async def test_file_traversal_is_refused(harness, client, monkeypatch, tmp_path):
    job = await _job_with_file(client, monkeypatch, tmp_path)
    base = "/api/agents/jobs/{}/files/".format(job["id"])
    # A bare "/.." is normalised away by the HTTP client before it is sent (it
    # resolves to the job URL itself), so it is covered at the store level in
    # test_agents_core; these are the encodings that DO reach the handler.
    for bad in ("..%2Fsecret.txt", "%2E%2E", "%2E%2E%2Fsecret.txt", "missing.mp4", "a%2Fb"):
        r = await client.get(base + bad)
        assert r.status_code == 404, bad
        assert "other job's data" not in r.text


async def test_file_serves_full_and_ranged(harness, client, monkeypatch, tmp_path):
    job = await _job_with_file(client, monkeypatch, tmp_path)
    url = "/api/agents/jobs/{}/files/tour.mp4".format(job["id"])
    full = await client.get(url)
    assert full.status_code == 200 and full.content == b"0123456789"
    assert full.headers["accept-ranges"] == "bytes"
    part = await client.get(url, headers={"Range": "bytes=2-5"})
    assert part.status_code == 206
    assert part.content == b"2345"
    assert part.headers["content-range"] == "bytes 2-5/10"
    tail = await client.get(url, headers={"Range": "bytes=-3"})
    assert tail.status_code == 206 and tail.content == b"789"
    bad = await client.get(url, headers={"Range": "bytes=50-60"})
    assert bad.status_code == 416 and bad.headers["content-range"] == "bytes */10"


# ── uploads ──────────────────────────────────────────────────────────────────

async def test_upload_accepts_images_and_feeds_files_fields(harness, client, monkeypatch, tmp_path):
    files = [("files", ("a{}.png".format(i), b"\x89PNG data", "image/png")) for i in range(4)]
    r = await client.post("/api/agents/uploads", files=files)
    assert r.status_code == 200
    ups = r.json()["uploads"]
    assert len(ups) == 4 and all(u["kind"] == "image" for u in ups)
    assert all(jobs.resolve_upload(u["upload_id"]) is not None for u in ups)
    make_runner(monkeypatch, "home_tour")
    run = await client.post("/api/agents/home_tour/run",
                            json={"input": {"photos": [u["upload_id"] for u in ups]}})
    assert run.status_code == 202
    missing = await client.post("/api/agents/home_tour/run",
                                json={"input": {"photos": ["0" * 32] * 4}})
    assert missing.status_code == 400 and "photos" in missing.json()["fields"]


async def test_upload_refuses_wrong_types_and_too_many(harness, client):
    r = await client.post("/api/agents/uploads", files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 400
    many = [("files", ("p{}.jpg".format(i), b"j", "image/jpeg")) for i in range(26)]
    assert (await client.post("/api/agents/uploads", files=many)).status_code == 400


async def test_upload_over_50mb_is_413_and_leaves_nothing(harness, client, monkeypatch, tmp_path):
    monkeypatch.setattr(agents_router_mod, "MAX_UPLOAD_BYTES", 10)
    r = await client.post("/api/agents/uploads",
                          files=[("files", ("ok.png", b"12345", "image/png")),
                                 ("files", ("big.png", b"x" * 64, "image/png"))])
    assert r.status_code == 413
    assert list((tmp_path / "agent_uploads").iterdir()) == []


# ── page ─────────────────────────────────────────────────────────────────────

async def test_agents_page_serves_the_html_or_says_not_on_site(harness, client, monkeypatch, tmp_path):
    monkeypatch.setattr(agents_router_mod, "FRONTEND", tmp_path)
    assert (await client.get("/agents")).status_code == 404
    (tmp_path / "agents.html").write_text("<h1>The Crew</h1>")
    r = await client.get("/agents")
    assert r.status_code == 200 and "The Crew" in r.text
