# PodClick — September 25 UI and release automation

## Objective

Polish the existing app and make podcast release management autonomous after explicit per-episode configuration. Preserve the working recording, editing, b-roll, Shorts, and guest tools.

## Completed work

- Three coordinated workers covered home/shared UI, episode/library UI, and the release service; the primary integrated and tested the changes and hardened the legacy queue.
- Updated home, episode library, and project publishing console; repaired blank layout and misleading overdue/retry states.
- Added file-backed review/automatic plans for Buzzsprout and YouTube, private preparation, readiness checks, scheduled publication, pause/resume/cancel, safe retries, durable receipts, duplicate prevention, and content version checks.
- Legacy manual scheduling and autopilot share ownership locks. Episode-number reservation is serialized. Existing user changes were retained; nothing was committed or pushed.

## Important locations

- UI: `frontend/index.html`, `frontend/podclick-design.css`, `frontend/projects.html`, `frontend/project.html`
- Backend: `services/podcast_autopilot.py`, `_autopilot_*` and release integration in `main.py`, `pipeline/scheduler.py`
- Tests: `tests/test_podcast_autopilot.py`, `tests/test_autopilot_api.py`, `tests/test_scheduler.py`
- API and function documentation: `docs/API.md`, `docs/FRONTEND.md`
- Screenshot evidence: `docs/verification/2026-09-25/`

## Decisions and verified state

- Existing episodes remain opted out. QA sent no public posts, uploads, emails, or Telegram messages.
- Safe verification used a port 8875 preview with automatic work disabled and lifespan off, fake adapters, and temporary stores. That preview is stopped. The normal port 8765 app has been reloaded with the final integrated backend.
- Final suite: **48 passed**, five existing runtime/deprecation warnings. JavaScript parsing and diff checks passed. The live app health endpoint and autopilot readiness endpoint returned 200.
- Regression checks prove edits during an upload block stale publication and unrelated long uploads do not prevent the worker from rescanning for newly due plans.
- Real browser checks passed: title search returns one matching episode; video output choice updates the processing hint; both selected destinations pass preflight without saving a plan. Actual 390px frame viewports have no horizontal document overflow.
- Historical Phase 3A criteria in `ISA.md` remain unverified history; this task does not claim that older scope complete.

## Completion and operational next action

The September upgrade is complete locally. No deployment, commit, or push was performed. Operational use: open an episode's Publishing autopilot, choose destinations/time, and explicitly save a review plan or enable automatic release. A review plan also requires approval before the worker acts. Existing episodes remain opted out.

## Known limits

The app must keep running for local due-time publication. Persistent plan storage must be retained. Started/uncertain uploads are not automatically replaced; the UI directs provider reconciliation. Python 3.9 and FastAPI startup deprecation warnings are pre-existing runtime maintenance work, outside this upgrade.
