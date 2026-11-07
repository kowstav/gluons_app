"""How owners connect Shopify, Gmail and WhatsApp, and how lapsing access is caught.

Real PostgreSQL and HTTP routes. Every provider is a labelled double: these prove Gluons'
handling only, never that Shopify, Google or Meta behave this way.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gluons import vault
from gluons.actions import ProviderError
from gluons.api.app import create_app
from gluons.commerce import install, service, shopify
from gluons.config import get_settings
from gluons.connections import health
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import Connection, Notification, ShopifyInstall, WebhookInbox
from gluons.db.session import public_tx, tenant_tx
from helpers import World
from test_api_foundation import signed_in_org

SECRET = "provider-double-secret"


@pytest.fixture
def shop(monkeypatch):
    monkeypatch.setattr(get_settings(), "shopify_client_id", "client")
    monkeypatch.setattr(get_settings(), "shopify_client_secret", SECRET)
    return f"store-{new_id('s').split('_')[1][:12]}.myshopify.com"


def signed(params: dict) -> list[tuple[str, str]]:
    params = {"timestamp": str(int(utcnow().timestamp())), **params}
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return [*params.items(), ("hmac", hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest())]


def provider_double(monkeypatch, shop: str, *, scopes=("read_products", "read_orders"), shop_gid: str | None = None):
    gid = shop_gid or f"gid://shopify/Shop/{abs(hash(shop)) % 10**9}"
    monkeypatch.setattr(shopify, "exchange", lambda s, code: {"access_token": "access-double", "refresh_token": "refresh-double",
                                                              "expires_in": 3600, "refresh_token_expires_in": 7776000})
    monkeypatch.setattr(shopify, "identity", lambda s, token: {"id": gid, "name": "Double Store", "myshopifyDomain": s, "currencyCode": "INR",
                                                               "plan": {"partnerDevelopment": True}, "scopes": list(scopes)})


def started(shop: str) -> tuple[str, str]:
    target, browser = install.begin(signed({"shop": shop}))
    return parse_qs(urlsplit(target).query)["state"][0], browser


def installed(monkeypatch, shop: str) -> tuple[str, str]:
    provider_double(monkeypatch, shop)
    state, browser = started(shop)
    return install.complete(signed({"shop": shop, "state": state, "code": "code-double"}), browser)


def browser(world):
    return replace(world.owner, session_id=f"ses_{new_id('b')}")


def test_install_starts_from_a_signed_shopify_request_only(shop):
    with pytest.raises(GluonsError, match="signature"):
        install.begin([("shop", shop), ("timestamp", str(int(utcnow().timestamp()))), ("hmac", "0" * 64)])
    target = urlsplit(install.begin(signed({"shop": shop}))[0])
    query = parse_qs(target.query)
    assert target.netloc == shop and target.path == "/admin/oauth/authorize"
    assert query["redirect_uri"] == [f"{get_settings().public_api_url}/v1/commerce/shopify/callback"]
    assert set(query["scope"][0].split(",")) == {"read_products", "read_orders"}


def test_stale_or_replayed_app_requests_are_refused(shop):
    old = {"shop": shop, "timestamp": str(int((utcnow() - timedelta(minutes=10)).timestamp()))}
    message = "&".join(f"{k}={v}" for k, v in sorted(old.items()))
    with pytest.raises(GluonsError, match="expired"):
        install.begin([*old.items(), ("hmac", hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest())])


def test_claim_binds_the_store_to_the_signed_in_business_once(world, shop, monkeypatch):
    install_id, secret = installed(monkeypatch, shop)
    owner = browser(world)
    with pytest.raises(GluonsError, match="not found"):
        install.claim(owner, install_id, "wrong-secret")
    preview = install.preview(owner, install_id, secret)
    assert preview["shop"] == shop and preview["environment"] == "sandbox"
    result = install.claim(owner, install_id, secret)
    assert result["account_confirmed"] is False
    with tenant_tx(world.tenant_id) as session:
        connection = session.get(Connection, result["connection_id"])
        assert connection.metadata_json["app_distribution"] == "shopify_install"
        assert connection.metadata_json["refresh_token_expires_at"]
        assert vault.read(session, world.tenant_id, connection.credential_ref)["access_token"] == "access-double"
    with public_tx() as session:
        row = session.get(ShopifyInstall, install_id)
        assert row.credential_sealed is None and row.claim_hash is None and row.claimed_tenant_id == world.tenant_id
    with pytest.raises(GluonsError, match="not found"):
        install.claim(owner, install_id, secret)


def test_an_assistant_or_sessionless_principal_cannot_claim(world, shop, monkeypatch):
    install_id, secret = installed(monkeypatch, shop)
    for principal in (world.owner, replace(world.owner, kind="assistant", session_id="ses_x")):
        with pytest.raises(GluonsError):
            install.claim(principal, install_id, secret)


def test_a_store_feeds_one_business(world, shop, monkeypatch):
    install_id, secret = installed(monkeypatch, shop)
    install.claim(browser(world), install_id, secret)
    other = World.create("Other Foods")
    with tenant_tx(world.tenant_id) as session:
        session.execute(Connection.__table__.update().where(Connection.tenant_id == world.tenant_id).values(status="needs_reauth"))
    second_id, second_secret = installed(monkeypatch, shop)
    with pytest.raises(GluonsError, match="another Gluons business"):
        install.claim(browser(other), second_id, second_secret)


def test_reopening_the_app_does_not_mint_a_token_that_retires_the_live_one(world, shop, monkeypatch):
    install_id, secret = installed(monkeypatch, shop)
    install.claim(browser(world), install_id, secret)
    assert install.begin(signed({"shop": shop})) == (f"{get_settings().public_web_url}/connections?shopify=connected", None)


def test_only_the_browser_that_opened_the_app_can_finish_oauth(world, shop, monkeypatch):
    provider_double(monkeypatch, shop)
    state, browser = started(shop)
    callback = signed({"shop": shop, "state": state, "code": "code-double"})
    for wrong in ("", "another-browser"):
        with pytest.raises(GluonsError, match="expired or was already used"):
            install.complete(callback, wrong)
    assert install.complete(callback, browser)[0].startswith("shi_")


def test_expired_installs_cannot_be_claimed_and_are_purged(world, shop, monkeypatch):
    install_id, secret = installed(monkeypatch, shop)
    with public_tx() as session:
        session.get(ShopifyInstall, install_id).expires_at = utcnow() - timedelta(hours=2)
    with pytest.raises(GluonsError, match="not found"):
        install.claim(browser(world), install_id, secret)
    install.purge_stale()
    with public_tx() as session:
        assert session.get(ShopifyInstall, install_id) is None


def test_http_install_sets_a_browser_bound_claim_cookie(world, shop, monkeypatch):
    provider_double(monkeypatch, shop)
    client = TestClient(create_app())
    entry = client.get("/v1/commerce/shopify/install", params=dict(signed({"shop": shop})), follow_redirects=False)
    assert entry.status_code == 303
    state = parse_qs(urlsplit(entry.headers["location"]).query)["state"][0]
    back = client.get("/v1/commerce/shopify/callback", params=dict(signed({"shop": shop, "state": state, "code": "c"})), follow_redirects=False)
    assert back.status_code == 303 and "/connect/shopify/shi_" in back.headers["location"]
    cookie = back.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Path=/v1/commerce/shopify/installs" in cookie
    install_id = back.headers["location"].rsplit("/", 1)[1]
    lured = TestClient(create_app())
    lured_back = lured.get("/v1/commerce/shopify/callback", params=dict(signed({"shop": shop, "state": state, "code": "c"})), follow_redirects=False)
    assert "connection_error=" in lured_back.headers["location"]
    headers = signed_in_org(client, world)
    claimed = client.post(f"/v1/commerce/shopify/installs/{install_id}/claim", headers=headers)
    assert claimed.status_code == 200, claimed.text
    stranger = TestClient(create_app())
    assert stranger.post(f"/v1/commerce/shopify/installs/{install_id}/claim", headers=signed_in_org(stranger, World.create("Stranger"))).status_code == 404


def test_provider_redirect_failures_return_to_gluons_web(world, shop):
    client = TestClient(create_app())
    response = client.get("/v1/commerce/shopify/callback", params=dict(signed({"shop": shop, "state": "s" * 40, "code": "c"})), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("/connections?connection_error=AUTH_REQUIRED")
    signed_in_org(client, world)
    gmail = client.get("/v1/oauth/gmail/callback", params={"state": "x" * 40, "error": "access_denied"}, follow_redirects=False)
    assert gmail.status_code == 303 and gmail.headers["location"].endswith("connection_error=OAUTH_STATE_INVALID")


def webhook(topic: str, body: dict, *, delivery: str | None = None, shop_header: str = "") -> dict:
    raw = json.dumps(body).encode()
    signature = base64.b64encode(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()).decode()
    return service.app_webhook(raw, signature, delivery or new_id("whd").replace("_", "-"), topic, shop_header)


def connected(world, shop, monkeypatch) -> str:
    install_id, secret = installed(monkeypatch, shop)
    return install.claim(browser(world), install_id, secret)["connection_id"]


def test_uninstall_closes_the_connection_and_tells_the_owner(world, shop, monkeypatch):
    connection_id = connected(world, shop, monkeypatch)
    delivery = new_id("whd").replace("_", "-")
    result = webhook("app/uninstalled", {"id": 548380009, "name": "Double Store", "myshopify_domain": shop, "domain": shop}, delivery=delivery)
    assert result["outcome"] == {"connections_closed": 1}
    assert webhook("app/uninstalled", {"id": 548380009, "myshopify_domain": shop}, delivery=delivery)["status"] == "duplicate"
    with tenant_tx(world.tenant_id) as session:
        connection = session.get(Connection, connection_id)
        assert connection.status == "disconnected" and connection.credential_ref is None
        assert session.scalar(select(func.count()).select_from(Notification).where(Notification.kind == "connection_removed")) == 1


def test_a_customer_body_cannot_be_replayed_as_a_store_erasure(world, shop, monkeypatch):
    connected(world, shop, monkeypatch)
    request = {"shop_id": 1, "shop_domain": shop, "customer": {"id": 1}, "orders_requested": [1], "data_request": {"id": 9}}
    for topic in ("shop/redact", "customers/redact"):
        with pytest.raises(GluonsError, match="does not match"):
            webhook(topic, request)
    with pytest.raises(GluonsError, match="signature"):
        service.app_webhook(b"{}", "bad", "delivery-123456", "shop/redact", "")


def test_store_erasure_waits_for_uninstall_and_customer_erasure_removes_order_evidence(world, shop, monkeypatch):
    connection_id = connected(world, shop, monkeypatch)
    with tenant_tx(world.tenant_id) as session:
        session.add(WebhookInbox(id=new_id("whi"), tenant_id=world.tenant_id, provider="shopify", event_id=f"{connection_id}:x", connection_id=connection_id,
                                 event_type="orders/paid", payload_hash="h", status="verified_evidence",
                                 payload={"provider_evidence": {"provider_record": {"id": "gid://shopify/Order/77"}}}))
    erase = {"shop_id": 1, "shop_domain": shop}
    assert webhook("shop/redact", erase)["outcome"] == {"connections_redacted": 0}
    request = webhook("customers/data_request", {**erase, "customer": {"id": 5}, "orders_requested": [77, 78], "data_request": {"id": 3}})
    assert request["outcome"]["order_records_held"] == 1
    redact = webhook("customers/redact", {**erase, "customer": {"id": 5}, "orders_to_redact": [77]})
    assert redact["outcome"]["order_records_deleted"] == 1
    webhook("app/uninstalled", {"id": 1, "myshopify_domain": shop})
    assert webhook("shop/redact", erase)["outcome"] == {"connections_redacted": 1}


def test_order_webhooks_at_the_app_endpoint_route_to_the_one_live_store(world, shop, monkeypatch):
    connection_id = connected(world, shop, monkeypatch)
    seen = {}
    monkeypatch.setattr(service, "webhook", lambda cid, raw, sig, wid, topic, s: seen.update(cid=cid) or {"status": "recorded"})
    assert webhook("orders/paid", {"id": 1}, shop_header=shop)["status"] == "recorded" and seen["cid"] == connection_id
    assert webhook("orders/paid", {"id": 1}, shop_header="nobody.myshopify.com")["status"] == "ignored_unknown_store"


def test_shopify_refresh_failures_are_classified_for_the_owner(world, shop, monkeypatch):
    connection_id = connected(world, shop, monkeypatch)
    def answer(status):
        return httpx.MockTransport(lambda request: httpx.Response(status, json={"error": "invalid_request"}))
    with tenant_tx(world.tenant_id) as session:
        row = session.get(Connection, connection_id)
        with pytest.raises(ProviderError) as transient:
            shopify.get_credential(session, world.tenant_id, row, force_refresh=True, transport=answer(503))
        assert transient.value.kind == "retryable"
        with pytest.raises(GluonsError) as dead:
            shopify.get_credential(session, world.tenant_id, row, force_refresh=True, transport=answer(401))
        assert dead.value.code == "CONNECTION_REAUTH_REQUIRED"
        fresh = httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "a2", "refresh_token": "r2", "expires_in": 3600,
                                                                               "refresh_token_expires_in": 86400}))
        assert shopify.get_credential(session, world.tenant_id, row, force_refresh=True, transport=fresh)["access_token"] == "a2"
        assert row.metadata_json["refresh_token_expires_at"] < (utcnow() + timedelta(days=2)).isoformat()


def test_health_check_flags_lapsed_access_once_and_warns_before_whatsapp_expiry(world, shop, monkeypatch):
    connection_id = connected(world, shop, monkeypatch)
    with tenant_tx(world.tenant_id) as session:
        row = session.get(Connection, connection_id)
        row.metadata_json = {**row.metadata_json, "refresh_token_expires_at": (utcnow() + timedelta(days=3)).isoformat()}
        wa = Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="whatsapp_business", external_account_id=new_id("p"), account_display="+91 98765 43210",
                        status="connected", created_by=world.user_id, token_expires_at=utcnow() + timedelta(days=2))
        session.add(wa)
    calls = []

    def refused(*args, **kwargs):
        calls.append(kwargs.get("force_refresh"))
        raise GluonsError("CONNECTION_REAUTH_REQUIRED", "Shopify no longer accepts this store's access.", status=409)

    monkeypatch.setattr(shopify, "get_credential", refused)
    assert health.check(world.tenant_id, connection_id, "shopify") == "needs_reauth" and calls == [True]
    assert health.check(world.tenant_id, wa.id, "whatsapp_business") == "warned"
    assert health.check(world.tenant_id, wa.id, "whatsapp_business") == "warned"
    with tenant_tx(world.tenant_id) as session:
        kinds = session.execute(select(Notification.kind)).scalars().all()
        assert kinds.count("connection_token_expiring") == 1 and kinds.count("connection_needs_reauth") == 1
        assert session.get(Connection, connection_id).status == "needs_reauth"


def test_gmail_health_detects_a_revoked_grant(world, monkeypatch):
    with tenant_tx(world.tenant_id) as session:
        secret = vault.store(session, world.tenant_id, "gmail", {"access_token": "a", "refresh_token": "r"})
        row = Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="gmail", external_account_id="sub-1", account_display="owner@example.com",
                         status="connected", credential_ref=secret, created_by=world.user_id, token_expires_at=utcnow() - timedelta(minutes=1))
        session.add(row)
    from gluons.connections import google
    monkeypatch.setattr(google, "refresh", lambda credential: (_ for _ in ()).throw(
        GluonsError("CONNECTION_REAUTH_REQUIRED", "Google could not verify this connection. Reconnect in Gluons.", status=409)))
    assert health.check(world.tenant_id, row.id, "gmail") == "needs_reauth"


def test_public_endpoints_are_rate_limited_per_address(monkeypatch):
    from gluons import ratelimit

    monkeypatch.setattr(get_settings(), "rate_limits_enabled", True)
    monkeypatch.setitem(ratelimit.LIMITS, "login", (3, 60))
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 2)
    client = TestClient(create_app())
    address = f"203.0.113.{int(new_id('x').split('_')[1][:2], 16)}"
    body = {"email": "nobody@example.com", "password": "not the password at all"}
    codes = [client.post("/v1/auth/login", json=body, headers={"X-Forwarded-For": f"10.0.0.1, {address}, 198.51.100.7"}).status_code for _ in range(4)]
    assert codes[:3] == [401, 401, 401] and codes[3] == 429
    # A spoofed leading entry is ignored: the trusted proxy's entry decides.
    assert client.post("/v1/auth/login", json=body, headers={"X-Forwarded-For": f"10.9.9.9, {address}, 198.51.100.7"}).status_code == 429
    assert client.post("/v1/auth/login", json=body, headers={"X-Forwarded-For": "203.0.113.250, 198.51.100.7"}).status_code == 401
