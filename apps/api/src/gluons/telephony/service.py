"""Phone routing is verified from provider records, never a chat or checkbox.

An operator binds an already authorized provider number using a local command.
Owners can request setup and test windows; neither operation provisions service.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select

from gluons import audit, billing
from gluons.authz import Principal
from gluons.config import get_settings
from gluons.core import GluonsError, conflict, hmac_sha256_hex, not_found, utcnow, validation_failed
from gluons.db.models import Automation, Call, PhoneRoute
from gluons.db.session import operation_lock, system_tx, tenant_tx
from gluons.telephony import provider

E164 = re.compile(r"\+[1-9][0-9]{7,14}")
MODES = frozenset({"new_number", "carrier_forwarding", "sip_pbx"})


def _view(row: PhoneRoute) -> dict[str, Any]:
    return {"id": row.id, "route_id": row.id, "number": row.number, "mode": row.route_mode,
            "state": row.state, "provider_number_id": row.provider_number_id, "voice_agent_id": row.voice_agent_id,
            "ownership_verified": row.ownership_verified, "test_call_verified": row.test_call_verified,
            "test_call_id": row.test_call_id, "routing_instructions": row.routing_instructions,
            "automation_id": row.automation_id, "version": row.version,
            "configuration": {"elevenlabs": bool(get_settings().elevenlabs_api_key),
                              "tool_auth": bool(get_settings().elevenlabs_tool_secret),
                              "post_call_auth": bool(get_settings().elevenlabs_webhook_secret)},
            "network_routing_verified": row.test_call_verified,
            "notice": "Setup requests and conversation consent do not redirect carrier calls. Existing-number forwarding needs independent carrier evidence."}


def _load(session, route_id: str, *, lock: bool = False) -> PhoneRoute:  # type: ignore[no-untyped-def]
    row = session.get(PhoneRoute, route_id, with_for_update=lock)
    if row is None:
        raise not_found("Phone route")
    return row


def begin_route(principal: Principal, mode: str, *, number_hint: str | None = None, country: str | None = None,
                automation_id: str | None = None) -> dict[str, Any]:
    from gluons.core import new_id
    principal.require("phone:manage")
    if mode not in MODES or (number_hint is not None and not E164.fullmatch(number_hint)):
        raise validation_failed("Choose new_number, carrier_forwarding or sip_pbx and use E.164 number format.")
    if country is not None and not re.fullmatch(r"[A-Za-z]{2}", country):
        raise validation_failed("Country must be a two-letter country code.")
    instructions = {
        "new_number": "Owner/operator obtains an authorized inbound number from a supported telephony provider and links it to an ElevenLabs agent. No number has been purchased by Gluons.",
        "carrier_forwarding": "The number's owner configures forwarding through their carrier to a supported inbound number. Confirm regional and carrier support; test forwarding from an independent caller. A direct call to the target does not prove forwarding.",
        "sip_pbx": "The authorized PBX administrator configures a supported ElevenLabs SIP trunk with authentication, then places an observed inbound test call.",
    }[mode]
    if country and country.upper() == "IN":
        instructions += " For India, evaluate supported Exotel or SIP routing and carrier restrictions; do not assume a local Twilio number is available."
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        if automation_id:
            automation = session.get(Automation, automation_id)
            if automation is None:
                raise not_found("Automation")
            if automation.skill_id not in ("multichannel_order_entry", "restaurant_voice_ordering"):
                raise validation_failed("This voice bridge currently supports shared order-intake skills.")
        row = PhoneRoute(id=new_id("phr"), tenant_id=principal.tenant_id, number="unassigned", route_mode=mode,
                         forwarded_from_number=number_hint if mode == "carrier_forwarding" else None,
                         state="awaiting_provider_setup", routing_instructions=instructions,
                         automation_id=automation_id, created_by=principal.actor_id)
        session.add(row)
        session.flush()
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="phone.setup_requested",
                     actor=principal, resource_type="phone_route", resource_id=row.id, data={"mode": mode, "country": country})
        return _view(row)


def list_routes(principal: Principal) -> list[dict[str, Any]]:
    principal.require("connections:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return [_view(row) for row in session.scalars(select(PhoneRoute).order_by(PhoneRoute.created_at.desc()))]


def route_status(principal: Principal, route_id: str) -> dict[str, Any]:
    principal.require("connections:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return _view(_load(session, route_id))


def configure_operator_route(*, tenant_id: str, route_id: str, number_id: str, agent_id: str,
                             transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Local operator entry point, deliberately absent from owner/assistant HTTP APIs."""
    number = provider.read("phone-numbers", number_id, transport=transport)
    assigned = number.get("assigned_agent") or {}
    actual_agent = assigned.get("agent_id") if isinstance(assigned, dict) else None
    actual_agent = actual_agent or number.get("agent_id")
    actual_number = number.get("phone_number", "")
    if number.get("phone_number_id") != number_id or actual_agent != agent_id or not E164.fullmatch(actual_number):
        raise GluonsError("VOICE_EVIDENCE_MISMATCH", "The provider number and assigned agent do not match.", status=409)
    if number.get("provider") not in ("twilio", "exotel", "sip_trunk"):
        raise GluonsError("VOICE_PROVIDER_UNSUPPORTED", "Use a documented supported inbound telephony provider.", status=409)
    with operation_lock("voice:provider-binding") as acquired:
        if not acquired:
            raise conflict("Another phone binding is being configured. Retry shortly.")
        with system_tx() as session:
            existing = session.execute(select(PhoneRoute.id).where(
                PhoneRoute.id != route_id, (PhoneRoute.provider_number_id == number_id) | (PhoneRoute.voice_agent_id == agent_id))).first()
            if existing:
                raise conflict("This number or agent already belongs to a Gluons phone route.")
        with tenant_tx(tenant_id) as session:
            row = _load(session, route_id, lock=True)
            row.number, row.provider_number_id, row.voice_agent_id = actual_number, number_id, agent_id
            row.ownership_verified, row.test_call_verified, row.test_call_id = True, False, None
            row.state, row.version = "awaiting_test_call", row.version + 1
            audit.record(session, tenant_id=tenant_id, category="permission", event_type="phone.provider_binding_verified",
                         actor="local_operator", resource_type="phone_route", resource_id=row.id,
                         data={"number_id": number_id, "agent_id": agent_id, "provider": number["provider"]})
            return _view(row)


