"""Pinned Shopify Admin API boundary. No arbitrary hosts or GraphQL from model input."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import httpx
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


def get_credential(session: Session, tenant_id: str, connection: Connection) -> dict:
    if connection.provider != "shopify" or connection.status != "connected" or connection.disconnected_at or not connection.credential_ref:
        raise GluonsError("CONNECTION_REQUIRED", "Reconnect the Shopify store.", status=409)
    credential = vault.read(session, tenant_id, connection.credential_ref)
    if connection.token_expires_at and connection.token_expires_at <= utcnow() + timedelta(seconds=60):
        if not credential.get("refresh_token"):
            raise GluonsError("CONNECTION_REAUTH_REQUIRED", "Reconnect Shopify to renew store access.", status=409)
        settings = get_settings()
        with write_permit("shopify_credential_refresh"):
            result = request(shop_domain(connection.metadata_json["shop"]), "POST", "/admin/oauth/access_token", data={
                "client_id": settings.shopify_client_id, "client_secret": settings.shopify_client_secret,
                "grant_type": "refresh_token", "refresh_token": credential["refresh_token"],
            })
        if not result.get("access_token") or not result.get("refresh_token") or int(result.get("expires_in", 0)) <= 0:
            raise GluonsError("CONNECTION_REAUTH_REQUIRED", "Shopify credential renewal was incomplete. Reconnect.", status=409)
        credential = {**credential, **result}
        vault.replace(session, tenant_id, connection.credential_ref, credential)
        connection.token_expires_at = utcnow() + timedelta(seconds=int(result["expires_in"]))
    return credential


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
