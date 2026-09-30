---
task: "Phase 3A — Brick the Foreman: Trust Model, Permit Ladder, Walk-Through, Punch List, Daily Cron, Memory"
project: PodClick
effort: E3
phase: VERIFY
progress: 24/63
mode: algorithm
started: "2026-05-27"
updated: "2026-09-25"
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

### September 25 follow-up — SaaS launch

Deliver a separate landing page, subscription billing, and the matching GoDaddy domain without exposing the owner's studio. Paid SaaS is not complete until customer authentication, tenant isolation, approved Stripe configuration, and verified domain/hosting exist. Current delivery is an owner-private marketing preview and tested, disabled-by-default billing foundation.

### September 25 — UI and publishing upgrade

Polish the existing PodClick home, project library, and episode workflow without replacing the established tools. Add persistent, opt-in release automation with destination readiness, precise scheduling, recovery, and visible outcomes. Validate locally using real-browser inspection and mocked publishing adapters; no real episodes are published as a verification side effect.

Build the Brick the Foreman subsystem: 5 new DB tables + User.timezone field, BrickAgent service with daily 4am planning cron, walk-through dashboard, punch list approve/reject UI, permit tier screen, and memory CRUD backend — all verified against 9 gates before phase is closed.

## Criteria

### September 25 SaaS follow-up

- [x] ISC-48: Antecedent: standalone landing page has a successful production build.
- [x] ISC-49: Marketing preview has a terminal successful owner-private deployment.
- [x] ISC-50: Anti: landing content does not invent paid signup, prices, testimonials, or customer statistics.
- [x] ISC-51: Billing actions reject an unverified or non-owner principal.
- [x] ISC-52: Stripe webhooks validate exact raw-body signatures and timestamp tolerance.
- [x] ISC-53: Repeated/out-of-order billing events cannot blindly duplicate checkout or replace a newer subscription.
- [x] ISC-54: Workspace billing migration produces valid offline PostgreSQL SQL.
- [x] ISC-55: Missing/unknown deployment mode locks private HTTP/media/WebSocket access.
- [x] ISC-56: Anti: test verification creates no real charges, uploads, or DNS changes.
- [ ] ISC-57: Approved Stripe account and commercial prices are connected and verified.
- [ ] ISC-58: Matching GoDaddy hostname ownership, DNS connection, and TLS are verified.
- [ ] ISC-59: Customer identity and two-tenant ownership isolation are enforced across the customer surface.
- [ ] ISC-60: Sandbox checkout, payment failure, cancellation, and feature entitlements pass end-to-end.
- [ ] ISC-61: Customer provider credentials and OAuth callbacks are securely isolated per tenant.
- [x] ISC-62: Local billing page accurately explains that paid access is not active.
- [ ] ISC-63: Customer compute/storage quotas and durable hosted workers pass launch checks.

### September 25 upgrade criteria (prior Phase 3A criteria retained below)

- [x] ISC-34: Antecedent: home-page controls form a coherent desktop layout without the reproduced empty right column.
- [x] ISC-35: Home, project library, and episode release controls fit a 390px viewport.
- [x] ISC-36: Existing upload and processing controls retain their IDs and original handlers; the output mode control responds in-browser.
- [x] ISC-37: Project library identifies past-due scheduled releases as needing attention.
- [x] ISC-38: Episode autopilot displays per-destination readiness and actionable blockers from the backend.
- [x] ISC-39: Release plans persist across service reconstruction.
- [x] ISC-40: Anti: episodes without explicit automatic authorization cannot publish through autopilot.
- [x] ISC-41: Autopilot observes the configured release time before making a destination public.
- [x] ISC-42: Pause or cancel prevents future automatic release actions.
- [x] ISC-43: Completed destination actions are not duplicated on repeated runs.
- [x] ISC-44: Transient release failures have a bounded retry policy.
- [x] ISC-45: Legacy queue claims prevent simultaneous duplicate dispatch.
- [x] ISC-46: Anti: verification performs no real posts or messages to external services.
- [x] ISC-47: Changed JavaScript and Python pass focused verification.

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

