"""Real tenant storage and provider-double Shopify contracts; no live merchant claims."""
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
from sqlalchemy import select

from gluons import approvals, policy, vault
from gluons.actions import ConnectorContext, ProviderError
from gluons.commerce import service, shopify
from gluons.config import get_settings
from gluons.connectors.shopify import ProductDraft, ProductDraftConnector
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import ActionReceipt, Automation, Connection, ConnectionSession, LaunchPlan, WebhookInbox
from gluons.db.session import tenant_tx
from gluons.net import SideEffectBlocked, write_permit
from helpers import World

SHOP = "gluons-pilot.myshopify.com"


@pytest.fixture(autouse=True)
def fresh_shop():
    """A store feeds one business, so each test needs a store of its own."""
    global SHOP
    SHOP = f"gluons-pilot-{new_id('s').split('_')[1][:12]}.myshopify.com"
INPUT = {"title": "Verified cotton shirt", "vendor": "Authorized Supplier", "description_text": "Cotton shirt",
         "source_evidence": ["https://supplier.example/catalog/shirt"]}


@pytest.fixture
def owner(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "shopify_client_id", "client")
    monkeypatch.setattr(get_settings(), "shopify_client_secret", "provider-double-secret")
    monkeypatch.setattr(get_settings(), "shopify_pilot_shops", SHOP)
    return replace(world.owner, session_id="browser-session")


def signed(parameters):
    message = "&".join(f"{key}={value}" for key, value in sorted(parameters.items()) if key != "hmac")
    return [*parameters.items(), ("hmac", hmac.new(get_settings().shopify_client_secret.encode(), message.encode(), hashlib.sha256).hexdigest())]


def pending(owner):
    result = service.begin_session(owner, shop=SHOP, capabilities=["stage_catalog", "monitor_orders"])
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    return result["session_id"], {"shop": SHOP, "state": query["state"][0], "code": "test-code", "timestamp": str(int(utcnow().timestamp()))}


def mock_oauth(monkeypatch, scopes=None):
    monkeypatch.setattr(shopify, "exchange", lambda shop, code: {"access_token": "access-provider-double", "refresh_token": "refresh-provider-double", "expires_in": 3600})
    monkeypatch.setattr(shopify, "identity", lambda shop, token: {"id": "gid://shopify/Shop/123", "name": "Pilot Store", "myshopifyDomain": SHOP,
        "currencyCode": "USD", "plan": {"partnerDevelopment": True}, "scopes": scopes or ["read_products", "write_products", "read_orders"]})


def connected(world, owner, monkeypatch):
    mock_oauth(monkeypatch)
    session_id, params = pending(owner)
    result = service.callback(owner, signed(params))
    return result["connection_id"]


@pytest.mark.parametrize("domain", ["https://shop.myshopify.com", "shop.myshopify.com/", "shop.myshopify.com:443", "shop.myshopify.com.evil.com", "user@shop.myshopify.com", "localhost", "127.0.0.1", "UPPER.myshopify.com"])
def test_shop_domains_cannot_escape_fixed_provider_boundary(domain):
    with pytest.raises(GluonsError):
        shopify.shop_domain(domain)


def test_manual_shop_field_is_restricted_to_reviewed_pilot(owner):
    with pytest.raises(GluonsError, match="operator-approved"):
        service.begin_session(owner, shop="another.myshopify.com", capabilities=["read_catalog"])


def test_missing_credentials_does_not_fake_authorization(owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "shopify_client_secret", None)
    result = service.begin_session(owner, shop=SHOP, capabilities=["read_catalog"])
    assert result["status"] == "needs_configuration" and "authorization_url" not in result


