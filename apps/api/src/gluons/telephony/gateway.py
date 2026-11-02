"""Authenticated voice intake converges on the ordinary durable order workflow."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from gluons import audit, knowledge
from gluons.config import get_settings
from gluons.core import GluonsError, constant_time_equals, fingerprint, hmac_sha256_hex, new_id, not_found, utcnow
from gluons.db.models import Automation, Call, PhoneRoute, Run, WebhookInbox
from gluons.db.session import operation_lock, system_tx, tenant_tx
from gluons.runtime import lifecycle
from gluons.telephony import provider, service


def verify_signature(raw: bytes, header: str) -> dict:
    secret = get_settings().elevenlabs_webhook_secret
    if not secret:
        raise GluonsError("VOICE_CONFIGURATION_REQUIRED", "Post-call authentication is not configured.", status=503)
    if len(raw) > 256_000:
        raise GluonsError("PAYLOAD_TOO_LARGE", "Voice event exceeds the permitted size.", status=413)
    try:
        fields = {}
        for part in header.split(","):
            key, value = part.strip().split("=", 1)
            if key in fields:
                raise ValueError
            fields[key] = value
        timestamp = int(fields["t"])
        if abs(utcnow().timestamp() - timestamp) > 1800:
            raise ValueError
        digest = hmac_sha256_hex(secret.encode(), str(timestamp).encode() + b"." + raw)
        if not constant_time_equals(fields["v0"], digest):
            raise ValueError
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise GluonsError("WEBHOOK_SIGNATURE_INVALID", "The voice webhook could not be authenticated.", status=401) from exc


def _route_for_agent(agent_id: str) -> PhoneRoute:
    with system_tx() as session:
        rows = session.scalars(select(PhoneRoute).where(PhoneRoute.voice_agent_id == agent_id).limit(2)).all()
        if len(rows) != 1:
            raise not_found("Verified phone route")
        return rows[0]


def record_conversation(conversation: dict, *, event_type: str = "post_call_transcription") -> dict:
    identifier = str(conversation.get("conversation_id", ""))
    if not identifier or len(identifier) > 200 or event_type != "post_call_transcription":
        raise GluonsError("VOICE_EVENT_UNSUPPORTED", "A transcription event with a conversation ID is required.", status=422)
    route = _route_for_agent(str(conversation.get("agent_id", "")))
    if route.state == "disabled":
        return {"status": "ignored_disabled_route"}
    phone_call = service._check_conversation(route, conversation, identifier)
    metadata = conversation.get("metadata") or {}
    try:
        started = datetime.fromtimestamp(int(metadata["start_time_unix_secs"]), tz=UTC)
        duration = int(metadata["call_duration_secs"])
        if not 0 <= duration <= 86400 or started > utcnow() + timedelta(minutes=5):
            raise ValueError
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise GluonsError("VOICE_EVENT_INVALID", "The provider call timing is invalid.", status=422) from exc
    event_id = f"{identifier}:{event_type}"
    with tenant_tx(route.tenant_id) as session:
        current = session.get(PhoneRoute, route.id, with_for_update=True)
        if current.state == "disabled":
            return {"status": "ignored_disabled_route"}
        inserted = session.execute(insert(WebhookInbox).values(
            id=new_id("whk"), provider="elevenlabs", event_id=event_id, tenant_id=route.tenant_id,
            event_type=event_type, payload_hash=fingerprint(conversation),
            payload={"conversation_id": identifier, "agent_id": route.voice_agent_id}, status="processed", processed_at=utcnow(),
        ).on_conflict_do_nothing(constraint="uq_webhook_provider_event").returning(WebhookInbox.id)).first()
        if inserted is None:
            return {"status": "processed", "duplicate": True}
        row = session.scalar(select(Call).where(Call.conversation_id == identifier).with_for_update())
        if row is None:
            row = Call(id=new_id("call"), tenant_id=route.tenant_id, phone_route_id=route.id,
                       conversation_id=identifier, started_at=started)
            session.add(row)
        row.caller = phone_call["external_number"]
        row.provider_call_id = phone_call.get("call_sid")
        row.status = str(conversation.get("status", "unknown"))[:30]
        row.duration_seconds = duration
        row.ended_at = started + timedelta(seconds=duration)
        row.is_test = bool(current.test_started_at and current.test_started_at <= started <= current.test_started_at + timedelta(minutes=30))
        service.record_provider_cost(session, current, row, metadata)
        audit.record(session, tenant_id=route.tenant_id, category="channel", event_type="voice.call_observed",
                     actor="elevenlabs_webhook", resource_type="call", resource_id=row.id,
                     data={"conversation_id": identifier, "duration_seconds": duration, "is_test": row.is_test})
    return {"status": "processed", "duplicate": False}


def post_call(raw: bytes, signature: str) -> dict:
    payload = verify_signature(raw, signature)
    if payload.get("type") != "post_call_transcription":
        # Audio is deliberately not retained without a separately configured policy.
        return {"status": "ignored_event_type"}
    data = payload.get("data")
    if not isinstance(data, dict):
        raise GluonsError("VOICE_EVENT_INVALID", "Expected a provider conversation object.", status=422)
    return record_conversation(data)


def authenticated_route(route_id: str, secret: str) -> PhoneRoute:
    with system_tx() as session:
        route = session.get(PhoneRoute, route_id)
    if route is None or not secret or not constant_time_equals(service.route_secret(route), secret):
        raise GluonsError("VOICE_AUTH_REQUIRED", "Voice tool authentication failed.", status=401)
    if route.state == "disabled" or not route.ownership_verified:
        raise GluonsError("VOICE_ROUTE_DISABLED", "The phone route is not enabled.", status=403)
    return route


def submit_order(route_id: str, secret: str, *, conversation_id: str, intent_id: str, text: str) -> dict:
    route = authenticated_route(route_id, secret)
    conversation = provider.read("conversations", conversation_id)
    phone = service._check_conversation(route, conversation, conversation_id)
    with tenant_tx(route.tenant_id) as session:
        current = session.get(PhoneRoute, route.id)
        automation = session.get(Automation, current.automation_id) if current.automation_id else None
        if automation is None or automation.skill_id != "multichannel_order_entry":
            raise GluonsError("VOICE_AUTOMATION_REQUIRED", "Bind an approved shared order automation first.", status=409)
        test_window = bool(current.test_started_at and current.test_started_at <= utcnow() <= current.test_started_at + timedelta(minutes=30))
        if not current.test_call_verified and not (test_window and automation.mode in ("shadow", "historical_replay")):
            raise GluonsError("VOICE_TEST_REQUIRED", "A verified inbound route is required before provider actions.", status=409)
        if current.test_call_verified and current.state != "active":
            raise GluonsError("VOICE_ROUTE_DISABLED", "The route is not active.", status=403)
    event_id = f"voice:{conversation_id}:{intent_id}"
    payload = {"text": text, "phone": phone["external_number"], "channel": "voice", "conversation_id": conversation_id}
    with operation_lock(f"voice-intake:{route.tenant_id}:{event_id}") as acquired:
        if not acquired:
            raise GluonsError("CONFLICT", "This intent is being processed. Retry shortly.", status=409)
        with tenant_tx(route.tenant_id) as session:
            existing = session.scalar(select(Run).where(Run.automation_id == automation.id, Run.source_event_id == event_id))
            if existing and fingerprint(existing.input) != fingerprint(payload):
                raise GluonsError("INTENT_CHANGED", "Use a new intent ID when an order changes.", status=409)
        run = lifecycle.submit_event(route.tenant_id, automation.id, source="voice", source_event_id=event_id, payload=payload)
        with tenant_tx(route.tenant_id) as session:
            call = session.scalar(select(Call).where(Call.conversation_id == conversation_id))
            if call is None:
                call = Call(id=new_id("call"), tenant_id=route.tenant_id, phone_route_id=route.id, conversation_id=conversation_id,
                            caller=phone["external_number"], is_test=not route.test_call_verified)
                session.add(call)
            call.run_id = run["run_id"]
    return {**run, "status": "queued_for_validation", "order_confirmed": False,
            "message": "Order intent received. The normal policy, validation and approval workflow determines acceptance."}


def business_knowledge(route_id: str, secret: str, query: str) -> dict:
    route = authenticated_route(route_id, secret)
    return {"documents": knowledge.search(route.tenant_id, query, audience="voice"), "trust": "untrusted_business_data"}