SaaS follow-up: mocked Stripe/store tests; pure-ASGI socket/Host/Origin/forwarding tests; full regression suite; migration SQL offline; landing HTTP, typecheck, authored-source lint, build, and terminal deployment status. Account ownership, prices, domain/DNS/TLS, live schema, customer identity/isolation, and sandbox payment flows remain pending.

September upgrade: inspect rendered pages in Interceptor at desktop and mobile widths (ISC-34–38); run isolated temporary-store tests with fake provider adapters for persistent plans, authorization, timing, pause, idempotency, and retries (ISC-39–45); inspect route validation through a lifespan-disabled local preview and avoid all publishing controls on real project data (ISC-46); parse changed scripts and run focused tests (ISC-47).

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

SaaS ownership: `stripe_billing` — service/router/models/tests (ISC-51–54); `production_boundary` — HTTP/WS perimeter (ISC-55); `saas_security_audit` — launch-risk evidence (ISC-59,61,63); `domain_launch_discovery` — provider/domain discovery (ISC-57,58); `landing_hero_asset` — one original image; primary — landing authoring/hosting, migration/integration, verification/docs (ISC-48–50,56,60,62). Only the primary authors/deploys the Sites checkout.

September upgrade ownership: `ui_polish` owns home/shared CSS (ISC-34–36); `project_experience` owns project library and release console (ISC-35,37,38); `autopilot_backend` owns service/API integration (ISC-39–44); primary owns legacy scheduler, integration, verification, and documentation (ISC-45–47). All agents preserve existing user changes.

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

- 2026-09-25 17:42: refined: Stripe and a marketing site do not make the single-owner studio SaaS-ready. Keep studio private until customer identity, tenant resources, integrations, and quotas are implemented.
- 2026-09-25 17:42: GoDaddy, Stripe, and Railway require sign-in; `app.podclick.ai` is only an assumption in old documentation. No DNS/live billing changes without confirmed targets and commercial terms.
- 2026-09-25 17:42: Available coordinated agent tools replace unavailable TeamCreate/nested subprocess workflows. Separate marketing repository only was committed/pushed; studio dirty changes retained.
- 2026-09-25 17:42: Sites requires explicit browser-testing intent before visual QA. New landing has build/HTTP/deployment evidence, not screenshot evidence. Previous studio QA is not reused as landing QA.

- 2026-09-25 16:50: refined: This work extends PodClick beyond the historical Phase 3A scope while retaining its criteria and history. Existing projects remain unchanged until the user explicitly saves an automatic release plan.
- 2026-09-25 16:50: UI reproduction captured at `/private/tmp/interceptor-screenshot-1790372761278.png`; the landing page has a narrow builder, unused right column, inconsistent navigation, and clipped controls. Fix layout at the shared style and container boundaries.
- 2026-09-25 16:50: Swarm uses the available coordinated agent tools with exclusive file ownership. Existing dirty files and the September 17 editor/clip fixes are preserved. No nested Codex subprocesses, broad repository migration, or automatic production publication.
- 2026-09-25 16:50: The authoritative recent history is `docs/BUGS_AND_FIXES.md`; `docs/current_state.md` and the older Phase 3A ISA do not reflect all subsequent project work.

- 2026-05-27: Postmark NOT used for notifications — `postmark_api_key` not configured. Telegram channel message is the Phase 3A notification channel. Will note in current_state.md.
- 2026-05-27: No mobile push for 3A — no push token infrastructure. Telegram is sufficient for JP's use case.
- 2026-05-27: APScheduler 3.x (not 4.x) chosen — 3.10.4 is the stable LTS, avoids breaking API changes in 4.x.
- 2026-05-27: brick_memory soft delete (active=false) — preserves audit trail, allows recovery, consistent with soft-delete pattern elsewhere.
- 2026-05-27: `last_referenced_at` updated in bulk at end of planning run (not per-row mid-prompt) — simpler, one DB roundtrip.
- 2026-05-27: Manual planning trigger `POST /api/brick/run-planning` added for Gate 9 verification and debugging.
- 2026-05-27: Brick's own speech uses brick-voice skill prompt injected into Claude's system prompt — NOT getBrandContext(). getBrandContext() is for user-attributed content only.