def test_shopify_oauth_requires_signed_owner_session_and_is_single_use(world, owner, monkeypatch):
    mock_oauth(monkeypatch)
    session_id, parameters = pending(owner)
    with pytest.raises(GluonsError, match="another session"):
        service.callback(replace(owner, session_id="different"), signed(parameters))
    result = service.callback(owner, signed(parameters))
    assert result["status"] == "authorized"
    assert "access-provider-double" not in str(result) and "refresh-provider-double" not in str(result)
    with tenant_tx(world.tenant_id) as session:
        connection = session.get(Connection, result["connection_id"])
        assert connection.environment == "sandbox" and connection.account_confirmed_at is None
        assert vault.read(session, world.tenant_id, connection.credential_ref)["refresh_token"] == "refresh-provider-double"
    with pytest.raises(GluonsError):
        service.callback(owner, signed(parameters))
    with pytest.raises(GluonsError):
        service.session_status(replace(World.create().owner, session_id="browser"), session_id)


@pytest.mark.parametrize("fault", ["hmac", "state", "expired", "scope", "duplicate"])
def test_shopify_negative_oauth_cases_make_no_connection(world, owner, monkeypatch, fault):
    mock_oauth(monkeypatch, scopes=["read_products"] if fault == "scope" else None)
    session_id, parameters = pending(owner)
    if fault == "state":
        parameters["state"] += "tampered"
    if fault == "expired":
        with tenant_tx(world.tenant_id) as session:
            session.get(ConnectionSession, session_id).expires_at = utcnow() - timedelta(seconds=1)
    values = signed(parameters)
    if fault == "hmac":
        values[-1] = ("hmac", "0" * 64)
    if fault == "duplicate":
        values.append(("shop", SHOP))
    with pytest.raises(GluonsError):
        service.callback(owner, values)
    with tenant_tx(world.tenant_id) as session:
        assert list(session.scalars(select(Connection))) == []


def test_refresh_rotates_encrypted_token_without_exposing_credentials(world, owner, monkeypatch):
    connection_id = connected(world, owner, monkeypatch)
    calls = []
    def token_request(shop, method, path, **kwargs):
        calls.append(kwargs["data"]["grant_type"])
        return {"access_token": "rotated-access", "refresh_token": "rotated-refresh", "expires_in": 3600}
    monkeypatch.setattr(shopify, "request", token_request)
    with tenant_tx(world.tenant_id) as session:
        connection = session.get(Connection, connection_id)
        connection.token_expires_at = utcnow() - timedelta(seconds=1)
        credential = shopify.get_credential(session, world.tenant_id, connection)
        assert credential["access_token"] == "rotated-access"
        assert vault.read(session, world.tenant_id, connection.credential_ref)["refresh_token"] == "rotated-refresh"
    assert calls == ["refresh_token"]


def product_provider():
    writes, state = [], {}
    def provider(request):
        body = json.loads(request.content)
        if "mutation" in body["query"]:
            writes.append(body)
            product = body["variables"]["product"]
            state.update(id="gid://shopify/Product/123", title=product["title"], vendor=product["vendor"], handle=product["handle"], status=product["status"], description="Cotton shirt")
            return httpx.Response(200, json={"data": {"productCreate": {"product": state, "userErrors": []}}})
        return httpx.Response(200, json={"data": {"product": state}})
    return writes, state, httpx.MockTransport(provider)


def test_draft_product_permit_and_independent_readback():
    writes, state, transport = product_provider()
    context = ConnectorContext("tenant", "connection", "shopify", "sandbox", "gid://shopify/Shop/123", {"shop": SHOP}, lambda: {"access_token": "double"}, transport)
    connector, payload = ProductDraftConnector(), ProductDraft(**INPUT)
    with pytest.raises(SideEffectBlocked):
        connector.execute(context, payload, "key")
    assert writes == []
    with write_permit("approved-action"):
        result = connector.execute(context, payload, "key")
    assert writes[0]["variables"]["product"]["status"] == "DRAFT"
    assert connector.verify(context, payload, result).matches is True
    state["status"] = "ACTIVE"
    assert connector.verify(context, payload, result).matches is False


