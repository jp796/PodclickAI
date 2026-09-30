"""Billing regressions with transaction-faithful memory storage and fake Stripe.

No credentials, live PostgreSQL, products, subscriptions, charges, or webhooks.
"""
import asyncio
from contextlib import asynccontextmanager
import copy
import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from db.billing_models import BillingAccount, StripeEventReceipt
from routers.billing import build_billing_router
from services.stripe_billing import (
    BillingConfig, BillingError, BillingPrincipal, StripeBilling, StripeClient, verify_stripe_event,
)


NOW = 1700000000
LOCATION = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
OWNER = BillingPrincipal(OTHER, LOCATION, True)
CONFIG = BillingConfig("sk_test_fake", "whsec_fake", {"creator": "price_creator", "studio": "price_studio"},
                       "https://podclick.example")


class MemoryStore:
    def __init__(self):
        self.accounts = {}
        self.events = {}
        self.lock = None
        self.committed_customers = set()

    @asynccontextmanager
    async def locked(self, location_id=None, customer_id=None):
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:
            if customer_id:
                location_id = next((key for key, value in self.accounts.items()
                                    if value.stripe_customer_id == customer_id), None)
                if not location_id:
                    yield None
                    return
            snapshot = copy.deepcopy((self.accounts, self.events))
            account = self.accounts.setdefault(location_id, SimpleNamespace(
                location_id=location_id, stripe_customer_id=None, livemode=None, stripe_subscription_id=None,
                price_id=None, status="none", current_period_end=None, cancel_at_period_end=False,
                checkout_request_id=None, checkout_price_id=None, checkout_started_at=None,
                checkout_session_id=None, updated_at=0,
            ))
            async def has_event(event_id):
                return event_id in self.events

            async def record_event(event_id, event_type, processed_at):
                assert event_id not in self.events
                self.events[event_id] = (location_id, event_type, processed_at)

            try:
                yield SimpleNamespace(account=account, has_event=has_event, record_event=record_event)
            except Exception:
                self.accounts, self.events = snapshot
                raise
            else:
                if account.stripe_customer_id:
                    self.committed_customers.add(account.stripe_customer_id)


class FakeStripe:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.idempotent = {}
        self.checkouts = {}
        self.subscriptions = {}
        self.fail_checkout_once = False
        self.fail_subscription_once = False

    async def request(self, method, path, data=None, idempotency_key=None):
        self.calls.append((method, path, copy.deepcopy(data), idempotency_key))
        await asyncio.sleep(0)
        if method == "POST" and path == "customers":
            return {"id": "cus_" + data["metadata[podclick_location_id]"].replace("-", "")}
        if method == "POST" and path == "checkout/sessions":
            assert data["customer"] in self.store.committed_customers, "customer mapping must commit before checkout"
            if idempotency_key not in self.idempotent:
                value = {"id": "cs_test_" + str(len(self.idempotent)), "url": "https://checkout.stripe.com/pay/test",
                         "status": "open", "subscription": None}
                self.idempotent[idempotency_key] = value
                self.checkouts[value["id"]] = value
            if self.fail_checkout_once:
                self.fail_checkout_once = False
                raise BillingError("Fake lost response", 502)
            return self.idempotent[idempotency_key]
        if path.startswith("checkout/sessions/"):
            return self.checkouts[path.rsplit("/", 1)[-1]]
        if path.startswith("subscriptions/"):
            if self.fail_subscription_once:
                self.fail_subscription_once = False
                raise BillingError("Fake temporary outage", 502)
            return self.subscriptions[path.rsplit("/", 1)[-1]]
        if path == "billing_portal/sessions":
            return {"url": "https://billing.stripe.com/p/session/test"}
        raise AssertionError((method, path))


@pytest.fixture
def service():
    store = MemoryStore()
    return StripeBilling(CONFIG, store, FakeStripe(store), clock=lambda: NOW)


def signed_event(obj=None, kind="customer.subscription.updated", event_id="evt_test", now=NOW, live=False):
    event = {"id": event_id, "type": kind, "livemode": live,
             "data": {"object": obj or {"id": "sub_test", "customer": "cus_" + LOCATION.replace("-", "")}}}
    payload = json.dumps(event, separators=(",", ":")).encode()
    digest = hmac.new(CONFIG.webhook_secret.encode(), str(now).encode() + b"." + payload, hashlib.sha256).hexdigest()
    return payload, "t={},v1={}".format(now, digest)


