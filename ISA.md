---
task: "Phase 3A — Brick the Foreman: Trust Model, Permit Ladder, Walk-Through, Punch List, Daily Cron, Memory"
project: PodClick
effort: E3
phase: PLAN
progress: 0/9
mode: algorithm
started: "2026-05-27"
updated: "2026-05-27"
---

## Problem

PodClick generates content and schedules posts, but every action still requires JP to initiate and approve each step individually. There is no autonomous agent watching the job site overnight, planning tomorrow's work, surfacing what needs approval, or remembering standing instructions across sessions. Without Brick, the platform is a set of tools — not a foreman.

## Vision

JP opens the app at 7am and the walk-through is already built. Brick ran the job site at 4am, reviewed calendar performance, and drafted a punch list of 3 actions waiting for approval. JP taps Approve on two items in under 30 seconds, taps Reject on the third with a note. By 7:05am, two posts are queued and scheduled — without JP having written a word. The Permit screen shows Brick at Draftsman tier; JP can see what Brick can and can't do, and promote him when ready. A memory set 3 weeks ago ("Never pitch DealCheck on Mondays") still shows up in Brick's planning context every day, silently honored.

## Out of Scope

- Phase 3A does not implement auto-promotion/demotion based on track record thresholds — tier changes are manual (Promote/Demote buttons only)
- No mobile push notifications in 3A — Telegram channel message is the notification channel
- No Brick-to-Brick conversation history or multi-turn chat UI in 3A (brick_messages table seeds content only)
- No email digest via Postmark (not configured) — Telegram only
- No Brick actions above Draftsman tier in 3A planning loop (suggest/draft only — no queue, no publish)
- No UI for memory management in 3A — backend CRUD endpoints only
- No Foundation calls for Brick's own speech — Brick voice uses brick-voice skill prompt, not getBrandContext()
- No auto-generation of walk-through content for past dates — only current day forward

## Principles

- Brick speaks in construction vocabulary — Walk-through, Punch list, Permit, Job Site, Project, Closing; never "leverage," "unlock," or "as an AI"
- Permit tier gates are enforced server-side before any action executes — the UI cannot override the gate
- Memory is injected into every planning run — standing instructions are always honored, never forgotten
- Audit trail is immutable — every approve/reject writes actor_type + actor_id; Brick actions write actor_type='brick'
- `timezone` is data, not config — user.timezone drives the 4am cron, never a hardcoded string
- The brick_memory table is the user's voice inside Brick's head — treat writes with the same care as Foundation samples

## Constraints

- Python 3.9: `Optional[str]` not `str | None`, `List[T]` not `list[T]`
- `import anthropic as _anthropic` (never at module level — lazy import pattern)
- All secrets via `config.py` Pydantic Settings — never `os.getenv()` in route handlers
- Alembic migration chains from `a1f3c8d2e094` — no live-DB ALTER TABLE
- APScheduler 3.10.4 integrated with FastAPI lifespan event
- Brick actions above Draftsman tier (bricklayer, foreman, gc) must check brick_permits.current_tier before executing
- No direct `social_service.publish()` calls from planning loop in 3A — max action is 'draft_post' (Draftsman tier)
- All social publishes still go through SocialService abstraction — Brick is no exception when/if he publishes
- Construction vocabulary enforced in all user-facing strings on /walkthrough and /permit pages

## Goal

Build the Brick the Foreman subsystem: 5 new DB tables + User.timezone field, BrickAgent service with daily 4am planning cron, walk-through dashboard, punch list approve/reject UI, permit tier screen, and memory CRUD backend — all verified against 9 gates before phase is closed.

## Criteria