def test_missing_product_lookup_never_blindly_recreates():
    context = ConnectorContext("tenant", "connection", "shopify", "sandbox", "id", {"shop": SHOP}, lambda: {"access_token": "double"},
                               httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {"products": {"nodes": []}}})))
    with pytest.raises(ProviderError) as error:
        ProductDraftConnector().lookup(context, ProductDraft(**INPUT), "key")
    assert error.value.kind == "ambiguous"


def test_stage_gateway_blocks_missing_action_grant_and_shadow_never_writes(world, owner, monkeypatch):
    connection_id = connected(world, owner, monkeypatch)
    automation = world.order_automation()
    result = service.stage_product(owner, automation_id=automation["id"], connection_id=connection_id, payload=INPUT, idempotency_key="shadow-draft-key")
    assert result.status == "preview" and result.verified is False
    with tenant_tx(world.tenant_id) as session:
        row = session.get(Automation, automation["id"])
        row.mode, row.status = "live", "active"
        session.get(Connection, connection_id).account_confirmed_at = utcnow()
    result = service.stage_product(owner, automation_id=automation["id"], connection_id=connection_id, payload=INPUT, idempotency_key="denied-draft-key")
    assert result.status == "denied" and "NO_ACTIVE_POLICY" in result.reasons


def test_webhook_signature_dedup_and_authoritative_order_account(world, owner, monkeypatch):
    connection_id = connected(world, owner, monkeypatch)
    monkeypatch.setattr(shopify, "order_status", lambda shop, token, gid: {"provider_record": {"id": gid}, "payment_confirmed": True, "is_test": True, "delivered": False})
    raw = json.dumps({"admin_graphql_api_id": "gid://shopify/Order/42"}).encode()
    signature = base64.b64encode(hmac.new(get_settings().shopify_client_secret.encode(), raw, hashlib.sha256).digest()).decode()
    with pytest.raises(GluonsError, match="signature"):
        service.webhook(connection_id, raw, "forged", "delivery-1", "orders/paid", SHOP)
    result = service.webhook(connection_id, raw, signature, "delivery-1", "orders/paid", SHOP)
    assert result["status"] == "recorded" and result["evidence"]["delivered"] is False
    assert service.webhook(connection_id, raw, signature, "delivery-1", "orders/paid", SHOP)["status"] == "duplicate"
    assert service.webhook(connection_id, raw, signature, "changed-unsigned-header", "orders/paid", SHOP)["status"] == "duplicate"
    with tenant_tx(world.tenant_id) as session:
        assert len(list(session.scalars(select(WebhookInbox)))) == 1


def test_provider_payment_setup_is_not_kyc_or_live_checkout(world, owner, monkeypatch):
    from gluons import launch
    connection_id = connected(world, owner, monkeypatch)
    plan = launch.create_plan(owner, goal="Owner-authorized shop")
    with tenant_tx(world.tenant_id) as session:
        connection = session.get(Connection, connection_id)
        connection.account_confirmed_at = utcnow()
        connection.granted_scopes = [*connection.granted_scopes, "read_shopify_payments_accounts"]
        session.get(LaunchPlan, plan["id"]).state = {"store_connection_id": connection_id}
    monkeypatch.setattr(shopify, "payment_status", lambda *args: {"setup_completed": True, "kyc_verified": False, "live_payments_verified": False})
    report = service.readiness(owner, plan["id"])
    assert report["payments"]["setup_completed"] is True
    assert report["payments"]["kyc_verified"] is False and report["store_live"] is False
    with pytest.raises(GluonsError):
        service.readiness(World.create().owner, plan["id"])


