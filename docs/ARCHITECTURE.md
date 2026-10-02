# PodClick Architecture
> Last updated: 2026-09-25 | Update this file when stack or data flows change.

## Persistent episode release automation

`frontend/project.html` configures an explicitly opted-in release through `/api/projects/{project_id}/autopilot`. `main.py` adapts the project database and Buzzsprout/YouTube providers into `services/podcast_autopilot.py`.

- Plans, destination receipts, activity, and durable pause/cancel signals live under `data/podcast_autopilot/`; atomic replacement and per-project file locks protect updates. Keep this directory persistent across restarts.
- The worker checks readiness, prepares private uploads, and publishes known provider IDs when due. Review mode requires explicit approval; automatic mode can recover when readiness becomes available. Content changes after preparation block stale release.
- Scans continue while individual uploads run, with at most two active project tasks. Pause/cancel is checked between provider actions; uncertain uploads require reconciliation instead of automatic reupload.
- Legacy scheduling shares ownership locks with autopilot. Episode-number reservation uses a shared PostgreSQL transaction advisory lock. The legacy JSON queue still requires a single scheduler process.
- Startup enables the local workers unless `PODCLICK_AUTOMATION_DISABLED=1`. Local scheduled releases require the service to remain running. Guest messages, Shorts, and social tools remain separate workflows.

See `API.md` for the endpoint contract and `CHECKPOINT_2026-09-25.md` for verified scope. The older architecture sections below describe established tools, not the complete current route inventory.

## Stack

The studio is currently single-owner, not customer-isolated SaaS. An outer pure-ASGI deployment perimeter now defaults private routes and WebSockets to locked; only explicitly configured local mode serves the studio. Stripe billing has workspace-scoped durable tables and a deny-by-default membership dependency, not a replacement for authentication. See `CHECKPOINT_SAAS_2026-09-25.md` before any public deployment.
- **Backend:** Python 3.9, FastAPI, uvicorn — `podcast-studio/main.py` (~3,775 lines)
- **Frontend:** Static HTML/JS (no build step) — `podcast-studio/frontend/`
  - `studio.html` — Recording studio + teleprompter (~1,300 lines)
  - `youtube-studio.html` — Click Studio / Market Scout (~2,805 lines)
- **Runtime:** `venv/` at `podcast-studio/venv/` — Python 3.9 venv
- **Port:** `8765` (local only)
- **AI:** OpenAI GPT-4o via `OPENAI_API_KEY` in `.env`
- **Env file:** `podcast-studio/.env` — all secrets (OPENAI, Buzzsprout, Telegram, TikTok, GHL, YouTube, Pexels)

## Run Server

```bash
PODCLICK_DEPLOYMENT_MODE=local venv/bin/uvicorn main:app --host 127.0.0.1 --port 8765 --no-proxy-headers
```

## Directory Layout

```
podcast-studio/
├── main.py          # All FastAPI routes + background job runners
├── frontend/
│   ├── studio.html          # Recording studio UI
│   └── youtube-studio.html  # Click Studio / Market Scout UI
├── docs/            # THIS DIRECTORY — reference docs for CLAUDE sessions
├── pipeline/        # Audio processing pipeline
├── data/            # Persisted JSON data (episodes, library, etc.)
├── .env             # Secrets (never commit)
└── venv/            # Python 3.9 virtualenv
```

## Key Architectural Patterns

### Job System (Market Scout / Competitor Spy)
```
POST /api/yt/competitor-spy → creates job in yt_spy_jobs dict → returns {job_id, status:"running"}
asyncio.create_task(_run_competitor_spy(job_id)) → runs 7 steps in background
GET  /api/yt/competitor-spy/{job_id} → returns job dict including step_statuses
```

**Job dict shape:**
```python
yt_spy_jobs[job_id] = {
    "status": "running" | "complete" | "error",
    "step": "current_step_key",
    "steps_complete": [...],
    "step_statuses": {
        "scanning_market":          {"status": "pending|running|completed|failed", "error": None},
        "identifying_competitors":  {"status": ...},
        "analyzing_viral":          {"status": ...},
        "finding_outliers":         {"status": ...},
        "mapping_standards":        {"status": ...},
        "discovering_searches":     {"status": ...},
        "compiling_intelligence":   {"status": ...},
    },
    "result": { ... },  # populated on complete
    "error": None,
    "city": str,
    "audience": str,
}
```

**Step helpers:**
- `_start_step(job_id, step_key)` — sets status="running", prints timestamp
- `_mark_step(job_id, step_key, error=None)` — sets "completed" or "failed"

### YouTube Data API v3
- **Daily quota:** 10,000 units. Resets midnight **Pacific Time**.
- `search.list` = 100 units per call. `videos.list` = 1 unit. `channels.list` = 1 unit.
- **Silent failure:** `_yt_get(url)` returns `{}` on ANY error including 403 quota exceeded.
- When quota is empty: all steps complete in <1 second with no data. UI shows empty grid. This is expected — not a bug.
- **Thumbnail URLs (no API needed):** `https://i.ytimg.com/vi/{VIDEO_ID}/hqdefault.jpg`

