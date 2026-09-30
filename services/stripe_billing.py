"""Configuration-driven Stripe subscriptions; no payment state from redirects.

Primary contracts: https://docs.stripe.com/webhooks,
https://docs.stripe.com/api/checkout/sessions/create,
https://docs.stripe.com/api/customer_portal/sessions/create.
Only a verified authentication adapter may construct a BillingPrincipal. Existing
single-tenant config.get_current_location_id is NOT an authentication adapter.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
import re
import time
from typing import Dict
from urllib.parse import urlsplit
import uuid

import httpx


class BillingError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class BillingPrincipal:
    user_id: str
    location_id: str
    can_manage_billing: bool = False

    def validate(self, manage=False):
        try:
            uuid.UUID(self.user_id)
            uuid.UUID(self.location_id)
        except (TypeError, ValueError, AttributeError):
            raise BillingError("A verified workspace membership is required.", 401)
        if manage and self.can_manage_billing is not True:
            raise BillingError("Only a workspace owner can manage billing.", 403)


@dataclass(frozen=True)
class BillingConfig:
    secret_key: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    prices: Dict[str, str] = field(default_factory=dict)
    public_url: str = ""
    api_version: str = "2025-02-24.acacia"

    @classmethod
    def from_env(cls):
        try:
            prices = json.loads(os.getenv("STRIPE_PRICE_IDS", "{}"))
        except ValueError:
            prices = {}
        return cls(os.getenv("STRIPE_SECRET_KEY", ""), os.getenv("STRIPE_WEBHOOK_SECRET", ""),
                   prices, os.getenv("PODCLICK_PUBLIC_URL", "").rstrip("/"),
                   os.getenv("STRIPE_API_VERSION", "2025-02-24.acacia"))

    @property
    def livemode(self):
        return self.secret_key.startswith(("sk_live_", "rk_live_"))

    def require_ready(self):
        if not self.secret_key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
            raise BillingError("Stripe billing is not configured yet.", 503)
        if not self.webhook_secret.startswith("whsec_"):
            raise BillingError("Stripe webhook verification must be configured before checkout.", 503)
        if (not isinstance(self.prices, dict) or not self.prices or
                not all(isinstance(k, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", k)
                        and isinstance(v, str) and re.fullmatch(r"price_[A-Za-z0-9]+", v)
                        for k, v in self.prices.items()) or len(set(self.prices.values())) != len(self.prices)):
            raise BillingError("Approved Stripe subscription prices are not configured yet.", 503)
        parsed = urlsplit(self.public_url)
        local_test = (not self.livemode and parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))
        if (not parsed.netloc or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or (parsed.scheme != "https" and not local_test)):
            raise BillingError("Billing requires a configured public HTTPS origin.", 503)

    @property
    def configured(self):
        try:
            self.require_ready()
            return True
        except BillingError:
            return False


def verify_stripe_event(payload, signature, secret, now=None, tolerance=300):
    """Verify exact request bytes before parsing; allow Stripe key-rotation v1s."""
    if not secret.startswith("whsec_"):
        raise BillingError("Stripe webhook is not configured.", 503)
    if len(payload) > 1024 * 1024:
        raise BillingError("Webhook payload is too large.", 413)
    try:
        pieces = [part.split("=", 1) for part in signature.split(",")]
        timestamps = [value for key, value in pieces if key == "t"]
        candidates = [value for key, value in pieces if key == "v1"]
        if len(timestamps) != 1:
            raise ValueError()
        timestamp = int(timestamps[0])
        if abs((time.time() if now is None else now) - timestamp) > tolerance:
            raise ValueError()
        expected = hmac.new(secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
            raise ValueError()
        event = json.loads(payload)
        if (not isinstance(event, dict) or not isinstance(event.get("id"), str)
                or not re.fullmatch(r"evt_[A-Za-z0-9]+", event["id"])
                or not isinstance(event.get("type"), str)
                or not isinstance(event.get("data", {}).get("object"), dict)):
            raise ValueError()
        return event
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise BillingError("Invalid Stripe webhook signature or payload.", 400)


class StripeClient:
    """Small official REST surface. Never log provider payloads or credentials."""
    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport

    async def request(self, method, path, data=None, idempotency_key=None):
        headers = {"Authorization": "Bearer " + self.config.secret_key, "Stripe-Version": self.config.api_version}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            async with httpx.AsyncClient(timeout=15, transport=self.transport, follow_redirects=False) as client:
                response = await client.request(method, "https://api.stripe.com/v1/" + path,
                                                headers=headers, data=data)
            if response.status_code >= 400:
                raise BillingError("Stripe could not complete this request. Please retry or contact support.", 502)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (httpx.HTTPError, ValueError):
            raise BillingError("Stripe is temporarily unavailable. Please retry.", 502)


def _identifier(value, prefix):
    value = value.get("id") if isinstance(value, dict) else value
    if not isinstance(value, str) or not re.fullmatch(prefix + r"[A-Za-z0-9_]+", value):
        raise BillingError("Stripe returned an invalid billing reference.", 502)
    return value


def _stripe_url(value, hostname):
    parsed = urlsplit(value or "")
    if parsed.scheme != "https" or parsed.hostname != hostname or parsed.username or parsed.password:
        raise BillingError("Stripe did not return a secure checkout URL.", 502)
    return value


class StripeBilling:
    def __init__(self, config, store, client=None, clock=time.time):
        self.config = config
        self.store = store
        self.client = client or StripeClient(config)
        self.clock = clock

    async def checkout(self, principal, plan):
        principal.validate(manage=True)
        self.config.require_ready()
        if plan not in self.config.prices:
            raise BillingError("Choose one of the available subscription plans.")
        price = self.config.prices[plan]
        # Persist the operation identity BEFORE contacting Stripe. On a timeout or
        # crashed response, the next request uses precisely the same idempotency key.
        async with self.store.locked(location_id=principal.location_id) as tx:
            account = tx.account
            if account.livemode is not None and account.livemode is not self.config.livemode:
                raise BillingError("This workspace billing record belongs to another Stripe mode.", 409)
            account.livemode = self.config.livemode
            if account.stripe_subscription_id and account.status not in ("canceled", "incomplete_expired"):
                raise BillingError("This workspace already has a subscription. Use Manage billing.", 409)
            if account.checkout_session_id:
                checkout = await self.client.request("GET", "checkout/sessions/" + account.checkout_session_id)
                if checkout.get("status") == "complete":
                    # A confirmed, subsequently canceled subscription can buy
                    # again. An unconfirmed completed checkout must never do so.
                    old_subscription = checkout.get("subscription")
                    old_subscription = old_subscription.get("id") if isinstance(old_subscription, dict) else old_subscription
                    if (not account.stripe_subscription_id or old_subscription != account.stripe_subscription_id
                            or account.status not in ("canceled", "incomplete_expired")):
                        raise BillingError("Checkout is complete; payment confirmation is still processing.", 409)
                if checkout.get("status") == "open":
                    if account.checkout_price_id != price:
                        raise BillingError("This workspace already has an open checkout for another plan.", 409)
                    return {"url": _stripe_url(checkout.get("url"), "checkout.stripe.com")}
                if checkout.get("status") not in ("expired", "complete"):
                    raise BillingError("Checkout requires billing support reconciliation.", 409)
                account.checkout_request_id = None
                account.checkout_session_id = None
            if account.checkout_request_id:
                if account.checkout_price_id != price:
                    raise BillingError("A checkout attempt already exists for another plan.", 409)
                if self.clock() - account.checkout_started_at > 23 * 3600:
                    raise BillingError("An unconfirmed checkout needs support reconciliation before retrying.", 409)
            else:
                account.checkout_request_id = str(uuid.uuid4())
                account.checkout_price_id = price
                account.checkout_started_at = int(self.clock())
            request_id = account.checkout_request_id

        async with self.store.locked(location_id=principal.location_id) as tx:
            account = tx.account
            if account.checkout_request_id != request_id:
                raise BillingError("Billing changed; reload before trying again.", 409)
            if account.stripe_subscription_id and account.status not in ("canceled", "incomplete_expired"):
                raise BillingError("This workspace already has a subscription. Use Manage billing.", 409)
            if not account.stripe_customer_id:
                customer = await self.client.request("POST", "customers", {
                    "metadata[podclick_location_id]": principal.location_id,
                }, "podclick-customer-" + principal.location_id)
                account.stripe_customer_id = _identifier(customer.get("id"), "cus_")
        # Commit customer ownership before a Checkout Session can emit webhooks.
        async with self.store.locked(location_id=principal.location_id) as tx:
            account = tx.account
            if account.checkout_request_id != request_id:
                raise BillingError("Billing changed; reload before trying again.", 409)
            if account.stripe_subscription_id and account.status not in ("canceled", "incomplete_expired"):
                raise BillingError("This workspace already has a subscription. Use Manage billing.", 409)
            # Metadata is generated from trusted membership, never a client body.
            checkout = await self.client.request("POST", "checkout/sessions", {
                "mode": "subscription", "customer": account.stripe_customer_id,
                "client_reference_id": principal.location_id,
                "line_items[0][price]": price, "line_items[0][quantity]": "1",
                "metadata[podclick_location_id]": principal.location_id,
                "subscription_data[metadata][podclick_location_id]": principal.location_id,
                "subscription_data[metadata][podclick_checkout_request_id]": request_id,
                "success_url": self.config.public_url + "/billing?checkout=complete",
                "cancel_url": self.config.public_url + "/billing?checkout=canceled",
            }, "podclick-checkout-" + request_id)
            account.checkout_session_id = _identifier(checkout.get("id"), "cs_")
            account.updated_at = int(self.clock())
            return {"url": _stripe_url(checkout.get("url"), "checkout.stripe.com")}

    async def portal(self, principal):
        principal.validate(manage=True)
        self.config.require_ready()
        async with self.store.locked(location_id=principal.location_id) as tx:
            if tx.account.livemode is not self.config.livemode:
                raise BillingError("This workspace billing record belongs to another Stripe mode.", 409)
            if not tx.account.stripe_customer_id:
                raise BillingError("This workspace has no Stripe customer yet.", 409)
            portal = await self.client.request("POST", "billing_portal/sessions", {
                "customer": tx.account.stripe_customer_id,
                "return_url": self.config.public_url + "/billing",
            }, "podclick-portal-" + str(uuid.uuid4()))
            return {"url": _stripe_url(portal.get("url"), "billing.stripe.com")}

    async def status(self, principal):
        principal.validate()
        self.config.require_ready()
        async with self.store.locked(location_id=principal.location_id) as tx:
            account = tx.account
            plan = next((key for key, price in self.config.prices.items() if price == account.price_id), None)
            return {"plan": plan, "status": account.status,
                    "entitled": bool(account.livemode is self.config.livemode and plan and account.status in ("active", "trialing")
                                     and (account.current_period_end or 0) > self.clock()),
                    "current_period_end": account.current_period_end,
                    "cancel_at_period_end": bool(account.cancel_at_period_end),
                    "can_manage_billing": principal.can_manage_billing}

    async def webhook(self, payload, signature):
        self.config.require_ready()
        event = verify_stripe_event(payload, signature, self.config.webhook_secret, now=self.clock())
        if event.get("livemode") is not self.config.livemode or event.get("account") or event.get("context"):
            raise BillingError("This event does not match the configured Stripe account mode.", 400)
        kind = event["type"]
        obj = event["data"]["object"]
        if kind.startswith("customer.subscription.") and kind in (
                "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted",
                "customer.subscription.paused", "customer.subscription.resumed"):
            subscription_id = _identifier(obj.get("id"), "sub_")
        elif kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded",
                      "invoice.paid", "invoice.payment_failed"):
            if kind.startswith("checkout.") and obj.get("mode") != "subscription":
                return {"received": True, "ignored": True}
            parent = obj.get("parent") or {}
            subscription = obj.get("subscription") or (parent.get("subscription_details") or {}).get("subscription")
            if not subscription:
                return {"received": True, "ignored": True}
            subscription_id = _identifier(subscription, "sub_")
        else:
            return {"received": True, "ignored": True}
        customer_id = _identifier(obj.get("customer"), "cus_")
        async with self.store.locked(customer_id=customer_id) as tx:
            if tx is None:
                # Shared Stripe accounts may contain unrelated customers. Never
                # create a tenant association from webhook metadata alone.
                return {"received": True, "ignored": True}
            if await tx.has_event(event["id"]):
                return {"received": True, "duplicate": True}
            account = tx.account
            if account.livemode is not self.config.livemode:
                raise BillingError("This workspace billing record belongs to another Stripe mode.", 409)
            if account.stripe_subscription_id and account.stripe_subscription_id != subscription_id:
                if account.status not in ("canceled", "incomplete_expired"):
                    await tx.record_event(event["id"], kind, int(self.clock()))
                    return {"received": True, "ignored": True}
            # Stripe delivery order and same-second timestamps are not ordered.
            # Fetch the authoritative subscription *inside* the workspace lock.
            subscription = await self.client.request("GET", "subscriptions/" + subscription_id)
            if (_identifier(subscription.get("customer"), "cus_") != account.stripe_customer_id
                    or subscription.get("metadata", {}).get("podclick_location_id") != str(account.location_id)):
                raise BillingError("Subscription does not match its verified workspace.", 409)
            if (account.stripe_subscription_id != subscription_id and
                    (not account.checkout_request_id or subscription.get("metadata", {}).get("podclick_checkout_request_id") != account.checkout_request_id)):
                await tx.record_event(event["id"], kind, int(self.clock()))
                return {"received": True, "ignored": True}
            if subscription.get("livemode") is not self.config.livemode:
                raise BillingError("Subscription mode does not match billing configuration.", 409)
            items = (subscription.get("items") or {}).get("data") or []
            price_id = (items[0].get("price") or {}).get("id") if len(items) == 1 else None
            period_end = subscription.get("current_period_end")
            if period_end is None and len(items) == 1:
                period_end = items[0].get("current_period_end")
            account.stripe_subscription_id = subscription_id
            account.price_id = price_id if price_id in self.config.prices.values() else None
            account.status = subscription.get("status", "unknown")
            account.current_period_end = int(period_end) if isinstance(period_end, (int, float)) else None
            account.cancel_at_period_end = bool(subscription.get("cancel_at_period_end"))
            account.updated_at = int(self.clock())
            await tx.record_event(event["id"], kind, int(self.clock()))
            return {"received": True}
