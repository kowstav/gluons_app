"""Pinned Shopify Admin API boundary. No arbitrary hosts or GraphQL from model input."""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from gluons import vault
from gluons.actions import ProviderError, classify_http
from gluons.config import get_settings
from gluons.core import GluonsError, utcnow, validation_failed
from gluons.db.models import Connection
from gluons.net import GuardedClient, write_permit

SHOP_DOMAIN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\.myshopify\.com")
GID = re.compile(r"gid://shopify/(Product|Order)/[0-9]+")


def shop_domain(value: str) -> str:
    if not isinstance(value, str) or not SHOP_DOMAIN.fullmatch(value):
        raise validation_failed("Use the exact lowercase shop-name.myshopify.com domain, without a URL or path.")
    return value


def configured() -> bool:
    settings = get_settings()
    return bool(settings.shopify_client_id and settings.shopify_client_secret)


def callback_url() -> str:
    return f"{get_settings().public_api_url.rstrip('/')}/v1/commerce/shopify/callback"


def authorize_url(shop: str, scopes: list[str], state: str) -> str:
    return f"https://{shop_domain(shop)}/admin/oauth/authorize?" + urlencode({
        "client_id": get_settings().shopify_client_id, "scope": ",".join(scopes), "state": state, "redirect_uri": callback_url()})


def verify_query(parameters: list[tuple[str, str]], *, required: tuple[str, ...] = ()) -> dict[str, str]:
    """Shopify signs the query it sends to the app URL and to the OAuth callback. The signature
    is checked before any value in it is trusted, the shop included."""
    params = dict(parameters)
    if len(parameters) != len(params) or not all(params.get(key) for key in ("hmac", "timestamp", "shop", *required)):
        raise GluonsError("OAUTH_CALLBACK_INVALID", "The Shopify request is incomplete.", status=400)
    secret = get_settings().shopify_client_secret
    if not secret:
        raise GluonsError("CONFIGURATION_REQUIRED", "Configure the Shopify app credentials.", status=409)
    message = "&".join(f"{key}={value}" for key, value in sorted(params.items()) if key != "hmac")
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, params["hmac"]):
        raise GluonsError("OAUTH_SIGNATURE_INVALID", "The Shopify request signature is invalid.", status=400)
    try:
        fresh = abs(utcnow().timestamp() - int(params["timestamp"])) <= 300
    except (ValueError, OverflowError):
        fresh = False
    if not fresh:
        raise GluonsError("OAUTH_CALLBACK_EXPIRED", "This Shopify request has expired. Open Gluons from Shopify again.", status=400)
    shop_domain(params["shop"])
    return params


def token_metadata(result: dict) -> dict[str, str]:
    """A refresh token lasts at most 90 days and cannot be renewed once it lapses, so its expiry
    is kept where the health check can see it without opening the vault."""
    try:
        lifetime = int(result.get("refresh_token_expires_in") or 0)
    except (TypeError, ValueError):
        lifetime = 0
    return {"refresh_token_expires_at": (utcnow() + timedelta(seconds=lifetime)).isoformat()} if lifetime > 0 else {}


def request(shop: str, method: str, path: str, *, token: str | None = None, read_only: bool = False,
            transport: httpx.BaseTransport | None = None, **kwargs: Any) -> dict:
    shop_domain(shop)
    client = GuardedClient((shop,), transport=transport)
    headers = {"X-Shopify-Access-Token": token} if token else {}
    try:
        try:
            response = client.request(method, f"https://{shop}{path}", headers=headers, read_only=read_only, **kwargs)
        except httpx.TransportError as exc:
            raise ProviderError("retryable" if read_only else "ambiguous", "Shopify response could not be confirmed.") from exc
        if response.status_code >= 400:
            raise classify_http(response.status_code)
        result = response.json()
        if not isinstance(result, dict):
            raise ProviderError("permanent", "Shopify returned an invalid response.")
        return result
    finally:
        client.close()


def graphql(shop: str, token: str, query: str, variables: dict | None = None, *, mutation: bool = False,
            transport: httpx.BaseTransport | None = None) -> dict:
    version = get_settings().shopify_api_version
    if not re.fullmatch(r"20[0-9]{2}-(01|04|07|10)", version):
        raise GluonsError("CONFIGURATION_REQUIRED", "Pin a supported Shopify API quarterly version.", status=409)
    data = request(shop, "POST", f"/admin/api/{version}/graphql.json", token=token, read_only=not mutation,
                   json={"query": query, "variables": variables or {}}, transport=transport)
    if data.get("errors"):
        raise ProviderError("permanent", "Shopify rejected the requested operation or its permissions.")
    if not isinstance(data.get("data"), dict):
        raise ProviderError("permanent", "Shopify returned no operation data.")
    return data["data"]


def exchange(shop: str, code: str) -> dict:
    settings = get_settings()
    with write_permit("owner_shopify_authorization"):
        result = request(shop, "POST", "/admin/oauth/access_token", data={
            "client_id": settings.shopify_client_id, "client_secret": settings.shopify_client_secret,
            "code": code, "expiring": "1",
        })
    if not result.get("access_token") or not result.get("refresh_token") or int(result.get("expires_in", 0)) <= 0:
        raise GluonsError("OAUTH_TOKEN_INVALID", "Shopify did not issue the required expiring offline credential.", status=409)
    return result