def test_supplier_inquiry_cannot_send_without_precise_owner_approval(world, owner):
    from gluons import launch
    plan = launch.create_plan(owner, goal="Find a supplier")
    supplier = service.add_supplier(owner, plan_id=plan["id"], name="Supplier", email="supplier@example.com", source_url="https://supplier.example/about")
    automation = world.order_automation()
    with tenant_tx(world.tenant_id) as session:
        connection = Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="gmail", external_account_id="mailbox", account_display="Owner Gmail", created_by=world.user_id)
        session.add(connection)
        session.flush()
        connection_id = connection.id
    result = service.supplier_inquiry(owner, supplier_id=supplier["id"], automation_id=automation["id"], connection_id=connection_id,
                                     subject="Request for quote", body="Please quote these products.", represented_entity="Owner Company", idempotency_key="supplier-contact-1")
    assert result.status == "waiting_approval"
    with tenant_tx(world.tenant_id) as session:
        approval = session.get(approvals.Approval, result.approval_id)
        assert approval.required_role == "owner"
        assert approval.records_after_preview["to"] == "supplier@example.com"
        assert approval.records_after_preview["represented_entity"] == "Owner Company"


def test_signed_draft_action_writes_once_and_requires_matching_scope(world, owner, monkeypatch):
    connection_id = connected(world, owner, monkeypatch)
    writes, state, transport = product_provider()
    original_request = shopify.request
    monkeypatch.setattr(shopify, "request", lambda *args, **kwargs: original_request(*args, **{**kwargs, "transport": transport}))
    automation = world.order_automation()
    with tenant_tx(world.tenant_id) as session:
        row = session.get(Automation, automation["id"])
        row.mode, row.status = "live", "active"
        session.get(Connection, connection_id).account_confirmed_at = utcnow()
        proposed = policy.propose(session, owner, row.id, {"mode": "live", "allowed_actions": ["shopify.product.create_draft"], "connection_ids": [connection_id]})
        policy.sign(session, owner, proposed.id)
    first = service.stage_product(owner, automation_id=automation["id"], connection_id=connection_id, payload=INPUT, idempotency_key="approved-stage-key")
    second = service.stage_product(owner, automation_id=automation["id"], connection_id=connection_id, payload=INPUT, idempotency_key="approved-stage-key")
    assert first.verified is True and second.duplicate is True and len(writes) == 1
    with tenant_tx(world.tenant_id) as session:
        session.get(Connection, connection_id).granted_scopes = ["read_products"]
    refused = service.stage_product(owner, automation_id=automation["id"], connection_id=connection_id, payload=INPUT, idempotency_key="missing-scope-key")
    assert refused.status == "denied" and "PROVIDER_SCOPE_MISSING" in refused.reasons
    assert len(writes) == 1


def test_approved_supplier_inquiry_uses_shared_gmail_once_after_action_grant(world, owner, monkeypatch):
    from gluons import launch
    from gluons.connectors import gmail
    from gluons.net import GuardedClient
    writes = []
    def provider(request):
        if request.method == "POST":
            writes.append(json.loads(request.content)["raw"])
            return httpx.Response(200, json={"id": "mail-1", "threadId": "thread-1"})
        return httpx.Response(200, json={"id": "mail-1", "raw": writes[0], "labelIds": ["SENT"]})
    transport = httpx.MockTransport(provider)
    monkeypatch.setattr(gmail, "GuardedClient", lambda hosts, **kwargs: GuardedClient(hosts, **{**kwargs, "transport": transport}))
    plan = launch.create_plan(owner, goal="Source a product")
    supplier = service.add_supplier(owner, plan_id=plan["id"], name="Supplier", email="supplier@example.com", source_url="https://supplier.example")
    automation = world.order_automation()
    with tenant_tx(world.tenant_id) as session:
        connection = Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="gmail", external_account_id="mailbox", account_display="owner@example.com",
            account_confirmed_at=utcnow(), granted_scopes=[gmail.GMAIL_READ_SCOPE, gmail.GMAIL_SEND_SCOPE], metadata_json={"account_email": "owner@example.com"}, created_by=world.user_id)
        connection.credential_ref = vault.store(session, world.tenant_id, "gmail", {"access_token": "double"})
        session.add(connection)
        session.flush()
        connection_id = connection.id
        row = session.get(Automation, automation["id"])
        row.mode, row.status = "live", "active"
    arguments = {"supplier_id": supplier["id"], "automation_id": automation["id"], "connection_id": connection_id,
        "subject": "Quote request", "body": "Please quote one unit.", "represented_entity": "Owner Company", "idempotency_key": "supplier-approved-key"}
    waiting = service.supplier_inquiry(owner, **arguments)
    assert waiting.status == "waiting_approval" and writes == []
    with tenant_tx(world.tenant_id) as session:
        approvals.decide(session, owner, waiting.approval_id, "approve")
    blocked = service.supplier_inquiry(owner, **arguments)
    assert blocked.status == "denied" and writes == []
    with tenant_tx(world.tenant_id) as session:
        proposed = policy.propose(session, owner, automation["id"], {"mode": "live", "allowed_actions": ["commerce.supplier.inquiry"], "connection_ids": [connection_id]})
        policy.sign(session, owner, proposed.id)
    sent = service.supplier_inquiry(owner, **arguments)
    duplicate = service.supplier_inquiry(owner, **arguments)
    assert sent.verified is True and duplicate.duplicate is True and len(writes) == 1
    assert b"Sent on behalf of Owner Company." in base64.urlsafe_b64decode(writes[0])