async def prepare_subscription(service, subscription_id="sub_test", status="active", **overrides):
    if LOCATION not in service.store.accounts:
        await service.checkout(OWNER, "creator")
    account = service.store.accounts[LOCATION]
    value = {"id": subscription_id, "customer": account.stripe_customer_id, "livemode": False,
             "metadata": {"podclick_location_id": LOCATION, "podclick_checkout_request_id": account.checkout_request_id},
             "status": status, "items": {"data": [{"price": {"id": "price_creator"}}]},
             "current_period_end": NOW + 10000, "cancel_at_period_end": False}
    value.update(overrides)
    service.client.subscriptions[subscription_id] = value
    return value


def test_models_have_tenant_foreign_keys_and_unique_provider_bindings():
    ddl = str(CreateTable(BillingAccount.__table__).compile(dialect=postgresql.dialect()))
    assert "FOREIGN KEY(location_id) REFERENCES locations (id) ON DELETE CASCADE" in ddl
    assert "UNIQUE (stripe_customer_id)" in ddl
    assert "UNIQUE (stripe_subscription_id)" in ddl
    assert StripeEventReceipt.__table__.primary_key.columns.keys() == ["event_id"]


@pytest.mark.parametrize("config", [BillingConfig(), BillingConfig("sk_test_fake", "", CONFIG.prices, CONFIG.public_url),
    BillingConfig("sk_live_fake", "whsec_fake", CONFIG.prices, "http://localhost:8765"),
    BillingConfig("sk_test_fake", "whsec_fake", {"creator": "price_x", "studio": "price_x"}, CONFIG.public_url),
    BillingConfig("sk_test_fake", "whsec_fake", CONFIG.prices, "https://evil.example/path"),
    BillingConfig("sk_test_fake", "whsec_fake", [], CONFIG.public_url)])
def test_missing_or_unsafe_configuration_fails_closed(config):
    assert not config.configured
    with pytest.raises(BillingError) as error:
        config.require_ready()
    assert error.value.status_code == 503


def test_signature_uses_raw_body_timestamp_and_rotation():
    payload, signature = signed_event()
    assert verify_stripe_event(payload, signature + ",v1=bad", "whsec_fake", now=NOW)["id"] == "evt_test"
    for bad_body, bad_signature, now in ((payload + b" ", signature, NOW), (payload, signature, NOW + 301),
                                          (payload, signature, NOW - 301), (payload, signature + ",t=1", NOW),
                                          (payload, "bad", NOW)):
        with pytest.raises(BillingError):
            verify_stripe_event(bad_body, bad_signature, "whsec_fake", now=now)


@pytest.mark.asyncio
async def test_allowlist_and_owner_required_before_provider_calls(service):
    for principal, plan in ((OWNER, "price_evil"), (BillingPrincipal(OTHER, LOCATION, False), "creator"),
                            (BillingPrincipal("invalid", LOCATION, True), "creator")):
        with pytest.raises(BillingError):
            await service.checkout(principal, plan)
    assert not service.client.calls
    assert not service.store.accounts


@pytest.mark.asyncio
async def test_checkout_binds_tenant_and_reuses_concurrent_request(service):
    results = await asyncio.gather(service.checkout(OWNER, "creator"), service.checkout(OWNER, "creator"))
    assert results[0] == results[1]
    assert len(service.client.idempotent) == 1
    posts = [call for call in service.client.calls if call[:2] == ("POST", "checkout/sessions")]
    assert posts[0][2]["mode"] == "subscription"
    assert posts[0][2]["line_items[0][price]"] == "price_creator"
    assert posts[0][2]["subscription_data[metadata][podclick_location_id]"] == LOCATION
    assert posts[0][2]["success_url"] == CONFIG.public_url + "/billing?checkout=complete"
    assert (await service.status(OWNER))["entitled"] is False


@pytest.mark.asyncio
async def test_lost_checkout_response_reuses_durable_idempotency_key(service):
    service.client.fail_checkout_once = True
    with pytest.raises(BillingError):
        await service.checkout(OWNER, "creator")
    saved_request = service.store.accounts[LOCATION].checkout_request_id
    assert service.store.accounts[LOCATION].stripe_customer_id
    await service.checkout(OWNER, "creator")
    assert service.store.accounts[LOCATION].checkout_request_id == saved_request
    assert len(service.client.idempotent) == 1


@pytest.mark.asyncio
async def test_uncertain_attempt_cannot_retry_after_stripe_idempotency_window(service):
    service.client.fail_checkout_once = True
    with pytest.raises(BillingError):
        await service.checkout(OWNER, "creator")
    service.clock = lambda: NOW + 86400
    with pytest.raises(BillingError, match="reconciliation"):
        await service.checkout(OWNER, "creator")
    assert len(service.client.idempotent) == 1