## Changelog

- 2026-09-25 | conjectured: Billing and domain connection would be the primary SaaS launch work.
  refuted by: Existing project/media/job access has no authentication; location and publishing credentials are single-owner globals.
  learned: Billing cannot replace customer authentication and tenant ownership; isolate marketing and protect the studio first.
  criterion now: ISC-59, ISC-61, and ISC-63 block customer launch; ISC-55 verifies the immediate perimeter.

## Verification

### SaaS follow-up — foundation shipped, full launch incomplete

- ISC-48: build — `Build complete. Run vinext start to start the production server.`; typecheck and authored-source lint exit0.
- ISC-49: deployment — `get_deployment_status` returned `status: succeeded` for `appgdep_6ab6f82838d88191b39abe77a33f0148`, owner-private URL `https://podclick.jpeace77.chatgpt.site`.
- ISC-50: source — `Private preview · No payment is collected here`; release illustration labeled `EXAMPLE`; no invented pricing/testimonials.
- ISC-51–53,55: tests — `253 passed, 5 warnings in 3.59s`; mocked Stripe and 166 perimeter probes included.
- ISC-54: SQL — offline Alembic produced both billing tables, FK/unique constraints/index, and revision update without a live connection.
- ISC-56: scope — no live payment/provider/DNS writes; marketing publication is separate and owner-private.
- ISC-62: HTTP — local billing200; plans `configured:false`; unsigned billing401; spoofed forwarding403; studio200.
- ISC-57–61,63: pending — account/commercial decisions, domain/DNS/TLS, customer identity/isolation, sandbox payment flows, tenant integrations, quotas/hosted worker durability. Full launch not complete.
- Independent final re-review failed at agent runtime authentication; no second-review pass claimed. Initial security audit and parent code/integration verification retained.

### September 25 upgrade — complete locally (14/14)

- ISC-34–38: real Chrome inspection, live search/output-mode/preflight interactions, desktop screenshots, and actual 390px iframe viewport measurements. Screenshots: `docs/verification/2026-09-25/`. DOM screenshots cannot render iframe pixels; mobile evidence is measured browser layout, not a screenshot claim.
- ISC-39–45: temporary-store, fake-provider regression tests in `tests/test_podcast_autopilot.py`, `tests/test_autopilot_api.py`, and `tests/test_scheduler.py`. Covers restarts, approval, due times, pause/cancel during uploads, receipt recovery, content edits, bounded retries, concurrent ownership, episode-number reservation, and independent due-plan scanning.
- ISC-46: no save/enable/approve controls used on live projects. Provider operations in automated tests are mocked. No real uploads, public posts, email, or Telegram messages were sent as QA actions.
- ISC-47: `venv/bin/python -m pytest tests -q --disable-warnings` → **48 passed, 5 warnings**; inline JavaScript parsed successfully; `git diff --check` passed. Warnings concern existing Python 3.9 support and deprecated FastAPI startup events.
- Local app reloaded on port 8765; health and readiness returned 200. Existing episodes remain opted out. No deployment, commit, or push performed.
- Historical Phase 3A ISC-1–33 remain unaudited; their unchecked state and the overall VERIFY phase intentionally remain unchanged.

### September 25 learning

- Conjectured: checking project content once at the start of a worker pass is sufficient. Refuted by: edits can arrive during a long private upload. Learned: refresh content/readiness immediately before each due publish and recheck durable stop signals after awaited inspection. Criterion now: ISC-41/42/43 includes in-flight-edit and stop-boundary regressions.
- Conjectured: awaiting the whole worker batch preserves due-time behavior. Refuted by: an unrelated upload can outlast another project's release time. Learned: retain one task per project and rescan independently with bounded concurrency. Criterion now: ISC-41 includes newly due work during another upload.