- [ ] ISC-1: `brick_permits` table exists with columns: id, location_id, current_tier, promoted_at, promoted_by, notes, created_at, updated_at — confirmed via `\d brick_permits`
- [ ] ISC-2: `brick_track_record` table exists with columns: id, location_id, action_type, outcome (success/failure/rejected), executed_at, metadata — confirmed via `\d brick_track_record`
- [ ] ISC-3: `brick_actions` table exists with columns: id, location_id, action_type, status (pending/approved/rejected/executed/expired), payload (JSONB), rationale, requested_at, reviewed_at, reviewed_by, review_note, expires_at — confirmed via `\d brick_actions`
- [ ] ISC-4: `brick_messages` table exists with columns: id, location_id, role, context_screen, content, created_at — confirmed via `\d brick_messages`
- [ ] ISC-5: `brick_memory` table exists with columns: id, location_id, content, category, active, created_at, last_referenced_at — confirmed via `\d brick_memory`
- [ ] ISC-6: `users` table has `timezone` column (Text, nullable, default 'America/Chicago') — confirmed via `\d users`
- [ ] ISC-7: Alembic migration `phase3a_brick_tables` applies cleanly (`alembic upgrade head` exits 0, `alembic current` shows new revision)
- [ ] ISC-8: `services/brick_agent.py` exists with class `BrickAgent` and methods: `run_daily_planning`, `execute_action`, `approve_action`, `reject_action`, `remember`, `forget`, `get_active_memories`
- [ ] ISC-9: `BrickAgent.run_daily_planning(location_id)` calls `_anthropic.Anthropic()` (Claude claude-sonnet-4-5) with brick-voice system prompt — confirmed by reading the method body
- [ ] ISC-10: Daily planning prompt includes `STANDING INSTRUCTIONS FROM USER` block populated from active `brick_memory` rows — confirmed by reading `_build_planning_prompt()` method
- [ ] ISC-11: `last_referenced_at` on `brick_memory` rows is updated when memories are read during planning — confirmed by reading the update query in `run_daily_planning`
- [ ] ISC-12: APScheduler job registered in FastAPI lifespan fires `run_daily_planning` at 04:00 America/Chicago (or user.timezone) daily — confirmed by reading lifespan setup
- [ ] ISC-13: `GET /walkthrough` returns FileResponse for `frontend/walkthrough.html` — confirmed via `curl -s -o /dev/null -w "%{http_code}" localhost:8765/walkthrough`
- [ ] ISC-14: `frontend/walkthrough.html` renders: Brick greeting (from brick_messages), "Built overnight" list, today's site plan, punch list, stats panel (Foundation %, posts MTD, foundation score trend), active projects with progress bars — confirmed by Interceptor screenshot
- [ ] ISC-15: Permit badge visible upper-right on walk-through page — confirmed by Interceptor screenshot
- [ ] ISC-16: `GET /permit` returns FileResponse for `frontend/permit.html` — confirmed via curl 200
- [ ] ISC-17: `frontend/permit.html` renders: current tier badge, track record stats, tier ladder (all 5 tiers), per-tier descriptions, Promote/Demote buttons — confirmed by Interceptor screenshot
- [ ] ISC-18: `POST /api/brick/actions/:id/approve` returns 200 and sets `brick_actions.status='approved'` + `reviewed_by=user_id` + `reviewed_at=now()` — confirmed by DB query
- [ ] ISC-19: After approve, the approved action's `status` flips to `'executed'` and disappears from punch list — confirmed by Interceptor: item gone from UI after approve click
- [ ] ISC-20: `POST /api/brick/actions/:id/reject` with `{"reason": "..."}` returns 200 and sets `brick_actions.status='rejected'` + `review_note=reason` — confirmed by DB query
- [ ] ISC-21: After reject, the item disappears from punch list — confirmed by Interceptor screenshot
- [ ] ISC-22: `POST /api/brick/actions/:id/approve` returns 403 if action requires a tier above current `brick_permits.current_tier` — confirmed by curl
- [ ] ISC-23: Actions older than 7 days are expired (status='expired') by daily cron — confirmed by reading cron body / manual trigger
- [ ] ISC-24: `POST /api/brick/permit/promote` advances `current_tier` one step (owner_builder→draftsman→bricklayer→foreman→gc) and writes `promoted_by`, `promoted_at` — confirmed by DB query after button click
- [ ] ISC-25: `POST /api/brick/permit/demote` reduces `current_tier` one step and records the change — confirmed by DB query
- [ ] ISC-26: `POST /api/brick/memory` creates a `brick_memory` row with `active=true`, returns `{"id": "uuid"}` — confirmed by curl
- [ ] ISC-27: `GET /api/brick/memory` returns all active `brick_memory` rows for location_id — confirmed by curl
- [ ] ISC-28: `DELETE /api/brick/memory/:id` sets `active=false` (soft delete), returns 200 — confirmed by curl; row still exists in DB with `active=false`
- [ ] ISC-29: Planning cron fires manually via `POST /api/brick/run-planning` and builds `brick_actions` rows + `brick_messages` greeting — confirmed by DB row count before/after
- [ ] ISC-30: Telegram notification sent when walk-through is ready — confirmed by message appearing in Telegram
- [ ] ISC-31: `anthropic` added to `requirements.txt` — confirmed by `grep anthropic requirements.txt`
- [ ] ISC-32: `apscheduler==3.10.4` added to `requirements.txt` — confirmed by `grep apscheduler requirements.txt`
- [ ] ISC-33: Brick planning loop creates zero `social_service.publish()` calls when `current_tier` is Draftsman — confirmed by log grep after manual run
- [ ] Anti: `POST /api/brick/actions/:id/approve` returns 403 when action_type requires foreman tier and current_tier is draftsman
- [ ] Anti: No `os.getenv()` calls in any new route handler (config.py settings only) — confirmed by `grep -n os.getenv services/brick_agent.py`
- [ ] Anti: No `str | None` Python 3.10+ syntax in any new file — confirmed by `grep -rn "str | None\|list\[" services/brick_agent.py`