@pytest.mark.asyncio
async def test_open_checkout_prevents_second_plan_and_expired_checkout_can_restart(service):
    await service.checkout(OWNER, "creator")
    with pytest.raises(BillingError, match="another plan"):
        await service.checkout(OWNER, "studio")
    first = service.store.accounts[LOCATION].checkout_session_id
    service.client.checkouts[first]["status"] = "expired"
    await service.checkout(OWNER, "studio")
    assert len(service.client.idempotent) == 2
    assert service.store.accounts[LOCATION].checkout_price_id == "price_studio"


@pytest.mark.asyncio
async def test_completed_checkout_never_grants_access_or_creates_another_subscription(service):
    await service.checkout(OWNER, "creator")
    service.client.checkouts[service.store.accounts[LOCATION].checkout_session_id]["status"] = "complete"
    assert not (await service.status(OWNER))["entitled"]
    with pytest.raises(BillingError, match="confirmation"):
        await service.checkout(OWNER, "creator")
    assert len(service.client.idempotent) == 1


@pytest.mark.asyncio
async def test_webhook_fresh_state_and_idempotent_event_receipt(service):
    await prepare_subscription(service)
    payload, signature = signed_event({"id": "sub_test", "customer": service.store.accounts[LOCATION].stripe_customer_id,
                                       "status": "canceled"})  # deliberately stale event snapshot
    assert await service.webhook(payload, signature) == {"received": True}
    assert (await service.status(OWNER))["entitled"]
    before = len(service.client.calls)
    assert (await service.webhook(payload, signature))["duplicate"]
    assert len(service.client.calls) == before
    assert len(service.store.events) == 1
    with pytest.raises(BillingError, match="already has"):
        await service.checkout(OWNER, "creator")


@pytest.mark.asyncio
async def test_cancellation_revokes_access_and_allows_repurchase(service):
    value = await prepare_subscription(service)
    await service.webhook(*signed_event())
    value["status"] = "canceled"
    await service.webhook(*signed_event(kind="customer.subscription.deleted", event_id="evt_canceled"))
    assert not (await service.status(OWNER))["entitled"]
    account = service.store.accounts[LOCATION]
    service.client.checkouts[account.checkout_session_id].update(status="complete", subscription="sub_test")
    await service.checkout(OWNER, "creator")
    assert len(service.client.idempotent) == 2


@pytest.mark.asyncio
async def test_old_subscription_events_cannot_replace_new_subscription(service):
    old = await prepare_subscription(service)
    await service.webhook(*signed_event())
    old["status"] = "canceled"
    await service.webhook(*signed_event(event_id="evt_oldCanceled"))
    account = service.store.accounts[LOCATION]
    service.client.checkouts[account.checkout_session_id].update(status="complete", subscription="sub_test")
    await service.checkout(OWNER, "creator")
    await prepare_subscription(service, "sub_new", status="canceled")
    customer = account.stripe_customer_id
    await service.webhook(*signed_event({"id": "sub_new", "customer": customer}, event_id="evt_new"))
    assert service.store.accounts[LOCATION].stripe_subscription_id == "sub_new"
    assert (await service.webhook(*signed_event(event_id="evt_oldAgain")))["ignored"]
    assert service.store.accounts[LOCATION].stripe_subscription_id == "sub_new"


@pytest.mark.asyncio
async def test_cross_tenant_metadata_rejected_and_receipt_not_committed(service):
    await prepare_subscription(service, metadata={"podclick_location_id": OTHER})
    with pytest.raises(BillingError, match="workspace"):
        await service.webhook(*signed_event())
    assert not service.store.events
    assert not (await service.status(OWNER))["entitled"]


@pytest.mark.asyncio
async def test_unknown_customer_is_not_bound_from_metadata(service):
    result = await service.webhook(*signed_event({"id": "sub_test", "customer": "cus_unknown",
                                                 "metadata": {"podclick_location_id": LOCATION}}))
    assert result["ignored"]
    assert not service.store.accounts
    assert not service.client.calls


@pytest.mark.asyncio
async def test_provider_failure_rolls_back_event_and_can_retry(service):
    await prepare_subscription(service)
    service.client.fail_subscription_once = True
    with pytest.raises(BillingError):
        await service.webhook(*signed_event())
    assert not service.store.events
    assert service.store.accounts[LOCATION].status == "none"
    await service.webhook(*signed_event())
    assert (await service.status(OWNER))["entitled"]


