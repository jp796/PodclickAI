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