def test_publication_is_precisely_owner_approved_and_verified_once(world, owner, monkeypatch):
    from gluons import launch
    writes, state = [], {"published": False}
    connection_id = connected(world, owner, monkeypatch)
    original_request = shopify.request
    def provider(request):
        query = json.loads(request.content)["query"]
        if query.startswith("mutation"):
            writes.append(query)
            state["published"] = True
            return httpx.Response(200, json={"data": {"publishablePublish": {"userErrors": []}}})
        return httpx.Response(200, json={"data": {"product": {"id": "gid://shopify/Product/123", "title": "Approved product", "status": "ACTIVE", "publishedOnPublication": state["published"]}, "publication": {"id": "gid://shopify/Publication/7", "name": "Owner channel"}}})
    transport = httpx.MockTransport(provider)
    monkeypatch.setattr(shopify, "request", lambda *args, **kwargs: original_request(*args, **{**kwargs, "transport": transport}))
    automation = world.order_automation()
    plan = launch.create_plan(owner, goal="Publish the approved product")
    with tenant_tx(world.tenant_id) as session:
        row = session.get(Automation, automation["id"])
        row.mode, row.status = "live", "active"
        connection = session.get(Connection, connection_id)
        connection.account_confirmed_at = utcnow()
        connection.granted_scopes = ["read_products", "read_publications", "write_publications"]
        session.get(LaunchPlan, plan["id"]).state = {"store_connection_id": connection_id}
        proposed = policy.propose(session, owner, row.id, {"mode": "live", "allowed_actions": ["shopify.product.publish"], "connection_ids": [connection_id]})
        policy.sign(session, owner, proposed.id)
    arguments = {"action": "shopify.product.publish", "automation_id": automation["id"], "connection_id": connection_id, "plan_id": plan["id"],
                 "payload": {"product_id": "gid://shopify/Product/123", "publication_id": "gid://shopify/Publication/7", "expected_title": "Approved product"}, "idempotency_key": "approved-publication-key"}
    waiting = service.execute_commerce_action(owner, **arguments)
    assert waiting.status == "waiting_approval" and writes == []
    with tenant_tx(world.tenant_id) as session:
        approvals.decide(session, owner, waiting.approval_id, "approve")
    published = service.execute_commerce_action(owner, **arguments)
    repeated = service.execute_commerce_action(owner, **arguments)
    assert published.verified is True and repeated.duplicate is True and len(writes) == 1
    with tenant_tx(world.tenant_id) as session:
        saved = session.get(LaunchPlan, plan["id"])
        assert saved.state["published_product_receipts"] == [published.receipt_id]
    assert service.record_provider_fact(owner, plan["id"], published.receipt_id)["whole_store_launch_verified"] is False


