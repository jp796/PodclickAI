"""Stripe API, intentionally unauthenticated only for plans and signed webhooks.

Parent application must inject a real verified membership dependency. The default
router is safe to mount but does not accept customer/workspace headers as identity.
"""
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from services.stripe_billing import BillingConfig, BillingError, BillingPrincipal, StripeBilling


async def require_billing_principal():
    raise HTTPException(401, "Sign in with a verified workspace membership to manage billing.")


def default_service():
    from db.engine import async_session
    from db.billing_models import PostgresBillingStore
    return StripeBilling(BillingConfig.from_env(), PostgresBillingStore(async_session))


def build_billing_router(principal_dependency: Callable = require_billing_principal,
                         service_dependency: Callable = default_service):
    router = APIRouter(prefix="/api/billing", tags=["Billing"])

    async def call(awaitable):
        try:
            return await awaitable
        except BillingError as exc:
            raise HTTPException(exc.status_code, str(exc))

    def owner_origin(request, service):
        # A same-origin JSON POST protects cookie-authenticated adapters from CSRF.
        # The webhook is explicitly exempt and uses a Stripe signature instead.
        try:
            service.config.require_ready()
        except BillingError as exc:
            raise HTTPException(exc.status_code, str(exc))
        if request.headers.get("origin") != service.config.public_url.rstrip("/"):
            raise HTTPException(403, "Use the configured PodClick website to manage billing.")
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise HTTPException(415, "Billing requests require application/json.")

    @router.get("/plans")
    async def plans(service=Depends(service_dependency)):
        ready = service.config.configured
        return {"configured": ready, "plans": list(service.config.prices) if ready else [],
                "checkout_requires_sign_in": True}

    @router.get("/status")
    async def status(principal: BillingPrincipal = Depends(principal_dependency), service=Depends(service_dependency)):
        return await call(service.status(principal))

    @router.post("/checkout")
    async def checkout(request: Request, principal: BillingPrincipal = Depends(principal_dependency),
                       service=Depends(service_dependency)):
        owner_origin(request, service)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Provide a JSON subscription plan.")
        if not isinstance(body, dict) or set(body) != {"plan"} or not isinstance(body.get("plan"), str):
            raise HTTPException(400, "Provide only an available plan name.")
        return await call(service.checkout(principal, body["plan"]))

    @router.post("/portal")
    async def portal(request: Request, principal: BillingPrincipal = Depends(principal_dependency),
                     service=Depends(service_dependency)):
        owner_origin(request, service)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Provide an empty JSON object.")
        if body != {}:
            raise HTTPException(400, "Customer and return URLs are determined by your workspace.")
        return await call(service.portal(principal))

    @router.post("/webhook")
    async def webhook(request: Request, service=Depends(service_dependency)):
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > 1024 * 1024:
                raise HTTPException(413, "Webhook payload is too large.")
        result = await call(service.webhook(bytes(payload), request.headers.get("stripe-signature", "")))
        return JSONResponse(result)

    return router


router = build_billing_router()