@pytest.mark.asyncio
async def test_price_and_period_expiry_fail_closed(service):
    value = await prepare_subscription(service, items={"data": [{"price": {"id": "price_unknown"}}]})
    await service.webhook(*signed_event())
    assert not (await service.status(OWNER))["entitled"]
    value.update(items={"data": [{"price": {"id": "price_creator"}}]}, current_period_end=NOW - 1)
    await service.webhook(*signed_event(event_id="evt_expired"))
    assert not (await service.status(OWNER))["entitled"]
    value.update(current_period_end=NOW + 10000, status="past_due")
    await service.webhook(*signed_event(event_id="evt_pastDue"))
    assert not (await service.status(OWNER))["entitled"]


@pytest.mark.asyncio
async def test_test_events_cannot_grant_live_access(service):
    await prepare_subscription(service)
    await service.webhook(*signed_event())
    service.config = BillingConfig("sk_live_fake", "whsec_fake", CONFIG.prices, CONFIG.public_url)
    assert not (await service.status(OWNER))["entitled"]
    with pytest.raises(BillingError):
        await service.webhook(*signed_event())
    with pytest.raises(BillingError, match="another Stripe mode"):
        await service.checkout(OWNER, "creator")


@pytest.mark.asyncio
async def test_portal_uses_saved_customer_and_trusted_return_url(service):
    await service.checkout(OWNER, "creator")
    assert (await service.portal(OWNER))["url"].startswith("https://billing.stripe.com/")
    assert service.client.calls[-1][2] == {"customer": service.store.accounts[LOCATION].stripe_customer_id,
                                          "return_url": CONFIG.public_url + "/billing"}


@pytest.mark.asyncio
async def test_http_contract_rejects_unauthenticated_and_spoofed_tenant_input(service):
    app = FastAPI()
    app.include_router(build_billing_router(service_dependency=lambda: service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=CONFIG.public_url) as client:
        response = await client.post("/api/billing/checkout", json={"plan": "creator"},
                                     headers={"X-User-Id": OTHER, "X-Location-Id": LOCATION, "Origin": CONFIG.public_url})
        assert response.status_code == 401
        assert (await client.get("/api/billing/status")).status_code == 401
        assert (await client.get("/api/billing/plans")).json()["checkout_requires_sign_in"]
    assert not service.client.calls


@pytest.mark.asyncio
async def test_http_contract_origin_and_server_owned_checkout_fields(service):
    app = FastAPI()
    app.include_router(build_billing_router(lambda: OWNER, lambda: service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=CONFIG.public_url) as client:
        for origin in ("https://evil.example", ""):
            assert (await client.post("/api/billing/checkout", json={"plan": "creator"},
                                      headers={"Origin": origin})).status_code == 403
        for payload in ({"plan": "creator", "location_id": OTHER}, {"plan": "creator", "amount": 1},
                        {"plan": "creator", "success_url": "https://evil.example"}, []):
            assert (await client.post("/api/billing/checkout", json=payload,
                                      headers={"Origin": CONFIG.public_url})).status_code == 400
        assert (await client.post("/api/billing/portal", json={"customer": "cus_victim"},
                                  headers={"Origin": CONFIG.public_url})).status_code == 400
        assert (await client.post("/api/billing/checkout", json={"plan": "creator"},
                                  headers={"Origin": CONFIG.public_url})).status_code == 200


@pytest.mark.asyncio
async def test_webhook_route_has_no_user_auth_but_requires_signed_raw_bytes(service):
    app = FastAPI()
    app.include_router(build_billing_router(service_dependency=lambda: service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=CONFIG.public_url) as client:
        payload, signature = signed_event()
        assert (await client.post("/api/billing/webhook", content=payload)).status_code == 400
        result = await client.post("/api/billing/webhook", content=payload, headers={"Stripe-Signature": signature})
        assert result.status_code == 200
        assert result.json()["ignored"]


@pytest.mark.asyncio
async def test_stripe_transport_pins_version_and_redacts_provider_errors():
    def handle(request):
        assert request.url.host == "api.stripe.com"
        assert request.headers["Stripe-Version"] == "2025-02-24.acacia"
        assert request.headers["Idempotency-Key"] == "safe-key"
        return httpx.Response(401, json={"error": {"message": "sensitive_fake_detail"}})
    client = StripeClient(CONFIG, httpx.MockTransport(handle))
    with pytest.raises(BillingError) as error:
        await client.request("POST", "customers", {}, "safe-key")
    assert "sensitive" not in str(error.value)
    assert error.value.status_code == 502