def fulfillment_world(world, owner, monkeypatch, fault=None):
    from gluons.connectors.shopify import FulfillmentConnector, RecordFulfillment
    monkeypatch.setattr(get_settings(), "shippo_api_token", "provider-double")
    writes = []
    parameters = {"order_id": "gid://shopify/Order/42", "fulfillment_order_id": "gid://shopify/FulfillmentOrder/1",
                  "line_items": [{"id": "gid://shopify/FulfillmentOrderLineItem/1", "quantity": 2}], "carrier": "usps", "tracking_number": "TRACK123", "shipment_receipt_id": new_id("rcp")}
    if fault != "missing_receipt":
        with tenant_tx(world.tenant_id) as session:
            session.add(ActionReceipt(id=parameters["shipment_receipt_id"], tenant_id=world.tenant_id, action="shipping.shipment.dispatch", mode="supervised", environment="sandbox",
                idempotency_key=new_id("key"), payload_hash="0" * 64, status="succeeded", verified=True,
                readback={"order_id": parameters["order_id"], "tracking_number": parameters["tracking_number"], "carrier": parameters["carrier"], "physical_handoff_verified": True}))
    def provider(request):
        if request.url.host == "api.goshippo.com":
            return httpx.Response(200, json={"carrier": "usps", "tracking_number": "TRACK123", "tracking_status": {"status": "PRE_TRANSIT" if fault == "pre_transit" else "TRANSIT"}})
        query = json.loads(request.content)["query"]
        if query.startswith("mutation"):
            writes.append(query)
            if fault == "dropped_response" and len(writes) == 1:
                return httpx.Response(504, json={"error": "provider double lost response after commit"})
            return httpx.Response(200, json={"data": {"fulfillmentCreate": {"fulfillment": {"id": "gid://shopify/Fulfillment/9"}, "userErrors": []}}})
        if "GluonsFulfillmentGate" in query:
            return httpx.Response(200, json={"data": {"fulfillmentOrder": {"id": parameters["fulfillment_order_id"], "status": "OPEN", "order": {
                "id": "gid://shopify/Order/999" if fault == "wrong_order" else parameters["order_id"], "displayFinancialStatus": "PENDING" if fault == "unpaid" else "PAID",
                "cancelledAt": "2026-11-02T00:00:00Z" if fault == "cancelled" else None, "risk": {"recommendation": "INVESTIGATE" if fault == "fraud" else "ACCEPT"}},
                "lineItems": {"nodes": [{"id": parameters["line_items"][0]["id"], "remainingQuantity": 1 if fault == "quantity" else 3, "lineItem": {"id": "gid://shopify/LineItem/5"}}], "pageInfo": {"hasNextPage": False}}}}})
        if "GluonsFindFulfillment" in query:
            return httpx.Response(200, json={"data": {"order": {"id": parameters["order_id"], "fulfillments": [{"id": "gid://shopify/Fulfillment/9", "status": "SUCCESS", "trackingInfo": [{"number": "TRACK123"}]}]}}})
        return httpx.Response(200, json={"data": {"fulfillment": {"id": "gid://shopify/Fulfillment/9", "status": "SUCCESS", "order": {"id": parameters["order_id"]},
            "trackingInfo": [{"number": "WRONG" if fault == "readback" else "TRACK123"}], "fulfillmentLineItems": {"nodes": [{"quantity": 2, "lineItem": {"id": "gid://shopify/LineItem/5"}}], "pageInfo": {"hasNextPage": False}}}}})
    context = ConnectorContext(world.tenant_id, "con", "shopify", "sandbox", "shop", {"shop": SHOP}, lambda: {"access_token": "double"}, httpx.MockTransport(provider))
    return FulfillmentConnector(), RecordFulfillment(**parameters), context, writes


@pytest.mark.parametrize("fault", ["missing_receipt", "pre_transit", "wrong_order", "unpaid", "cancelled", "fraud", "quantity"])
def test_fulfillment_blocks_unverified_shipment_unpaid_fraud_or_wrong_order(world, owner, monkeypatch, fault):
    connector, payload, context, writes = fulfillment_world(world, owner, monkeypatch, fault)
    with write_permit("approved-owner-action"), pytest.raises(ProviderError):
        connector.execute(context, payload, "key")
    assert writes == []