## Test Strategy

| ISC | Type | Check | Threshold | Tool |
|-----|------|-------|-----------|------|
| ISC-1 through ISC-7 | schema | `alembic upgrade head` + `\d table` for each | exit 0, all columns present | Bash/psql |
| ISC-8 | static | `grep -n "def run_daily_planning\|def execute_action\|def approve_action\|def reject_action\|def remember\|def forget\|def get_active_memories" services/brick_agent.py` | 7 matches | Bash |
| ISC-9 | static | Read `services/brick_agent.py` `run_daily_planning` method | `_anthropic.Anthropic()` + `claude-sonnet-4-5` present | Read |
| ISC-10, ISC-11 | static | Read `_build_planning_prompt()` | STANDING INSTRUCTIONS block + last_referenced_at update | Read |
| ISC-12 | static | Read lifespan setup in main.py | APScheduler job with cron trigger 04:00 | Read |
| ISC-13, ISC-16 | http | `curl -s -o /dev/null -w "%{http_code}" localhost:8765/walkthrough` | 200 | Bash |
| ISC-14, ISC-15, ISC-17 | visual | Interceptor screenshot | elements visible | Interceptor |
| ISC-18 through ISC-25 | functional | curl + DB query | status field matches expected | Bash + psql |
| ISC-26 through ISC-28 | functional | curl memory endpoints | CRUD returns correct shape | Bash |
| ISC-29 | functional | `curl -X POST localhost:8765/api/brick/run-planning` + DB count | rows created | Bash + psql |
| ISC-30 | functional | Trigger planning, check Telegram | message delivered | Manual/Interceptor |
| ISC-31, ISC-32 | static | `grep` requirements.txt | lines present | Bash |
| ISC-33 | log | Grep server logs after manual planning run | zero publish calls | Bash |
| Anti-ISC | boundary | curl with wrong tier + grep for os.getenv + grep for str\|None | 403 + 0 matches | Bash |

## Features

| Name | Description | Satisfies | Depends On | Parallelizable |
|------|-------------|-----------|------------|----------------|
| db-migration | Alembic migration adding 5 tables + User.timezone | ISC-1 to ISC-7 | none | false |
| requirements-update | Add anthropic + apscheduler to requirements.txt | ISC-31, ISC-32 | none | true |
| brick-models | Add 5 SQLAlchemy model classes to db/models.py | ISC-1 to ISC-5 | db-migration | false |
| brick-agent-service | Create services/brick_agent.py with BrickAgent class | ISC-8 to ISC-12, ISC-23, ISC-29 | brick-models | false |
| walkthrough-frontend | Create frontend/walkthrough.html | ISC-14, ISC-15 | brick-agent-service | false |
| permit-frontend | Create frontend/permit.html | ISC-17 | brick-models | true (with walkthrough-frontend) |
| brick-api-routes | Add all /api/brick/* routes to main.py | ISC-13, ISC-16, ISC-18 to ISC-28 | brick-agent-service | false |
| cron-registration | Register APScheduler 4am cron in FastAPI lifespan | ISC-12 | brick-agent-service | false |

## Decisions

- 2026-05-27: Postmark NOT used for notifications — `postmark_api_key` not configured. Telegram channel message is the Phase 3A notification channel. Will note in current_state.md.
- 2026-05-27: No mobile push for 3A — no push token infrastructure. Telegram is sufficient for JP's use case.
- 2026-05-27: APScheduler 3.x (not 4.x) chosen — 3.10.4 is the stable LTS, avoids breaking API changes in 4.x.
- 2026-05-27: brick_memory soft delete (active=false) — preserves audit trail, allows recovery, consistent with soft-delete pattern elsewhere.
- 2026-05-27: `last_referenced_at` updated in bulk at end of planning run (not per-row mid-prompt) — simpler, one DB roundtrip.
- 2026-05-27: Manual planning trigger `POST /api/brick/run-planning` added for Gate 9 verification and debugging.
- 2026-05-27: Brick's own speech uses brick-voice skill prompt injected into Claude's system prompt — NOT getBrandContext(). getBrandContext() is for user-attributed content only.

## Verification

_(Populated after EXECUTE — Gate results go here)_
