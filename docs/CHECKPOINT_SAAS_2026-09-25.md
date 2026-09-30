# PodClick SaaS launch — checkpoint

## Objective and honest status

User requested a fresh swarm, SaaS readiness, Stripe subscriptions, the matching GoDaddy website, and a strong landing page. **Marketing preview and tested billing/security foundations are delivered; paid multi-tenant SaaS launch remains incomplete.** Do not describe the application as SaaS-ready or Stripe-connected yet.

## Completed

- Fresh coordinated workers audited security, implemented Stripe billing, discovered domain/deployment access, implemented a fail-closed deployment perimeter, and generated one original landing image. Parent integrated, verified, and published the marketing preview.
- Marketing: sibling `../podclick-marketing`, standalone Vinext/Sites, orange/black microphone art, product workflow, publishing-autopilot illustration, responsive styling, and honest private-launch CTA. No fabricated pricing/testimonials, customer data, or fake payment form.
- Private marketing publication: `https://podclick.jpeace77.chatgpt.site`. Project `appgprj_6ab6f617beb48191be4410c1391ccc8e`, version `appgprj_6ab6f617beb48191be4410c1391ccc8e~appgver_657f4f5f789c8191b9fdfc52f6f40168`, source `fb494c01361565bcbea265f57c64bc67f33578d5`, deployment `appgdep_6ab6f82838d88191b39abe77a33f0148` succeeded. New marketing repo only was committed/pushed; dirty studio repo was not.
- Stripe: checkout, customer portal, price allowlist, trusted-owner interface, raw-body signature verification, durable checkout intent/idempotency, transaction-locked event reconciliation, canceled/re-subscribe handling, and test/live separation. Default membership is 401, no account connected.
- SQLAlchemy billing models plus Alembic migration `b6e4d9f7a210`, prepared and rendered offline only. No live database changes.
- Outer pure-ASGI boundary protects every HTTP/WS/media path. Default/unknown mode is locked; local mode validates actual socket/Host/Origin and rejects forwarding spoofing. Locked startup cannot launch owner background tasks.
- Local studio remains running at `http://localhost:8765` with explicit local mode and proxy header trust disabled. Updated `.claude/launch.json` preserves that launch contract; static preview also binds loopback.

## Verification

- Full studio suite: **253 passed, five existing runtime/deprecation warnings**. Includes 166 perimeter cases and 27 Stripe tests; providers/database are mocked for billing tests.
- Local HTTP: studio200, health200, billing plans configured=false, forged forwarding403, unsigned billing status401.
- Landing build, typecheck, authored-page lint, compiled local HTTP200, and terminal Sites deployment status passed. Stock unused scaffold components retain their pre-existing lint issues; no generated component-library edits.
- No browser screenshot/interaction QA performed on the new Sites landing because it was not explicitly requested. The earlier studio upgrade's browser checks are documented separately.
- No real charges, customers, Stripe products/prices/webhooks, public podcast uploads, emails, DNS changes, or live database migrations were performed.

## Exact blockers and next actions

1. **Account access and commercial choices:** Stripe is available to connect but not confirmed connected. GoDaddy Domain Portfolio, Stripe Dashboard, Railway Dashboard all showed sign-in. No current owned domain/account/hosting IDs confirmed. The old SOW explicitly *assumes* `app.podclick.ai`; ownership is not established. User has been asked to confirm domain and subscription prices. Never infer pricing/trial terms or use another business's Stripe account.
2. **Real authentication and tenant isolation:** project list/get/update, media/jobs/autopilot are still single-owner inside the perimeter. Implement verified identity, server-derived membership, ownership filters/negative two-tenant tests, and trusted billing principal injection before customer access. Do not serve `studio_app` directly.
3. **Per-tenant integrations/security:** global provider tokens, OAuth callback state validation, encryption, provider callback origins, upload quotas/paid-feature enforcement, outbound URL validation, and resource limits must be completed for customer launch.
4. **Hosted worker/storage:** choose confirmed hosting target, provision persistent media/plans, keep legacy scheduler single-instance or replace it, validate ffmpeg/dependencies/backups/recovery. Current Railway config is guarded but not deployed this turn.
5. **Stripe sandbox:** after auth and approved account/prices, apply migration to the confirmed target, configure signed webhook and portal, verify checkout/payment/cancellation/failure/entitlement flows end-to-end. Only then consider live activation.
6. **Domain/public launch:** confirm GoDaddy ownership and matching site, read existing DNS to preserve mail/unrelated services, connect the selected hostname to the verified marketing/app deployment, then verify DNS/TLS. Sites currently owner-private; public access needs the explicit publication approval required by Sites hosting.

## Important files

`services/stripe_billing.py`, `routers/billing.py`, `db/billing_models.py`, `services/deployment_boundary.py`, `frontend/billing.html`, billing migration, `tests/test_stripe_billing.py`, `tests/test_deployment_boundary.py`, `tests/test_saas_integration.py`. `main.py`, `config.py`, `.env.example`, `.claude/launch.json`, and `railway.toml` contain integration/launch changes.

## Local run

From the studio root: `PODCLICK_DEPLOYMENT_MODE=local venv/bin/uvicorn main:app --host 127.0.0.1 --port 8765 --no-proxy-headers`. For safe test previews also set `PODCLICK_AUTOMATION_DISABLED=1` and use `--lifespan off` on another port. Never use local mode behind a tunnel or public reverse proxy.