def test_fulfillment_uses_real_api_contract_and_reports_record_not_delivery(world, owner, monkeypatch):
    connector, payload, context, writes = fulfillment_world(world, owner, monkeypatch)
    with pytest.raises(SideEffectBlocked):
        connector.execute(context, payload, "key")
    assert writes == []
    with write_permit("approved-owner-action"):
        result = connector.execute(context, payload, "key")
    verified = connector.verify(context, payload, result)
    assert verified.matches is True and verified.record["delivered"] is False and len(writes) == 1
    recovered = connector.lookup(context, payload, "key")
    assert recovered.provider_object_id == result.provider_object_id
    assert connector.verify(context, payload, recovered).matches is True


def test_fulfillment_independent_readback_rejects_wrong_tracking(world, owner, monkeypatch):
    connector, payload, context, writes = fulfillment_world(world, owner, monkeypatch, "readback")
    with write_permit("approved-owner-action"):
        result = connector.execute(context, payload, "key")
    assert connector.verify(context, payload, result).matches is False


def test_fulfillment_shadow_returns_preview_without_any_carrier_or_shopify_write(world, owner, monkeypatch):
    connection_id = connected(world, owner, monkeypatch)
    _, payload, _, writes = fulfillment_world(world, owner, monkeypatch)
    automation = world.order_automation()
    result = service.execute_commerce_action(owner, action="shopify.fulfillment.create", automation_id=automation["id"], connection_id=connection_id,
        payload=payload.model_dump(), idempotency_key="shadow-fulfillment-key")
    assert result.status == "preview" and result.verified is False and writes == []


def test_fulfillment_gateway_requires_owner_then_recovers_lost_response_without_duplicate(world, owner, monkeypatch):
    from gluons.connectors import shopify as connector_module
    from gluons.net import GuardedClient
    connection_id = connected(world, owner, monkeypatch)
    _, payload, context, writes = fulfillment_world(world, owner, monkeypatch, "dropped_response")
    original_request = shopify.request
    monkeypatch.setattr(shopify, "request", lambda *args, **kwargs: original_request(*args, **{**kwargs, "transport": context.transport}))
    monkeypatch.setattr(connector_module, "GuardedClient", lambda hosts, **kwargs: GuardedClient(hosts, **{**kwargs, "transport": context.transport}))
    automation = world.order_automation()
    with tenant_tx(world.tenant_id) as session:
        row = session.get(Automation, automation["id"])
        row.mode, row.status = "supervised", "active"
        connection = session.get(Connection, connection_id)
        connection.account_confirmed_at = utcnow()
        connection.granted_scopes = ["read_orders", "read_merchant_managed_fulfillment_orders", "write_merchant_managed_fulfillment_orders"]
        proposed = policy.propose(session, owner, row.id, {"mode": "supervised", "allowed_actions": ["shopify.fulfillment.create"], "connection_ids": [connection_id]})
        policy.sign(session, owner, proposed.id)
    arguments = {"action": "shopify.fulfillment.create", "automation_id": automation["id"], "connection_id": connection_id,
                 "payload": payload.model_dump(), "idempotency_key": "verified-shipment-once"}
    waiting = service.execute_commerce_action(owner, **arguments)
    assert waiting.status == "waiting_approval" and writes == []
    with tenant_tx(world.tenant_id) as session:
        approval = session.get(approvals.Approval, waiting.approval_id)
        assert approval.required_role == "owner"
        approvals.decide(session, owner, approval.id, "approve")
    ambiguous = service.execute_commerce_action(owner, **arguments)
    assert ambiguous.status == "retry" and "OUTCOME_UNCERTAIN" in ambiguous.reasons
    recovered = service.execute_commerce_action(owner, **arguments)
    repeated = service.execute_commerce_action(owner, **arguments)
    assert recovered.verified is True and repeated.duplicate is True and len(writes) == 1
