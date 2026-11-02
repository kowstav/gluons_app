"""Provider doubles for ElevenLabs; real DB and ERP simulator for shared execution."""
import json

import pytest
from sqlalchemy import select

from gluons import approvals, knowledge
from gluons.config import get_settings
from gluons.core import GluonsError, hmac_sha256_hex, new_id, utcnow
from gluons.db.models import Approval, Call, PhoneRoute, WebhookInbox
from gluons.db.session import tenant_tx
from gluons.telephony import gateway, provider, service
from helpers import World, drain


@pytest.fixture
def voice(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_tool_secret", "test-route-authentication-secret")
    monkeypatch.setattr(get_settings(), "elevenlabs_webhook_secret", "test-postcall-authentication-secret")
    route = service.begin_route(world.owner, "new_number")
    agent_id, number_id = new_id("agent"), new_id("number")
    number = {"phone_number_id": number_id, "phone_number": "+15551234567", "assigned_agent": {"agent_id": agent_id}, "provider": "twilio"}
    monkeypatch.setattr(provider, "read", lambda kind, identifier, **kwargs: number)
    service.configure_operator_route(tenant_id=world.tenant_id, route_id=route["id"], number_id=number_id, agent_id=agent_id)
    service.start_test(world.owner, route["id"])
    conversation = {"conversation_id": new_id("conv"), "agent_id": agent_id, "status": "done", "has_user_audio": True, "has_response_audio": True,
                    "metadata": {"start_time_unix_secs": int(utcnow().timestamp()) + 2, "call_duration_secs": 15,
                                 "phone_call": {"direction": "inbound", "agent_number": "+15551234567", "external_number": "+15550100", "call_sid": "provider-call"}}}
    monkeypatch.setattr(provider, "read", lambda kind, identifier, **kwargs: conversation)
    with tenant_tx(world.tenant_id) as session:
        row = session.get(PhoneRoute, route["id"])
        secret = service.route_secret(row)
    return route["id"], secret, conversation


def signed(conversation, *, timestamp=None):
    raw = json.dumps({"type": "post_call_transcription", "data": conversation}).encode()
    stamp = int(utcnow().timestamp()) if timestamp is None else timestamp
    digest = hmac_sha256_hex(get_settings().elevenlabs_webhook_secret.encode(), str(stamp).encode() + b"." + raw)
    return raw, f"t={stamp},v0={digest}"


def test_signed_postcall_is_deduplicated_and_route_needs_independent_verification(world, voice):
    route_id, _, conversation = voice
    raw, signature = signed(conversation)
    assert gateway.post_call(raw, signature)["duplicate"] is False
    assert gateway.post_call(raw, signature)["duplicate"] is True
    assert service.route_status(world.owner, route_id)["test_call_verified"] is False
    assert service.verify_test_call(world.owner, route_id)["test_call_verified"] is True
    with tenant_tx(world.tenant_id) as session:
        assert len(session.scalars(select(Call)).all()) == 1
        assert len(session.scalars(select(WebhookInbox)).all()) == 1
        assert session.scalar(select(Call)).cost_usd is None


@pytest.mark.parametrize("fault", ["tamper", "expired", "future", "duplicate_timestamp"])
def test_signature_rejects_tampering_expiry_future_and_ambiguous_headers(world, voice, fault):
    _, _, conversation = voice
    timestamp = int(utcnow().timestamp()) + (1900 if fault == "future" else -1900 if fault == "expired" else 0)
    raw, signature = signed(conversation, timestamp=timestamp)
    if fault == "tamper":
        raw += b" "
    if fault == "duplicate_timestamp":
        signature += ",t=" + str(timestamp)
    with pytest.raises(GluonsError) as error:
        gateway.post_call(raw, signature)
    assert error.value.code == "WEBHOOK_SIGNATURE_INVALID"
    with tenant_tx(world.tenant_id) as session:
        assert session.scalar(select(Call)) is None


def test_forwarding_is_not_proved_by_a_call_directly_to_target(world, voice):
    route_id, _, conversation = voice
    with tenant_tx(world.tenant_id) as session:
        session.get(PhoneRoute, route_id).route_mode = "carrier_forwarding"
    gateway.post_call(*signed(conversation))
    status = service.verify_test_call(world.owner, route_id)
    assert status["target_call_verified"] is True
    assert status["network_routing_verified"] is False
    assert status["state"] == "awaiting_carrier_evidence"


def test_voice_uses_shared_durable_order_approval_and_simulator_readback(world, voice, sandbox_erp, erp):
    route_id, secret, conversation = voice
    world.connect_erp(sandbox_erp)
    automation = world.supervised_automation()
    with tenant_tx(world.tenant_id) as session:
        session.get(PhoneRoute, route_id).automation_id = automation["id"]
    with pytest.raises(GluonsError, match="verified inbound"):
        gateway.submit_order(route_id, secret, conversation_id=conversation["conversation_id"], intent_id="order1", text="2 cases FISH-1")
    gateway.post_call(*signed(conversation))
    service.verify_test_call(world.owner, route_id)
    run = gateway.submit_order(route_id, secret, conversation_id=conversation["conversation_id"], intent_id="order1", text="2 cases FISH-1")
    repeated = gateway.submit_order(route_id, secret, conversation_id=conversation["conversation_id"], intent_id="order1", text="2 cases FISH-1")
    assert repeated["run_id"] == run["run_id"] and repeated["duplicate"]
    assert run["order_confirmed"] is False
    drain()
    assert erp.orders() == []
    with tenant_tx(world.tenant_id, world.user_id) as session:
        approval = session.scalar(select(Approval).where(Approval.run_id == run["run_id"], Approval.status == "pending"))
        approvals.decide(session, world.owner, approval.id, "approve")
    drain()
    assert len(erp.orders()) == 1
    with pytest.raises(GluonsError, match="new intent ID"):
        gateway.submit_order(route_id, secret, conversation_id=conversation["conversation_id"], intent_id="order1", text="8 cases FISH-1")


def test_voice_wrong_secret_or_agent_and_disconnection_never_read_business_data(world, voice):
    route_id, secret, conversation = voice
    knowledge.add_document(world.owner, title="Hours", body="Open 9 to 5")
    assert gateway.business_knowledge(route_id, secret, "Open")["documents"] == []
    knowledge.add_document(world.owner, title="Public hours", body="Open 9 to 5", audience=["voice"])
    assert len(gateway.business_knowledge(route_id, secret, "Open")["documents"]) == 1
    with pytest.raises(GluonsError):
        gateway.business_knowledge(route_id, "wrong-secret", "Open")
    other = World.create()
    assert service.list_routes(other.owner) == []
    conversation["agent_id"] = "other-agent"
    with pytest.raises(GluonsError):
        gateway.post_call(*signed(conversation))
    service.disable_route(world.owner, route_id)
    with pytest.raises(GluonsError, match="not enabled"):
        gateway.business_knowledge(route_id, secret, "Open")