### Frontend Polling
```javascript
// pollJob() called every 2000ms
// step_statuses drives progress bar:
const completedCount = STEPS.filter(s => ['completed','failed'].includes((stepStatuses[s.key]||{}).status)).length;
const pct = Math.min(100, Math.round((completedCount / STEPS.length) * 100));
// On status === 'complete': clearInterval, wait 800ms for green checks, show report panel
```

### localStorage Cross-Tab Flow (Click Studio → Recording Studio)
```
youtube-studio.html: sendToTeleprompter()
  → localStorage.setItem('podclick_teleprompter_script', script)
  → localStorage.setItem('podclick_teleprompter_title', title)
  → window.open('/studio', '_blank')

studio.html: checkInboundScript() runs on DOMContentLoaded
  → reads + clears localStorage keys
  → populates textarea + renders prompter + shows toast
```

### Content Schedule (Today's Topic)
- `GET /api/studio/today-topic` reads `data/schedule.json`, finds today's shoot day
- `POST /api/studio/generate-script` calls GPT-4o with topic/pillar/market/notes → returns {script, title, hook_line}

## Agents hub and media providers (shipped 2026-10-02)

> Status: **shipped 2026-10-02 (provider live calls unverified; Muse and Higgsfield need keys).** Source: `AGENTS_HUB_SPEC.md` §1, §2, §4.  Verified provider facts: `MEDIA_PROVIDERS_VERIFIED.md`.

**Idea.** An agent is a named, approvable wrapper around generators that already exist, not a new framework. Brick's `GENERATOR_ACTIONS` allowlist and `_dispatch_generator` (in-process calls through `httpx.ASGITransport` against `studio_app`) are the model; agents call the same routes through `ctx.call_route`, so validation, the Foundation gate and output shape do not drift.

**Layout (planned).**
```
services/agents/   registry.py (12 AgentSpec entries, pure data), contract.py, jobs.py, internal.py,
                   render_thumbnail.py, runners/<agent_id>.py
services/media/    base.py, registry.py, elevenlabs.py, higgsfield.py, ffmpeg_local.py
routers/agents.py  /api/agents routes + GET /agents
```
Adding an agent = one `AgentSpec` entry plus one runner module. The registry tolerates a missing runner (`not_built`).

**Jobs.** In-memory `AGENT_JOBS` dict plus an atomic file write (tmp then `os.replace`) to `data/agent_jobs/{job_id}.json` on every status/step transition, the `podcast_autopilot` durability pattern. Runs start through `services/task_guard.spawn` with `on_task_failure`. No DB migration. On startup, jobs left in `queued|running|committing` become `failed` ("Interrupted by a restart"). Concurrency: one overall semaphore (4) plus one per media provider (2). Lifecycle: `queued -> running -> done | needs_approval -> committing -> done | rejected`; any non-terminal state can go to `cancelled`; failures go to `failed`.

**Approval path.** Each agent declares `run_tier`, `spend_tier`, `commit_tier` on Brick's existing ladder. User-initiated jobs always stop at `needs_approval` before a commit. Approval is a `BrickAction(action_type="agent_commit")` registered at `draftsman` in `ACTION_TIER_MAP` (the `guest_asset_package` precedent: the human click is the gate), and `_dispatch_action` gains one branch calling `services.agents.jobs.commit_job`. Approving from `/agents` and from the Walk-through punch list therefore commits the same job. Commits are idempotent per job.

**Constraints carried over from the codebase.** Agent bookkeeping is never written to `PostVariant.platform_specific` (GHL 422 risk, BUGS_AND_FIXES 2026-05-27). Provider keys are read from `config.settings` first, `os.getenv` fallback only (`.env` is not exported to the process environment). Agent output files live under `data/agent_outputs/{job_id}/` and are served only by the files route.

**Media provider layer.** `MediaProvider` interface with capabilities `tts`, `voices`, `avatar_video`, `image_to_video`, `text_to_video`, `text_to_image`, `ken_burns`. `get_provider(capability)` returns the first configured provider or `None` and never raises for a missing key. Hard requirements make an agent `needs_setup` before any job starts; soft requirements use a fallback (Ken Burns, captions-only, color plate) and add a job warning. `health()` is cached and never runs in a poll loop. A `submit` is never auto-retried after an uncertain response (double-charge risk); only reads and polls retry. Provider errors map to Brick-voice step errors.

**Settings.** ElevenLabs, Higgsfield, `PODCLICK_MEDIA_PREFERENCE`, and the `PODCLICK_AGENTS_DISABLED` kill switch (lists everything `not_built`, run returns 423); names are listed in `API.md`. No key, voice id or provider URL is logged, stored in a job file, or sent to a browser; job files record `provider` and `usage` only. PodClick does not read the PAI voice server's key.
