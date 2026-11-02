"""Real PostgreSQL and explicitly labelled Graph provider doubles; no real messages."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from gluons import approvals, gateway, vault
from gluons.actions import ConnectorContext, ProviderError
from gluons.config import get_settings
from gluons.connectors.whatsapp import SendWhatsApp, WhatsAppSendConnector, _callback
from gluons.core import GluonsError, hmac_sha256_hex, new_id, utcnow
from gluons.db.models import ChannelMessage, ChannelThread, Connection, ConnectionSession, Customer, Job, Run, WebhookInbox
from gluons.db.session import tenant_tx
from gluons.net import SideEffectBlocked, write_permit
from gluons.runtime import lifecycle
from gluons.whatsapp import service
from helpers import World


@pytest.fixture
def wa(world, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_secret", "double-app-secret")
    monkeypatch.setattr(settings, "meta_webhook_verify_token", "double-verify-token")
    monkeypatch.setattr(settings, "meta_app_id", "999999")
    phone_id = str(int(new_id("id").split("_")[1], 16))[:18]
    with tenant_tx(world.tenant_id) as session:
        secret = vault.store(session, world.tenant_id, "whatsapp_business", {"access_token": "provider-double"})
        connection = Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="whatsapp_business", environment="sandbox",
            external_account_id=phone_id, account_display="+15550001", granted_scopes=service.SCOPES,
            credential_ref=secret, account_confirmed_at=utcnow(), created_by=world.user_id,
            metadata_json={"account_id": "777777", "app_id": "999999", "subscription_verified": True, "requested_capabilities": ["read_messages", "send_replies"]})
        session.add(connection)
    return connection


def event(wa, *, event_id="wamid.inbound", text="2 cases FISH-1", timestamp=None):
    return {"object": "whatsapp_business_account", "entry": [{"id": "777777", "changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": wa.external_account_id}, "messages": [{"id": event_id, "from": "15550100",
            "timestamp": str(int((timestamp or utcnow()).timestamp())), "type": "text", "text": {"body": text}}]}}]}]}


def deliver(payload):
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac_sha256_hex(get_settings().meta_app_secret.encode(), raw)
    return service.receive_webhook(raw, signature)


def process(world):
    with tenant_tx(world.tenant_id) as session:
        ids = session.execute(select(WebhookInbox.id).where(WebhookInbox.status == "received")).scalars().all()
    for inbox_id in ids:
        service.process_event(world.tenant_id, inbox_id)


def test_challenge_and_raw_body_signature_are_mandatory(wa):
    assert service.challenge("subscribe", "double-verify-token", "challenge") == "challenge"
    with pytest.raises(GluonsError, match="verification failed"):
        service.challenge("subscribe", "wrong", "challenge")
    with pytest.raises(GluonsError, match="signature"):
        service.receive_webhook(json.dumps(event(wa)).encode(), "sha256=wrong")
    with pytest.raises(GluonsError) as error:
        service.receive_webhook(b"x" * (service.MAX_WEBHOOK_BYTES + 1), "bad")
    assert error.value.status == 413


def test_exact_duplicate_is_durably_deduplicated_and_creates_one_job(world, wa):
    payload = event(wa)
    assert deliver(payload)["accepted"] == 1
    assert deliver(payload)["duplicates"] == 1
    with tenant_tx(world.tenant_id) as session:
        assert session.execute(select(func.count()).select_from(WebhookInbox)).scalar_one() == 1
        assert session.execute(select(func.count()).select_from(Job).where(Job.kind == "whatsapp.process")).scalar_one() == 1


@pytest.mark.parametrize("days", [-8, 1])
def test_old_or_future_provider_event_cannot_reopen_service_window(wa, days):
    with pytest.raises(GluonsError, match="replay window"):
        deliver(event(wa, timestamp=utcnow() + timedelta(days=days)))


def test_wrong_account_unknown_number_or_unconfirmed_connection_do_not_route(world, wa):
    payload = event(wa)
    payload["entry"][0]["id"] = "888888"
    assert deliver(payload)["accepted"] == 0
    payload = event(wa)
    payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "000000"
    assert deliver(payload)["accepted"] == 0
    with tenant_tx(world.tenant_id) as session:
        session.get(Connection, wa.id).account_confirmed_at = None
    assert deliver(event(wa))["accepted"] == 0


def test_shared_order_intake_requires_explicit_automation_configuration(world, wa):
    automation = world.order_automation()
    trial = lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"])
    deliver(event(wa))
    process(world)
    with tenant_tx(world.tenant_id) as session:
        assert session.execute(select(func.count()).select_from(Run)).scalar_one() == 0
        assert session.execute(select(func.count()).select_from(ChannelMessage)).scalar_one() == 1
    from gluons.db.models import Automation
    with tenant_tx(world.tenant_id) as session:
        session.get(Automation, trial["id"]).config = {"connections": {"messaging": wa.id}, "whatsapp_ingest_enabled": True}
    deliver(event(wa, event_id="wamid.second"))
    process(world)
    process(world)
    with tenant_tx(world.tenant_id) as session:
        runs = session.execute(select(Run)).scalars().all()
        assert len(runs) == 1 and runs[0].mode == "shadow"
        assert runs[0].input["phone"] == "+15550100"
        assert runs[0].input["channel"] == "whatsapp"
        thread = session.execute(select(ChannelThread)).scalar_one()
        assert thread.service_window_expires_at > utcnow() + timedelta(hours=23)


def test_disconnection_quarantines_already_queued_events(world, wa):
    deliver(event(wa))
    with tenant_tx(world.tenant_id) as session:
        session.get(Connection, wa.id).status = "disconnected"
    process(world)
    with tenant_tx(world.tenant_id) as session:
        assert session.execute(select(WebhookInbox)).scalar_one().status == "quarantined"
        assert session.execute(select(func.count()).select_from(ChannelMessage)).scalar_one() == 0


def test_personal_account_and_setup_link_without_owner_session_are_refused(world):
    with pytest.raises(GluonsError, match="Gluons Web"):
        service.begin_session(world.owner)
    browser = replace(world.owner, session_id="browser")
    with pytest.raises(GluonsError, match="personal"):
        service.begin_session(browser, account_type="personal")


def test_operator_binding_checks_scopes_phone_subscription_and_consumes_session(world, monkeypatch):
    browser = replace(world.owner, session_id="browser")
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "999999")
    monkeypatch.setattr(settings, "meta_app_secret", "configured-secret")
    monkeypatch.setattr(settings, "meta_webhook_verify_token", "configured-verifier")
    monkeypatch.setenv("GLUONS_WHATSAPP_ACCESS_TOKEN", "never-return-this-token")
    setup = service.begin_session(browser)
    phone_id = str(int(new_id("id").split("_")[1], 16))[:18]
    calls = []
    def meta(request):
        calls.append(str(request.url))
        assert "never-return" not in str(request.url)
        if request.url.path.endswith("permissions"):
            return httpx.Response(200, json={"data": [{"permission": scope, "status": "granted"} for scope in service.SCOPES]})
        if request.url.path.endswith("phone_numbers"):
            return httpx.Response(200, json={"data": [{"id": phone_id, "display_phone_number": "+15550001", "code_verification_status": "VERIFIED"}]})
        return httpx.Response(200, json={"data": [{"whatsapp_business_api_data": {"id": "999999"}}]})
    with pytest.raises(GluonsError, match="new owner-bound"):
        service.bind_from_environment(replace(browser, session_id="another-browser"), setup["session_id"], account_id="777777", phone_number_id=phone_id, transport=httpx.MockTransport(meta))
    result = service.bind_from_environment(browser, setup["session_id"], account_id="777777", phone_number_id=phone_id, transport=httpx.MockTransport(meta))
    assert result["account_confirmed"] is False and len(calls) == 3
    assert "never-return" not in str(result)
    with tenant_tx(world.tenant_id) as session:
        row = session.get(ConnectionSession, setup["session_id"])
        assert row.consumed_at is not None
        connection = session.get(Connection, result["connection_id"])
        assert connection.credential_ref and connection.granted_scopes == service.SCOPES


def _context(world, wa, provider):
    return ConnectorContext(world.tenant_id, wa.id, "whatsapp_business", "sandbox", wa.external_account_id,
        wa.metadata_json, lambda: {"access_token": "provider-double"}, httpx.MockTransport(provider))


def test_outgoing_consent_window_and_shadow_enforced_without_provider_write(world, wa):
    writes = []
    context = _context(world, wa, lambda request: writes.append(request) or httpx.Response(200, json={"messages": [{"id": "wamid.out"}]}))
    payload = SendWhatsApp(to="+15550100", text="Order draft")
    connector = WhatsAppSendConnector()
    with pytest.raises(ProviderError, match="opt-in"):
        with write_permit("test"):
            connector.execute(context, payload, "send-key")
    with tenant_tx(world.tenant_id) as session:
        session.execute(select(Customer)).scalar_one().opted_in_channels = ["whatsapp"]
    with pytest.raises(ProviderError, match="window is closed"):
        with write_permit("test"):
            connector.execute(context, payload, "send-key")
    result = gateway.execute(gateway.ActionRequest(tenant_id=world.tenant_id, action="whatsapp.message.send", input=payload.model_dump(),
        idempotency_key="shadow-send", mode="shadow", connection_id=wa.id, transport=context.transport))
    assert result.status == "preview" and not result.verified and writes == []


def test_provider_acceptance_is_not_delivery_and_signed_status_is_independent(world, wa):
    deliver(event(wa))
    process(world)
    with tenant_tx(world.tenant_id) as session:
        session.execute(select(Customer)).scalar_one().opted_in_channels = ["whatsapp"]
    writes = []
    context = _context(world, wa, lambda r: writes.append(r) or httpx.Response(200, json={"messages": [{"id": "wamid.outbound"}]}))
    connector, payload = WhatsAppSendConnector(), SendWhatsApp(to="+15550100", text="Approved confirmation")
    with pytest.raises(SideEffectBlocked):
        connector.execute(context, payload, "send-key")
    with write_permit("approved-test-write"):
        result = connector.execute(context, payload, "send-key")
    with pytest.raises(ProviderError, match="Awaiting"):
        connector.verify(context, payload, result)
    receipt = event(wa)
    value = receipt["entry"][0]["changes"][0]["value"]
    value.pop("messages")
    value["statuses"] = [{"id": "wamid.outbound", "recipient_id": "15550100", "status": "delivered", "timestamp": str(int(utcnow().timestamp())),
                          "biz_opaque_callback_data": _callback(world.tenant_id, "send-key")}]
    deliver(receipt)
    process(world)
    readback = connector.verify(context, payload, result)
    assert readback.matches and readback.record["status"] == "delivered"
    assert len(writes) == 1
    assert connector.lookup(context, payload, "send-key").provider_object_id == "wamid.outbound"
    with pytest.raises(ProviderError, match="reconcile"):
        connector.lookup(context, payload, "unknown-send-key")


def test_template_is_checked_against_exact_provider_approval(world, wa):
    with tenant_tx(world.tenant_id) as session:
        session.execute(select(Customer)).scalar_one().opted_in_channels = ["whatsapp"]
    methods = []
    def meta(request):
        methods.append(request.method)
        return httpx.Response(200, json={"data": [{"name": "approved_template", "language": "en_US", "status": "PENDING", "components": []}]})
    connector = WhatsAppSendConnector()
    payload = SendWhatsApp(to="+15550100", kind="template", template_name="approved_template", template_language="en_US")
    with pytest.raises(ProviderError, match="approved"):
        with write_permit("test"):
            connector.execute(_context(world, wa, meta), payload, "template-key")
    assert methods == ["GET"]


def test_tenant_media_authority_size_and_integrity_are_checked(world, wa):
    payload = event(wa)
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message.update(type="document", document={"id": "123456", "mime_type": "text/plain"})
    message.pop("text")
    deliver(payload)
    process(world)
    body = b"authorized document"
    def meta(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"file_size": len(body), "mime_type": "text/plain", "url": "https://lookaside.fbsbx.com/media",
                                            "sha256": hashlib.sha256(body).hexdigest()})
        return httpx.Response(200, content=body)
    assert service.download_media(world.owner, wa.id, "123456", transport=httpx.MockTransport(meta)) == (body, "text/plain")
    with pytest.raises(GluonsError):
        service.download_media(World.create().owner, wa.id, "123456", transport=httpx.MockTransport(meta))
    with pytest.raises(GluonsError):
        service.download_media(world.owner, wa.id, "999999", transport=httpx.MockTransport(meta))


def test_media_stream_is_bounded_even_if_provider_metadata_lies(world, wa, monkeypatch):
    payload = event(wa)
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message.update(type="document", document={"id": "123456", "mime_type": "text/plain"})
    message.pop("text")
    deliver(payload)
    process(world)
    monkeypatch.setattr(service, "MAX_MEDIA_BYTES", 20)
    def meta(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"file_size": 5, "mime_type": "text/plain", "url": "https://lookaside.fbsbx.com/media"})
        return httpx.Response(200, content=b"x" * 21)
    with pytest.raises(GluonsError, match="bounded media"):
        service.download_media(world.owner, wa.id, "123456", transport=httpx.MockTransport(meta))


def test_malformed_signed_records_are_rejected_as_validation_errors(wa):
    payload = event(wa)
    payload["entry"][0]["changes"] = "not-a-list"
    with pytest.raises(GluonsError) as error:
        deliver(payload)
    assert error.value.code == "WEBHOOK_INVALID"


def test_gateway_approval_and_delivery_retries_never_send_twice(world, wa, sandbox_erp):
    world.connect_erp(sandbox_erp)
    world.connections["messaging"] = wa.id
    automation = world.supervised_automation(allowed_actions=["erp.order.create", "whatsapp.message.send"])
    deliver(event(wa))
    process(world)
    with tenant_tx(world.tenant_id) as session:
        session.execute(select(Customer)).scalar_one().opted_in_channels = ["whatsapp"]
    writes = []
    def meta(request):
        writes.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.approved"}]})
    request = gateway.ActionRequest(tenant_id=world.tenant_id, action="whatsapp.message.send", mode="supervised",
        actor=world.user_id, automation_id=automation["id"], connection_id=wa.id, idempotency_key="approved-wa-key",
        input=SendWhatsApp(to="+15550100", text="Authorized confirmation").model_dump(), transport=httpx.MockTransport(meta))
    pending = gateway.execute(request)
    assert pending.status == "waiting_approval" and writes == []
    with tenant_tx(world.tenant_id) as session:
        approvals.decide(session, world.owner, pending.approval_id, "approve")
    assert gateway.execute(request).status == "retry"
    assert gateway.execute(request).status == "retry"
    assert len(writes) == 1
    receipt = event(wa)
    value = receipt["entry"][0]["changes"][0]["value"]
    value.pop("messages")
    value["statuses"] = [{"id": "wamid.approved", "recipient_id": "15550100", "status": "delivered",
        "timestamp": str(int(utcnow().timestamp())), "biz_opaque_callback_data": _callback(world.tenant_id, "approved-wa-key")}]
    deliver(receipt)
    process(world)
    result = gateway.execute(request)
    assert result.status == "success" and result.verified is True and len(writes) == 1


def test_connection_verification_revocation_is_persisted_and_account_change_requires_confirmation(world, wa):
    def revoked(request):
        return httpx.Response(403, json={"error": {"message": "revoked"}})
    with pytest.raises(GluonsError) as error:
        service.verify_connection(world.owner, wa.id, transport=httpx.MockTransport(revoked))
    assert error.value.code == "CONNECTION_REAUTH_REQUIRED"
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Connection, wa.id).status == "needs_reauth"
    def restored(request):
        if request.url.path.endswith("permissions"):
            return httpx.Response(200, json={"data": [{"permission": scope, "status": "granted"} for scope in service.SCOPES]})
        return httpx.Response(200, json={"data": [{"id": wa.external_account_id, "display_phone_number": "+15550002", "code_verification_status": "VERIFIED"}]})
    result = service.verify_connection(world.owner, wa.id, transport=httpx.MockTransport(restored))
    assert result["status"] == "connected" and result["account_confirmed"] is False
    assert result["account_display"] == "+15550002"


def test_send_only_link_cannot_start_reading_customer_messages(world, wa):
    with tenant_tx(world.tenant_id) as session:
        connection = session.get(Connection, wa.id)
        connection.metadata_json = {**connection.metadata_json, "requested_capabilities": ["send_replies"]}
    assert deliver(event(wa))["accepted"] == 0