def get_credential(session: Session, tenant_id: str, connection: Connection, *, force_refresh: bool = False,
                   transport: httpx.BaseTransport | None = None) -> dict:
    if connection.provider != "shopify" or connection.status != "connected" or connection.disconnected_at or not connection.credential_ref:
        raise GluonsError("CONNECTION_REQUIRED", "Reconnect the Shopify store.", status=409)
    if not (force_refresh or _expiring(connection)):
        return vault.read(session, tenant_id, connection.credential_ref)
    # A refresh retires the token it presents, so two workers refreshing one store at once would
    # leave the slower one holding a dead token. Lock the row, then look again.
    session.execute(select(Connection).where(Connection.id == connection.id).with_for_update()
                    .execution_options(populate_existing=True))
    if connection.status != "connected" or not connection.credential_ref:
        raise GluonsError("CONNECTION_REQUIRED", "Reconnect the Shopify store.", status=409)
    credential = vault.read(session, tenant_id, connection.credential_ref)
    if not (force_refresh or _expiring(connection)):
        return credential
    if not credential.get("refresh_token"):
        raise GluonsError("CONNECTION_REAUTH_REQUIRED", "Reconnect Shopify to renew store access.", status=409)
    settings = get_settings()
    try:
        with write_permit("shopify_credential_refresh"):
            result = request(shop_domain(connection.metadata_json["shop"]), "POST", "/admin/oauth/access_token", data={
                "client_id": settings.shopify_client_id, "client_secret": settings.shopify_client_secret,
                "grant_type": "refresh_token", "refresh_token": credential["refresh_token"],
            }, read_only=True, transport=transport)
    except ProviderError as exc:
        if exc.kind in ("auth", "permanent"):
            # Shopify answers a lapsed refresh token with 401 invalid_request. Only the owner can fix that.
            raise GluonsError("CONNECTION_REAUTH_REQUIRED", "Shopify no longer accepts this store's access. Reconnect the store.", status=409) from exc
        # A presented refresh token stays valid until its successor is used, so retrying a
        # timeout or 5xx with the same token is safe. It is never an ambiguous business write.
        raise ProviderError("retryable", "Shopify could not renew store access. It will be retried.") from exc
    if not result.get("access_token") or not result.get("refresh_token") or int(result.get("expires_in", 0)) <= 0:
        raise GluonsError("CONNECTION_REAUTH_REQUIRED", "Shopify credential renewal was incomplete. Reconnect.", status=409)
    credential = {**credential, **result}
    vault.replace(session, tenant_id, connection.credential_ref, credential)
    connection.token_expires_at = utcnow() + timedelta(seconds=int(result["expires_in"]))
    connection.metadata_json = {**connection.metadata_json, **token_metadata(result)}
    return credential


def _expiring(connection: Connection) -> bool:
    return bool(connection.token_expires_at and connection.token_expires_at <= utcnow() + timedelta(seconds=60))


def identity(shop: str, token: str) -> dict:
    data = graphql(shop, token, "query GluonsShopIdentity { shop { id name myshopifyDomain currencyCode plan { partnerDevelopment } } currentAppInstallation { accessScopes { handle } } }")
    account = data.get("shop") or {}
    if account.get("myshopifyDomain") != shop or not re.fullmatch(r"gid://shopify/Shop/[0-9]+", account.get("id", "")):
        raise GluonsError("PROVIDER_IDENTITY_MISMATCH", "Shopify did not confirm the expected store identity.", status=409)
    return {**account, "scopes": [scope["handle"] for scope in (data.get("currentAppInstallation") or {}).get("accessScopes", [])]}


def payment_status(shop: str, token: str) -> dict:
    data = graphql(shop, token, "query GluonsPaymentStatus { shopifyPaymentsAccount { id activated onboardable country defaultCurrency } }")
    account = data.get("shopifyPaymentsAccount")
    if account is None:
        return {"status": "unavailable", "setup_completed": False, "kyc_verified": False, "live_payments_verified": False}
    return {"status": "setup_completed" if account.get("activated") is True else "requires_owner_action",
            "setup_completed": account.get("activated") is True, "onboardable": account.get("onboardable") is True,
            "country": account.get("country"), "currency": account.get("defaultCurrency"),
            "provider_account_id": account.get("id"), "kyc_verified": False, "live_payments_verified": False,
            "notice": "Shopify reports setup state. This does not certify KYC, current processor acceptance, settlement or live checkout."}


def order_status(shop: str, token: str, order_gid: str) -> dict:
    if not re.fullmatch(r"gid://shopify/Order/[0-9]+", order_gid):
        raise validation_failed("Use a Shopify order record ID.")
    data = graphql(shop, token, "query GluonsOrderEvidence($id: ID!) { order(id: $id) { id name test cancelledAt displayFinancialStatus displayFulfillmentStatus fulfillments { id status trackingInfo { number url company } } } }", {"id": order_gid})
    order = data.get("order")
    if not order or order.get("id") != order_gid:
        raise GluonsError("INSUFFICIENT_EVIDENCE", "The order could not be read from the authorized store.", status=409)
    return {"provider_record": order, "payment_confirmed": order.get("displayFinancialStatus") == "PAID" and not order.get("cancelledAt"),
            "is_test": order.get("test") is True, "delivered": False,
            "notice": "Shopify fulfillment/tracking records are not independent proof of physical delivery."}