def start_test(principal: Principal, route_id: str) -> dict[str, Any]:
    principal.require("phone:manage")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = _load(session, route_id, lock=True)
        if not row.ownership_verified or not row.voice_agent_id or row.state == "disabled":
            raise GluonsError("VOICE_CONFIGURATION_REQUIRED", "An operator must verify the provider number and agent first.", status=409)
        row.test_started_at, row.test_call_verified, row.test_call_id = utcnow(), False, None
        row.state, row.version = "awaiting_test_call", row.version + 1
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="phone.test_requested",
                     actor=principal, resource_type="phone_route", resource_id=row.id)
        return {**_view(row), "test_window_expires_at": (row.test_started_at + timedelta(minutes=30)).isoformat(),
                "next_step": "Place a real inbound call through the configured route. No call is placed by this endpoint."}


def _check_conversation(route: PhoneRoute, conversation: dict[str, Any], conversation_id: str) -> dict[str, Any]:
    metadata = conversation.get("metadata") or {}
    call = metadata.get("phone_call") or {}
    if (conversation.get("conversation_id") != conversation_id or conversation.get("agent_id") != route.voice_agent_id
            or call.get("direction") != "inbound" or call.get("agent_number") != route.number
            or not isinstance(call.get("external_number"), str) or not E164.fullmatch(call["external_number"])):
        raise GluonsError("VOICE_EVIDENCE_MISMATCH", "This conversation does not match the configured inbound route.", status=403)
    return call


