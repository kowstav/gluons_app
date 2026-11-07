"""Shopify-initiated installs, the only path Shopify allows a public app.

The merchant starts in Shopify, and OAuth runs before Gluons knows who they are. The token is
sealed against a short-lived install record, and the browser that finished OAuth gets a
one-time claim cookie. The owner then signs in to Gluons and chooses the business that gets the
store. Nothing acts on the store until that owner also confirms it and signs a policy.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, or_, select

from gluons import vault
from gluons.actions import ProviderError
from gluons.authz import Principal
from gluons.commerce import service, shopify
from gluons.config import get_settings
from gluons.core import GluonsError, constant_time_equals, new_id, new_secret_token, not_found, token_hash, utcnow
from gluons.db.models import Connection, ShopifyInstall
from gluons.db.session import public_tx, system_tx, tenant_tx

AUTHORIZE_WINDOW = timedelta(minutes=10)
CLAIM_WINDOW = timedelta(minutes=30)
CLAIM_COOKIE = "gluons_shopify_claim"
CLAIM_COOKIE_PATH = "/v1/commerce/shopify/installs"
BEGIN_COOKIE = "gluons_shopify_begin"
BEGIN_COOKIE_PATH = "/v1/commerce/shopify/callback"
# Reading the catalog and orders is enough to start. Writes need a pilot or later consent.
CAPABILITIES = ("read_catalog", "monitor_orders")


def scopes() -> list[str]:
    return sorted({scope for capability in CAPABILITIES for scope in service.CAPABILITIES[capability]})


def _scope(install_id: str) -> str:
    return f"shopify_install:{install_id}"


def begin(parameters: list[tuple[str, str]]) -> tuple[str, str | None]:
    """The app URL Shopify opens. Returns where to send the browser next and, when OAuth
    starts, a secret for that browser to present at the callback."""
    if not shopify.configured():
        raise GluonsError("CONFIGURATION_REQUIRED", "Configure the Shopify app credentials.", status=409)
    shop = shopify.verify_query(parameters)["shop"]
    with system_tx() as session:
        live = session.scalar(select(Connection.id).where(
            Connection.provider == "shopify", Connection.status == "connected", Connection.disconnected_at.is_(None),
            Connection.metadata_json["shop"].as_string() == shop).limit(1))
    if live:
        # Opening the app again must not mint a token: a new one retires the token in use.
        return f"{get_settings().public_web_url.rstrip('/')}/connections?shopify=connected", None
    state, browser = new_secret_token(), new_secret_token()
    with public_tx() as session:
        session.add(ShopifyInstall(id=new_id("shi"), shop=shop, state_hash=token_hash(state), browser_hash=token_hash(browser),
                                   scopes=scopes(), expires_at=utcnow() + AUTHORIZE_WINDOW))
    return shopify.authorize_url(shop, scopes(), state), browser


def owns_state(state: str) -> bool:
    with public_tx() as session:
        return session.scalar(select(ShopifyInstall.id).where(ShopifyInstall.state_hash == token_hash(state))) is not None


def complete(parameters: list[tuple[str, str]], browser: str) -> tuple[str, str]:
    """The OAuth callback for an install. Returns the install and its one-time claim secret."""
    params = shopify.verify_query(parameters, required=("state", "code"))
    failure: GluonsError | None = None
    result: tuple[str, str] = ("", "")
    with public_tx() as session:
        row = session.scalar(select(ShopifyInstall).where(ShopifyInstall.state_hash == token_hash(params["state"])).with_for_update())
        if (row is None or row.status != "authorizing" or row.expires_at <= utcnow() or row.shop != params["shop"]
                or not browser or not row.browser_hash or not constant_time_equals(token_hash(browser), row.browser_hash)):
            raise GluonsError("OAUTH_STATE_INVALID", "This Shopify install expired or was already used. Open Gluons from Shopify again.", status=400)
        row.status = "failed"
        try:
            credential = shopify.exchange(row.shop, params["code"])
            identity = shopify.identity(row.shop, credential["access_token"])
            if not set(row.scopes) <= set(identity["scopes"]):
                raise GluonsError("SCOPE_INSUFFICIENT", "Shopify did not grant the access Gluons asked for.", status=409)
            secret = new_secret_token()
            row.credential_sealed = vault.seal_global(_scope(row.id), json.dumps(credential))
            row.shop_identity = identity
            row.claim_hash = token_hash(secret)
            row.status = "authorized"
            row.expires_at = utcnow() + CLAIM_WINDOW
            result = (row.id, secret)
        except (GluonsError, ProviderError) as exc:
            failure = exc if isinstance(exc, GluonsError) else GluonsError("EXTERNAL_UNAVAILABLE", "Shopify could not complete the install.", status=502)
            row.failure_reason = failure.message
    if failure:
        raise failure
    return result


def _claimable(row: ShopifyInstall | None, secret: str) -> ShopifyInstall:
    if (row is None or row.status != "authorized" or row.expires_at <= utcnow() or not row.claim_hash
            or not secret or not constant_time_equals(token_hash(secret), row.claim_hash)):
        # The same answer for every case, so the endpoint cannot be used to probe installs.
        raise not_found("Shopify install")
    return row


def _view(row: ShopifyInstall) -> dict[str, Any]:
    identity = row.shop_identity
    return {"install_id": row.id, "shop": row.shop, "store_name": identity.get("name"), "scopes": identity.get("scopes", []),
            "environment": "sandbox" if (identity.get("plan") or {}).get("partnerDevelopment") is True else "production",
            "expires_at": row.expires_at.isoformat(), "status": row.status}


def preview(principal: Principal, install_id: str, secret: str) -> dict[str, Any]:
    service._browser(principal)
    with public_tx() as session:
        return _view(_claimable(session.get(ShopifyInstall, install_id), secret))


def claim(principal: Principal, install_id: str, secret: str) -> dict[str, Any]:
    """Binds the store to the owner's current business. Confirmation and grants stay separate."""
    service._browser(principal)
    with public_tx() as session:
        row = _claimable(session.get(ShopifyInstall, install_id, with_for_update=True), secret)
        credential = json.loads(vault.open_global(_scope(row.id), row.credential_sealed or ""))
        with tenant_tx(principal.tenant_id, principal.user_id) as business:
            connection = service.store_connection(business, principal, shop=row.shop, identity=row.shop_identity,
                                                  credential=credential, distribution="shopify_install")
            connection_id = connection.id
        row.status, row.claimed_tenant_id, row.connection_id, row.claimed_at = "claimed", principal.tenant_id, connection_id, utcnow()
        row.credential_sealed, row.claim_hash = None, None
        return {**_view(row), "connection_id": connection_id, "account_confirmed": False,
                "next_step": "Confirm the store on the Connections page. No automation can use it until you also approve a policy."}


def purge_stale() -> int:
    """An unclaimed install still holds a live token, so it is dropped soon after its window."""
    now = utcnow()
    with system_tx() as session:
        return session.execute(delete(ShopifyInstall).where(or_(
            (ShopifyInstall.status != "claimed") & (ShopifyInstall.expires_at < now - timedelta(hours=1)),
            (ShopifyInstall.status == "claimed") & (ShopifyInstall.claimed_at < now - timedelta(days=30))))).rowcount
