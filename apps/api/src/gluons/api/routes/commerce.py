"""Merchant account consent and verified draft operations; no merchant credential in responses."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from gluons.api.deps import IdempotencyKey, PrincipalDep, current_principal, current_session, idempotent
from gluons.commerce import install, service
from gluons.config import get_settings
from gluons.connectors.shopify import ProductDraft, PublishProduct, RecordFulfillment
from gluons.core import GluonsError, validation_failed
from gluons.ratelimit import limited

router = APIRouter(prefix="/v1/commerce", tags=["commerce"])


class ShopifySession(BaseModel):
    shop: str = Field(max_length=100)
    capabilities: list[str] = Field(min_length=1, max_length=10)
    plan_id: str | None = None


class DraftRequest(BaseModel):
    automation_id: str
    connection_id: str
    plan_id: str | None = None
    product: ProductDraft


class SupplierRequest(BaseModel):
    plan_id: str
    name: str = Field(min_length=1, max_length=300)
    email: EmailStr
    source_url: str = Field(max_length=2000)


class InquiryRequest(BaseModel):
    automation_id: str
    connection_id: str
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=99_000)
    represented_entity: str = Field(min_length=1, max_length=300)


class PublicationRequest(BaseModel):
    automation_id: str
    connection_id: str
    plan_id: str | None = None
    publication: PublishProduct


class FulfillmentRequest(BaseModel):
    automation_id: str
    connection_id: str
    plan_id: str | None = None
    fulfillment: RecordFulfillment


class ProviderFact(BaseModel):
    receipt_id: str


@router.post("/shopify/sessions", status_code=201)
def begin(body: ShopifySession, principal: PrincipalDep) -> dict:
    return service.begin_session(principal, **body.model_dump())


@router.get("/shopify/connect-options")
def connect_options(principal: PrincipalDep) -> dict:
    """What this server can offer for connecting a store. Holds nothing secret."""
    principal.require("connections:read")
    settings = get_settings()
    url = settings.shopify_install_url if (settings.shopify_install_url or "").startswith("https://") else None
    return {"configured": service.shopify.configured(), "install_url": url,
            "pilot_shops_enabled": bool(settings.shopify_pilot_shops.strip())}


@router.get("/shopify/sessions/{session_id}")
def session_status(session_id: str, principal: PrincipalDep) -> dict:
    return service.session_status(principal, session_id)


def _web(path: str) -> str:
    return f"{get_settings().public_web_url.rstrip('/')}{path}"


def failure_redirect(error: GluonsError) -> RedirectResponse:
    """Provider redirects land in a browser, so a failure goes back to Gluons Web, not raw JSON."""
    return RedirectResponse(_web(f"/connections?connection_error={error.code}"), status_code=303)


@router.get("/shopify/install")
def install_entry(request: Request, _: None = limited("shopify_install")) -> RedirectResponse:
    """The app URL in the Shopify app configuration. Shopify opens it on install and on launch."""
    if len(request.url.query) > 10_000:
        raise validation_failed("The Shopify request is too large.")
    try:
        target, browser = install.begin(list(request.query_params.multi_items()))
    except GluonsError as exc:
        return failure_redirect(exc)
    response = RedirectResponse(target, status_code=303)
    if browser:
        response.set_cookie(install.BEGIN_COOKIE, browser, max_age=int(install.AUTHORIZE_WINDOW.total_seconds()), httponly=True,
                            samesite="lax", secure=get_settings().hosted, path=install.BEGIN_COOKIE_PATH)
    return response


@router.get("/shopify/callback")
def callback(request: Request) -> RedirectResponse:
    """One redirect URL serves both paths: a Shopify-initiated install, which has no Gluons
    session yet, and a pilot connection the signed-in owner started in Gluons."""
    if len(request.url.query) > 10_000:
        raise validation_failed("Authorization response is too large.")
    query = list(request.query_params.multi_items())
    state = request.query_params.get("state", "")
    try:
        if state and install.owns_state(state):
            install_id, secret = install.complete(query, request.cookies.get(install.BEGIN_COOKIE, ""))
            response = RedirectResponse(_web(f"/connect/shopify/{install_id}"), status_code=303)
            response.delete_cookie(install.BEGIN_COOKIE, path=install.BEGIN_COOKIE_PATH)
            response.set_cookie(install.CLAIM_COOKIE, secret, max_age=int(install.CLAIM_WINDOW.total_seconds()), httponly=True,
                                samesite="lax", secure=get_settings().hosted, path=install.CLAIM_COOKIE_PATH)
            return response
        principal = current_principal(current_session(request))
        result = service.callback(principal, query)
        return RedirectResponse(_web(f"/setup/{result['session_id']}"), status_code=303)
    except GluonsError as exc:
        return failure_redirect(exc)


@router.get("/shopify/installs/{install_id}")
def install_preview(install_id: str, request: Request, principal: PrincipalDep) -> dict:
    return install.preview(principal, install_id, request.cookies.get(install.CLAIM_COOKIE, ""))


@router.post("/shopify/installs/{install_id}/claim")
def install_claim(install_id: str, request: Request, response: Response, principal: PrincipalDep) -> dict:
    result = install.claim(principal, install_id, request.cookies.get(install.CLAIM_COOKIE, ""))
    response.delete_cookie(install.CLAIM_COOKIE, path=install.CLAIM_COOKIE_PATH)
    return result


@router.post("/shopify/connections/{connection_id}/verify")
def verify(connection_id: str, principal: PrincipalDep) -> dict:
    return service.verify_connection(principal, connection_id)


@router.post("/plans/{plan_id}/readiness")
def readiness(plan_id: str, principal: PrincipalDep, order_id: str | None = None) -> dict:
    return service.readiness(principal, plan_id, order_id=order_id)


@router.post("/catalog/drafts")
def stage_product(body: DraftRequest, principal: PrincipalDep, key: IdempotencyKey) -> dict:
    # The external action gateway owns durable idempotency and ambiguous recovery.
    return asdict(service.stage_product(principal, automation_id=body.automation_id, connection_id=body.connection_id,
                                       payload=body.product.model_dump(), idempotency_key=key, plan_id=body.plan_id))


@router.post("/suppliers", status_code=201)
def add_supplier(body: SupplierRequest, principal: PrincipalDep, key: IdempotencyKey) -> dict:
    return idempotent(principal, key, {"operation": "supplier.create", **body.model_dump(mode="json")},
                      lambda: service.add_supplier(principal, **body.model_dump(mode="json")))


@router.post("/suppliers/{supplier_id}/inquiries")
def inquiry(supplier_id: str, body: InquiryRequest, principal: PrincipalDep, key: IdempotencyKey) -> dict:
    return asdict(service.supplier_inquiry(principal, supplier_id=supplier_id, idempotency_key=key, **body.model_dump()))


@router.get("/catalog/targets/{connection_id}")
def catalog_targets(connection_id: str, principal: PrincipalDep) -> dict:
    return service.catalog_targets(principal, connection_id)


@router.post("/catalog/publications")
def publish(body: PublicationRequest, principal: PrincipalDep, key: IdempotencyKey) -> dict:
    return asdict(service.execute_commerce_action(principal, action="shopify.product.publish", automation_id=body.automation_id,
        connection_id=body.connection_id, payload=body.publication.model_dump(), plan_id=body.plan_id, idempotency_key=key))


@router.post("/fulfillments")
def fulfill(body: FulfillmentRequest, principal: PrincipalDep, key: IdempotencyKey) -> dict:
    return asdict(service.execute_commerce_action(principal, action="shopify.fulfillment.create", automation_id=body.automation_id,
        connection_id=body.connection_id, payload=body.fulfillment.model_dump(), plan_id=body.plan_id, idempotency_key=key))


@router.post("/plans/{plan_id}/provider-facts")
def record_fact(plan_id: str, body: ProviderFact, principal: PrincipalDep) -> dict:
    return service.record_provider_fact(principal, plan_id, body.receipt_id)


@router.post("/shopify/webhooks/{connection_id}")
async def webhook(connection_id: str, request: Request) -> dict:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 512 * 1024:
            raise validation_failed("Webhook exceeds the size limit.")
    return service.webhook(connection_id, bytes(raw), request.headers.get("x-shopify-hmac-sha256", ""),
                           request.headers.get("x-shopify-webhook-id", ""), request.headers.get("x-shopify-topic", ""),
                           request.headers.get("x-shopify-shop-domain", ""))


@router.post("/shopify/webhooks")
async def app_webhook(request: Request) -> dict:
    """Subscriptions declared in the app configuration, including Shopify's compliance topics."""
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 512 * 1024:
            raise validation_failed("Webhook exceeds the size limit.")
    return service.app_webhook(bytes(raw), request.headers.get("x-shopify-hmac-sha256", ""),
                               request.headers.get("x-shopify-webhook-id", ""), request.headers.get("x-shopify-topic", ""),
                               request.headers.get("x-shopify-shop-domain", ""))