def verify_test_call(principal: Principal, route_id: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    principal.require("phone:manage")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = _load(session, route_id)
        if row.test_started_at is None:
            raise GluonsError("VOICE_TEST_REQUIRED", "Start a test window and make a real inbound call first.", status=409)
        calls = session.scalars(select(Call).where(Call.phone_route_id == row.id, Call.is_test.is_(True),
                                                  Call.started_at >= row.test_started_at).order_by(Call.started_at.desc()).limit(1)).all()
        if not calls:
            raise GluonsError("VOICE_TEST_REQUIRED", "No authenticated post-call event has been observed in this test window.", status=409)
        candidate = calls[0]
    conversation = provider.read("conversations", candidate.conversation_id, transport=transport)
    _check_conversation(row, conversation, candidate.conversation_id)
    metadata = conversation.get("metadata") or {}
    try:
        started = datetime.fromtimestamp(int(metadata["start_time_unix_secs"]), tz=UTC)
        duration = int(metadata["call_duration_secs"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise GluonsError("VOICE_EVIDENCE_MISMATCH", "Provider call timing is unavailable.", status=409) from exc
    if (conversation.get("status") != "done" or duration <= 0 or not conversation.get("has_user_audio")
            or not conversation.get("has_response_audio") or started < row.test_started_at
            or started > row.test_started_at + timedelta(minutes=30)):
        raise GluonsError("VOICE_TEST_REQUIRED", "A completed two-way audio call within the test window is required.", status=409)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        current = _load(session, route_id, lock=True)
        if current.version != row.version or current.state == "disabled":
            raise conflict("The phone route changed during verification. Start again.")
        current.test_call_id = candidate.conversation_id
        # ElevenLabs receiving an inbound call proves the target worked. It
        # cannot prove which carrier forwarding configuration delivered it.
        current.test_call_verified = current.route_mode != "carrier_forwarding"
        current.state = "active" if current.test_call_verified else "awaiting_carrier_evidence"
        current.version += 1
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="phone.test_observed",
                     actor=principal, resource_type="phone_route", resource_id=current.id,
                     data={"conversation_id": candidate.conversation_id, "target_call_verified": True,
                           "forwarding_verified": current.test_call_verified})
        return {**_view(current), "target_call_verified": True}


def disable_route(principal: Principal, route_id: str) -> dict[str, Any]:
    principal.require("phone:manage")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = _load(session, route_id, lock=True)
        row.state, row.test_call_verified, row.version = "disabled", False, row.version + 1
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="phone.disabled",
                     actor=principal, resource_type="phone_route", resource_id=row.id)
        return {**_view(row), "effects": "Gluons tools are disabled. Disable or restore carrier/provider routing separately; this does not reconfigure the phone network."}


def route_secret(row: PhoneRoute) -> str:
    key = get_settings().elevenlabs_tool_secret
    if not key:
        raise GluonsError("VOICE_CONFIGURATION_REQUIRED", "Configure the voice tool signing secret securely.", status=409)
    return hmac_sha256_hex(key.encode(), f"voice-route:{row.tenant_id}:{row.id}")


def record_provider_cost(session, route: PhoneRoute, call: Call, metadata: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    # `cost` has undocumented units. Only a provider's explicit cost_fiat USD
    # field can become measured monetary usage. Telecom charges remain separate.
    raw = metadata.get("cost_fiat")
    if raw is None:
        return
    try:
        amount = Decimal(str(raw))
        if not amount.is_finite() or not 0 <= amount <= Decimal("999999.999999"):
            return
    except InvalidOperation:
        return
    call.cost_usd = amount
    billing.record_usage(session, tenant_id=route.tenant_id, dedupe_key=f"voice:{call.conversation_id}:fiat", category="voice",
                         provider="elevenlabs", meter="conversation", quantity=call.duration_seconds or 0,
                         cost_usd=amount, cost_basis="provider_reported", automation_id=route.automation_id,
                         run_id=call.run_id, detail={"conversation_id": call.conversation_id, "telephony_cost_included": False})
