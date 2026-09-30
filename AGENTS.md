# PodClick — Project Context
> Reference docs below replace reading full source files. Update docs as code changes.

@docs/ARCHITECTURE.md
@docs/API.md
@docs/FRONTEND.md
@docs/BUGS_AND_FIXES.md

## Project Rules

### SaaS launch boundary (September 25, 2026 — later follow-up)

- Latest launch checkpoint: `docs/CHECKPOINT_SAAS_2026-09-25.md`. This is **not yet a paid multi-tenant SaaS**. Customer authentication, tenant resources/integrations, quotas, and live provider setup remain release blockers.
- `main:app` is the outer `DeploymentBoundary`; never serve `main:studio_app` publicly. Missing/unknown `PODCLICK_DEPLOYMENT_MODE` locks private HTTP, media, and WebSockets and skips owner background jobs. Only exact health and signed Stripe webhook routes are public exceptions.
- Explicit local mode requires loopback, local Host/port, no forwarding headers, and same-origin browser writes. Always launch with `--no-proxy-headers`; never expose local mode through a tunnel/reverse proxy.
- Stripe code lives in `services/stripe_billing.py`, `routers/billing.py`, `db/billing_models.py`. Default billing identity rejects 401; do not wire it to the hardcoded location helper. Migration `b6e4d9f7a210` is prepared but not applied.
- Marketing is a separate sibling project `../podclick-marketing`, owner-private on Sites. Do not copy private project/episode data into it. No domain ownership or live Stripe prices are confirmed.

### Current publishing implementation (September 25, 2026)

- Read `docs/CHECKPOINT_2026-09-25.md` for the UI/autopilot upgrade; the older `docs/current_state.md` inventory predates current database-backed projects.
- `services/podcast_autopilot.py` owns durable per-project release plans. Main integration is in `_autopilot_*` functions and `/api/projects/{id}/autopilot` routes. Do not bypass private preparation, explicit destination selection, or the shared legacy/autopilot project lock.
- `pipeline/scheduler.py` is the separate legacy Buzzsprout visibility queue, intended for a single running scheduler process. Never start a second live application against the same queue for verification.
- Safe preview: set `PODCLICK_DEPLOYMENT_MODE=local PODCLICK_AUTOMATION_DISABLED=1`, bind loopback, and run uvicorn on a separate port with `--lifespan off --no-proxy-headers`. Test providers with injected/mocked adapters; no real uploads, releases, guest messages, or Telegram alerts for QA.
- Focused verification: `venv/bin/python -m pytest tests/test_podcast_autopilot.py tests/test_autopilot_api.py tests/test_scheduler.py -q`. Existing Foundation regression checks are `tests/test_foundation.py`.
- The home page loads versioned `podclick-design.css`. Interceptor's default DOM-render screenshots may substitute external display fonts; inspect DOM bounds and font loading when screenshot text metrics disagree with the browser.

- **Server start:** `PODCLICK_DEPLOYMENT_MODE=local venv/bin/uvicorn main:app --host 127.0.0.1 --port 8765 --no-proxy-headers` from the project root.
- **Python version:** 3.9 — NO `str | None` syntax (use `Optional[str]` or just `= None`)
- **Frontend:** Static HTML, no build step. Edit files and hard-refresh browser.
- **Secrets:** All in `.env` — never hardcode. `os.getenv("KEY_NAME")` always.
- **Background jobs:** `asyncio.create_task()` — stored in `yt_spy_jobs` dict by UUID.
- **YouTube quota:** 10K units/day. Resets midnight Pacific. `_yt_get()` returns `{}` on failure.
- **Copy button encoding:** See `docs/FRONTEND.md` "Encoding Conventions" before adding any onclick copy button.
- **New API route:** Add to `docs/API.md` immediately.
- **New JS function:** Add to `docs/FRONTEND.md` immediately.
- **Bug fixed:** Add dated entry to `docs/BUGS_AND_FIXES.md` immediately.
