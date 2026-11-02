"""Durable signed-webhook intake and owner-controlled Business Platform binding.

Owners link through Embedded Signup v5 when the Meta app and configuration are set. Its
browser code lives 30 seconds, so the page posts it at once and the exchange, webhook
subscription and registration happen here. An operator may instead bind an already
onboarded account from a local environment secret. Web and assistant requests never
carry a provider credential.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from gluons import audit, policy, queue, vault
from gluons.actions import ProviderError
from gluons.authz import Principal
from gluons.config import get_settings
from gluons.core import (
    GluonsError,
    constant_time_equals,
    fingerprint,
    hmac_sha256_hex,
    new_id,
    new_secret_token,
    not_found,
    token_hash,
    utcnow,
)
from gluons.db.models import (
    Automation,
    AutomationPolicy,
    ChannelMessage,
    ChannelThread,
    Connection,
    ConnectionSession,
    WebhookInbox,
)
from gluons.db.session import operation_lock, system_tx, tenant_tx
from gluons.net import GuardedClient, HostNotAllowed, ResponseTooLarge, write_permit
from gluons.runtime import lifecycle
from gluons.whatsapp.provider import MetaClient, identifier

SCOPES = ["whatsapp_business_management", "whatsapp_business_messaging"]
MAX_WEBHOOK_BYTES = 256 * 1024
MAX_MEDIA_BYTES = 10 * 1024 * 1024
CHANNEL = "whatsapp"
SUPPORTED_MEDIA = {"image", "audio", "document", "video", "sticker"}
TERMS_RISK = ("WhatsApp terms section 4.7 bars AI providers whose AI is the primary functionality, at Meta's sole discretion. "
              "Legal review is required before launch, and platform data may not train models.")
CHARGES_NOTICE = ("Meta charges for service messages since 2026-10-01, after 1,000 free per number each month. "
                  "The business must add its own payment method in WhatsApp Manager, or service messages stop.")


def _browser(principal: Principal) -> None:
    principal.require("connections:manage")
    if principal.kind != "user" or not principal.session_id:
        raise GluonsError("OWNER_WEB_REQUIRED", "Continue WhatsApp account setup in Gluons Web.", status=403)


def signup_enabled() -> bool:
    settings = get_settings()
    return bool(settings.meta_app_id and settings.meta_app_secret and settings.meta_config_id and settings.meta_webhook_verify_token)


def _signup_details(session_id: str) -> dict:
    """Everything the setup page needs. It holds no secret: the app ID is public."""
    settings = get_settings()
    return {"sdk_url": "https://connect.facebook.net/en_US/sdk.js",
            "init": {"appId": settings.meta_app_id, "autoLogAppEvents": True, "xfbml": True, "version": settings.meta_graph_version},
            "login_options": {"config_id": settings.meta_config_id, "response_type": "code", "override_default_response_type": True, "extras": {"version": "v5"}},
            "message_event": {"origin_suffix": "facebook.com", "type": "WA_EMBEDDED_SIGNUP"}, "code_ttl_seconds": 30,
            "complete_path": f"/v1/whatsapp/sessions/{session_id}/complete", "notices": [CHARGES_NOTICE, TERMS_RISK]}


def begin_session(principal: Principal, *, capabilities: list[str] | None = None, account_type: str = "business_platform") -> dict:
    _browser(principal)
    if account_type != "business_platform":
        raise GluonsError("PERSONAL_WHATSAPP_UNSUPPORTED", "Use approved WhatsApp Business Platform onboarding, not personal chat access.", status=422)
    capabilities = capabilities or ["read_messages"]
    if set(capabilities) - {"read_messages", "draft_replies", "send_replies", "submit_orders"}:
        raise GluonsError("CAPABILITY_UNSUPPORTED", "Choose supported WhatsApp Business capabilities.", status=422)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = ConnectionSession(id=new_id("cns"), tenant_id=principal.tenant_id, initiated_by=principal.actor_id,
                                initiated_via="web", provider="whatsapp_business", requested_capabilities=capabilities,
                                requested_scopes=SCOPES, setup_handle_hash=token_hash(new_secret_token()),
                                state_hash=token_hash(new_secret_token()), status="pending" if signup_enabled() else "needs_operator_verification",
                                extra={"browser_session_id": principal.session_id, "signup": "embedded_signup_v5" if signup_enabled() else "approved_external_business_onboarding_required"},
                                expires_at=utcnow() + timedelta(seconds=get_settings().connection_session_ttl_seconds))
        session.add(row)
        session.flush()
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="whatsapp.setup_requested", actor=principal,
                     resource_type="connection_session", resource_id=row.id, data={"capabilities": capabilities})
        result = {"session_id": row.id, "status": row.status, "provider": row.provider, "expires_at": row.expires_at.isoformat(),
                  "setup_url": f"{get_settings().public_web_url.rstrip('/')}/setup/{row.id}", "requested_scopes": SCOPES}
        if row.status == "pending":
            return {**result, "whatsapp_setup": _signup_details(row.id), "next_step": "Finish Meta's signup window. Gluons then asks you to confirm the number."}
        return {**result, "next_step": "Embedded Signup is not configured. Complete approved Meta onboarding, then a local operator verifies the account and subscription."}


def _exchange_code(code: str, transport: httpx.BaseTransport | None) -> dict:
    """Meta takes the app secret as a query value here, so httpx request logging stays off."""
    settings = get_settings()
    client = GuardedClient(("graph.facebook.com",), transport=transport)
    try:
        response = client.get(f"https://graph.facebook.com/{settings.meta_graph_version}/oauth/access_token",
                              params={"client_id": settings.meta_app_id, "client_secret": settings.meta_app_secret, "code": code})
    except httpx.HTTPError as exc:
        raise GluonsError("EXTERNAL_UNAVAILABLE", "Meta could not be reached. Start the connection again.", status=502) from exc
    finally:
        client.close()
    try:
        result = response.json()
    except ValueError:
        result = {}
    if response.status_code >= 400 or not isinstance(result, dict) or not result.get("access_token"):
        raise GluonsError("EXTERNAL_REJECTED", "Meta did not issue a business token. Start the connection again.", status=502)
    return result


def _provision(token: str, accounts: list[str], phone_number_id: str, coexistence: bool, pin: str | None,
               transport: httpx.BaseTransport | None) -> dict:
    """Owner account consent rather than business automation, like a Google token exchange."""
    client = MetaClient(token, transport=transport)
    try:
        with write_permit("owner_whatsapp_onboarding"):
            for account in accounts:
                client.call("POST", f"{account}/subscribed_apps")
            if not coexistence:
                # Coexistence numbers stay registered to the Business app, and Meta says to skip this.
                client.call("POST", f"{phone_number_id}/register", json={"messaging_product": "whatsapp", "pin": pin or f"{secrets.randbelow(1_000_000):06d}"})
        return client.call("GET", phone_number_id, params={"fields": "display_phone_number,verified_name"})
    finally:
        client.close()


def complete_signup(principal: Principal, session_id: str, signup: dict, *, transport: httpx.BaseTransport | None = None) -> dict:
    """Binds the session to the browser that opened it. A failure is recorded and the code is spent."""
    _browser(principal)
    event = signup.get("event")
    finished = event in ("FINISH", "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING")
    accounts = [str(account) for account in signup.get("messaging_account_ids") or []]
    phone_number_id = str(signup.get("phone_number_id") or "")
    pin = signup.get("pin")
    if finished:
        if not signup.get("code") or not accounts or len(accounts) > 10:
            raise GluonsError("VALIDATION_FAILED", "Signup did not return a code and a messaging account.", status=422)
        try:
            phone_number_id = identifier(phone_number_id)
            accounts = [identifier(account) for account in accounts]
        except ProviderError:
            raise GluonsError("VALIDATION_FAILED", "Signup returned an invalid Meta identifier.", status=422) from None
    if pin and not re.fullmatch(r"[0-9]{6}", str(pin)):
        raise GluonsError("VALIDATION_FAILED", "The registration PIN must be 6 digits.", status=422)
    coexistence = bool(signup.get("is_on_biz_app")) or event == "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING"
    failure: GluonsError | None = None
    result: dict = {}
    with operation_lock(f"whatsapp.bind:{phone_number_id or session_id}") as acquired:
        if not acquired:
            raise GluonsError("CONFLICT", "This number is being configured. Retry shortly.", status=409)
        with system_tx() as session:
            linked = session.scalars(select(Connection.tenant_id).where(Connection.provider == "whatsapp_business",
                Connection.external_account_id == phone_number_id, Connection.disconnected_at.is_(None))).first() if finished else None
        with tenant_tx(principal.tenant_id, principal.user_id) as session:
            row = session.get(ConnectionSession, session_id, with_for_update=True)
            if (row is None or row.provider != "whatsapp_business" or row.initiated_by != principal.actor_id
                    or row.extra.get("browser_session_id") != principal.session_id or row.expires_at <= utcnow()
                    or row.consumed_at or row.status != "pending"):
                raise GluonsError("CONNECTION_SESSION_INVALID", "This setup expired, was already used, or belongs to another session.", status=409)
            row.consumed_at = utcnow()
            try:
                if not finished:
                    raise GluonsError("OAUTH_DECLINED", "WhatsApp setup was not finished. Start again when ready.", status=400)
                if linked is not None:
                    raise GluonsError("ACCOUNT_ALREADY_LINKED", "Disconnect the existing Gluons connection before relinking this number.", status=409)
                credential = _exchange_code(str(signup["code"]), transport)
                try:
                    number = _provision(credential["access_token"], accounts, phone_number_id, coexistence, pin, transport)
                except ProviderError as exc:
                    raise GluonsError("PROVIDER_VERIFICATION_FAILED", "Meta refused an onboarding step. No connection was created.", status=409) from exc
                reference = vault.store(session, principal.tenant_id, "whatsapp_business", {"access_token": credential["access_token"]})
                expires_in = credential.get("expires_in")
                connection = Connection(id=new_id("con"), tenant_id=principal.tenant_id, provider="whatsapp_business",
                    environment=str((row.extra or {}).get("environment", "production")), external_account_id=phone_number_id,
                    account_display=str(number.get("display_phone_number", phone_number_id)), granted_scopes=SCOPES, credential_ref=reference,
                    status="connected", last_checked_at=utcnow(), created_by=principal.actor_id,
                    token_expires_at=utcnow() + timedelta(seconds=int(expires_in)) if expires_in else None,
                    metadata_json={"account_id": accounts[0], "account_ids": accounts, "phone_number_id": phone_number_id, "app_id": str(get_settings().meta_app_id),
                                   "verified_name": str(number.get("verified_name", "")), "subscription_verified": True, "coexistence": coexistence,
                                   "requested_capabilities": row.requested_capabilities, "api_version": get_settings().meta_graph_version,
                                   "business_id": signup.get("business_id"), "client_payment_method": "unknown"})
                session.add(connection)
                session.flush()
                row.status, row.connection_id = "authorized", connection.id
                audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="whatsapp.account_verified", actor=principal,
                             resource_type="connection", resource_id=connection.id, data={"account_ids": accounts, "phone_number_id": phone_number_id, "coexistence": coexistence})
                result = {"connection_id": connection.id, "status": "authorized", "account_display": connection.account_display, "account_confirmed": False,
                          "next_step": "Confirm the displayed number, then add a payment method in WhatsApp Manager.", "notices": [CHARGES_NOTICE, TERMS_RISK]}
            except GluonsError as exc:
                failure = exc
                row.status, row.failure_reason = "failed", exc.message
                audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="whatsapp.signup_failed", actor=principal,
                             resource_type="connection_session", resource_id=row.id, data={"reason": exc.code})
    if failure:
        raise failure
    return result


def bind_from_environment(principal: Principal, session_id: str, *, account_id: str, phone_number_id: str,
                          environment: str = "sandbox", transport: httpx.BaseTransport | None = None) -> dict:
    """Local operator function, intentionally absent from REST/MCP. Token stays in env/vault."""
    _browser(principal)
    account_id, phone_number_id = identifier(account_id), identifier(phone_number_id)
    if environment not in {"sandbox", "production"}:
        raise GluonsError("VALIDATION_FAILED", "Use sandbox or production environment.", status=422)
    token = os.environ.get("GLUONS_WHATSAPP_ACCESS_TOKEN")
    if not token or not get_settings().meta_app_id or not get_settings().meta_app_secret or not get_settings().meta_webhook_verify_token:
        raise GluonsError("CONFIGURATION_REQUIRED", "Configure Meta app, webhook secrets and the authorized account token in local environment or Secret Manager.", status=409)
    with operation_lock(f"whatsapp.bind:{phone_number_id}") as acquired:
        if not acquired:
            raise GluonsError("CONFLICT", "This number is being configured. Retry shortly.", status=409)
        # Phone mappings are globally unambiguous, even across separate businesses.
        with system_tx() as session:
            existing = session.execute(select(Connection.tenant_id).where(Connection.provider == "whatsapp_business",
                Connection.external_account_id == phone_number_id, Connection.disconnected_at.is_(None))).scalars().all()
        if existing:
            raise GluonsError("ACCOUNT_ALREADY_LINKED", "Disconnect the existing Gluons connection before relinking this number.", status=409)
        with tenant_tx(principal.tenant_id, principal.user_id) as session:
            row = session.get(ConnectionSession, session_id, with_for_update=True)
            if (row is None or row.provider != "whatsapp_business" or row.initiated_by != principal.actor_id
                    or row.extra.get("browser_session_id") != principal.session_id or row.expires_at <= utcnow()
                    or row.consumed_at or row.status != "needs_operator_verification"):
                raise GluonsError("CONNECTION_SESSION_INVALID", "Start a new owner-bound WhatsApp setup session.", status=409)
            client = MetaClient(token, transport=transport)
            try:
                permissions = client.call("GET", "me/permissions").get("data", [])
                granted = {p.get("permission") for p in permissions if p.get("status") == "granted"}
                if not set(SCOPES) <= granted:
                    raise GluonsError("PROVIDER_SCOPE_MISSING", "Meta did not verify the required account permissions.", status=409)
                phones = client.call("GET", f"{account_id}/phone_numbers", params={"fields": "id,display_phone_number,verified_name,code_verification_status", "limit": 100})
                number = next((p for p in phones.get("data", []) if p.get("id") == phone_number_id), None)
                if number is None or number.get("code_verification_status") != "VERIFIED":
                    raise GluonsError("PROVIDER_IDENTITY_UNVERIFIED", "Meta did not verify this number under the selected business account.", status=409)
                apps = client.call("GET", f"{account_id}/subscribed_apps").get("data", [])
                app_ids = {str((a.get("whatsapp_business_api_data") or a).get("id", "")) for a in apps}
                if str(get_settings().meta_app_id) not in app_ids:
                    raise GluonsError("WEBHOOK_SUBSCRIPTION_REQUIRED", "Subscribe the approved Gluons app in Meta before linking this account.", status=409)
            except ProviderError as exc:
                raise GluonsError("PROVIDER_VERIFICATION_FAILED", "Meta could not verify this token/account. No connection was created.", status=409) from exc
            finally:
                client.close()
            reference = vault.store(session, principal.tenant_id, "whatsapp_business", {"access_token": token})
            connection = Connection(id=new_id("con"), tenant_id=principal.tenant_id, provider="whatsapp_business", environment=environment,
                external_account_id=phone_number_id, account_display=str(number.get("display_phone_number", phone_number_id)),
                granted_scopes=SCOPES, credential_ref=reference, status="connected", last_checked_at=utcnow(), created_by=principal.actor_id,
                metadata_json={"account_id": account_id, "phone_number_id": phone_number_id, "app_id": str(get_settings().meta_app_id),
                               "verified_name": str(number.get("verified_name", "")), "subscription_verified": True,
                               "requested_capabilities": row.requested_capabilities,
                               "api_version": get_settings().meta_graph_version})
            session.add(connection)
            session.flush()
            row.status, row.connection_id, row.consumed_at = "authorized", connection.id, utcnow()
            audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="whatsapp.account_verified", actor=principal,
                         resource_type="connection", resource_id=connection.id, data={"environment": environment, "account_id": account_id, "phone_number_id": phone_number_id})
            return {"connection_id": connection.id, "status": "authorized", "account_display": connection.account_display,
                    "account_confirmed": False, "next_step": "Owner confirms the displayed account before activating any automation."}


def challenge(mode: str, verify_token: str, value: str) -> str:
    secret = get_settings().meta_webhook_verify_token
    if not secret or mode != "subscribe" or not constant_time_equals(secret, verify_token) or len(value) > 1000:
        raise GluonsError("WEBHOOK_VERIFICATION_FAILED", "Webhook verification failed.", status=403)
    return value


def verify_connection(principal: Principal, connection_id: str, *, transport: httpx.BaseTransport | None = None) -> dict:
    principal.require("connections:manage")
    failure = None
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        connection = session.get(Connection, connection_id, with_for_update=True)
        if connection is None or connection.provider != "whatsapp_business" or connection.disconnected_at or not connection.credential_ref:
            raise not_found("WhatsApp connection")
        client = MetaClient(vault.read(session, principal.tenant_id, connection.credential_ref)["access_token"], transport=transport)
        try:
            permissions = client.call("GET", "me/permissions").get("data", [])
            granted = {p.get("permission") for p in permissions if p.get("status") == "granted"}
            account_id = identifier(str(connection.metadata_json.get("account_id", "")))
            phones = client.call("GET", f"{account_id}/phone_numbers", params={"fields": "id,display_phone_number,code_verification_status", "limit": 100}).get("data", [])
            number = next((p for p in phones if p.get("id") == connection.external_account_id and p.get("code_verification_status") == "VERIFIED"), None)
            if not set(SCOPES) <= granted or number is None:
                raise ProviderError("auth", "Provider permission or number ownership was revoked.")
            connection.granted_scopes = sorted(granted)
            if connection.account_display != str(number.get("display_phone_number", connection.external_account_id)):
                connection.account_display = str(number.get("display_phone_number", connection.external_account_id))
                connection.account_confirmed_at, connection.account_confirmed_by = None, None
            connection.status, connection.status_detail = "connected", "Verified account access; owner action grants remain separate."
            connection.last_checked_at = utcnow()
        except ProviderError as exc:
            connection.status_detail = "Meta could not verify the connection. Reconnect or retry in Gluons."
            if exc.kind == "auth":
                connection.status = "needs_reauth"
            failure = GluonsError("CONNECTION_REAUTH_REQUIRED" if exc.kind == "auth" else "EXTERNAL_UNAVAILABLE", connection.status_detail, status=409)
        finally:
            client.close()
        result = {"id": connection.id, "provider": connection.provider, "status": connection.status, "environment": connection.environment,
                  "account_display": connection.account_display, "account_confirmed": connection.account_confirmed_at is not None,
                  "scopes": connection.granted_scopes, "last_checked_at": connection.last_checked_at.isoformat() if connection.last_checked_at else None}
    if failure:
        raise failure
    return result


def _timestamp(value: Any) -> datetime:
    try:
        moment = datetime.fromtimestamp(int(value), UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        raise GluonsError("WEBHOOK_INVALID", "A valid provider event timestamp is required.", status=400) from None
    if moment < utcnow() - timedelta(days=7) or moment > utcnow() + timedelta(minutes=5):
        raise GluonsError("WEBHOOK_EXPIRED", "This provider event falls outside the replay window.", status=400)
    return moment


def _records(value: Any) -> list[dict]:
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(item, dict) for item in value):
        raise GluonsError("WEBHOOK_INVALID", "Webhook records must be bounded objects.", status=400)
    return value


def receive_webhook(raw: bytes, signature: str) -> dict:
    secret = get_settings().meta_app_secret
    if not secret:
        raise GluonsError("CONFIGURATION_REQUIRED", "WhatsApp webhook verification is not configured.", status=503)
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise GluonsError("PAYLOAD_TOO_LARGE", "Webhook body exceeds the limit.", status=413)
    expected = "sha256=" + hmac_sha256_hex(secret.encode(), raw)
    if not constant_time_equals(expected, signature):
        raise GluonsError("WEBHOOK_SIGNATURE_INVALID", "Webhook signature is invalid.", status=403)
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
            raise ValueError
        entries = _records(payload.get("entry", []))
    except (ValueError, TypeError):
        raise GluonsError("WEBHOOK_INVALID", "Webhook body is invalid.", status=400) from None
    accepted, duplicates = 0, 0
    for entry in entries:
        for change in _records(entry.get("changes", [])):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            if not isinstance(value, dict) or not isinstance(value.get("metadata", {}), dict):
                raise GluonsError("WEBHOOK_INVALID", "Webhook message metadata is invalid.", status=400)
            phone_id = str((value.get("metadata") or {}).get("phone_number_id", ""))
            # Resolve only a provider-verified, owner-confirmed number mapping.
            with system_tx() as session:
                routes = session.execute(select(Connection).where(Connection.provider == "whatsapp_business",
                    Connection.external_account_id == phone_id, Connection.status == "connected", Connection.disconnected_at.is_(None),
                    Connection.account_confirmed_at.is_not(None))).scalars().all()
            if len(routes) != 1 or str(entry.get("id", "")) not in (routes[0].metadata_json.get("account_ids") or [routes[0].metadata_json.get("account_id")]):
                continue
            connection = routes[0]
            for kind in ("messages", "statuses"):
                if kind == "messages" and "read_messages" not in connection.metadata_json.get("requested_capabilities", []):
                    continue
                for event in _records(value.get(kind, [])):
                    _timestamp(event.get("timestamp"))
                    event_id = str(event.get("id", ""))
                    if not event_id or len(event_id) > 220:
                        raise GluonsError("WEBHOOK_INVALID", "Provider event identifier is invalid.", status=400)
                    suffix = ":" + str(event.get("status", "")) if kind == "statuses" else ""
                    key = f"{phone_id}:{kind}:{event_id}{suffix}"
                    event_hash = fingerprint(event)
                    with tenant_tx(connection.tenant_id) as session:
                        inbox_id = new_id("whk")
                        created = session.execute(insert(WebhookInbox).values(id=inbox_id, provider="whatsapp", event_id=key,
                            tenant_id=connection.tenant_id, connection_id=connection.id, event_type=kind,
                            payload_hash=event_hash, payload={"event": event, "phone_id": phone_id}, status="received")
                            .on_conflict_do_nothing(constraint="uq_webhook_provider_event").returning(WebhookInbox.id)).first()
                        if created is None:
                            duplicates += 1
                            continue
                        queue.enqueue(session, "whatsapp.process", {"inbox_id": inbox_id}, tenant_id=connection.tenant_id,
                                      dedupe_key=f"whatsapp:{inbox_id}")
                        accepted += 1
    return {"status": "accepted", "accepted": accepted, "duplicates": duplicates}


def _sender(value: Any) -> str:
    value = str(value)
    if not re.fullmatch(r"[0-9]{6,20}", value):
        raise GluonsError("WEBHOOK_INVALID", "Unsupported sender identity; human review is required.", status=400)
    return "+" + value


def process_event(tenant_id: str, inbox_id: str) -> None:
    """Durable worker entry. All record/submit operations are replay-safe."""
    with operation_lock(f"whatsapp.inbox:{inbox_id}") as acquired:
        if not acquired:
            raise GluonsError("CONFLICT", "This event is already being processed.", status=409)
        with tenant_tx(tenant_id) as session:
            inbox = session.get(WebhookInbox, inbox_id, with_for_update=True)
            if inbox is None or inbox.provider != "whatsapp" or inbox.status == "processed":
                return
            connection = session.get(Connection, inbox.connection_id)
            if connection is None or connection.status != "connected" or connection.disconnected_at or not connection.account_confirmed_at:
                inbox.status, inbox.error = "quarantined", "connection_unavailable"
                return
            event, kind = inbox.payload["event"], inbox.event_type
            if kind == "statuses":
                _apply_status(session, connection, event)
                inbox.status, inbox.processed_at = "processed", utcnow()
                return
            moment = _timestamp(event.get("timestamp"))
            sender = _sender(event.get("from", ""))
            thread_key = f"{connection.external_account_id}:{sender}"
            thread = session.execute(select(ChannelThread).where(ChannelThread.channel == CHANNEL, ChannelThread.external_thread_id == thread_key).with_for_update()).scalar_one_or_none()
            if thread is None:
                thread = ChannelThread(id=new_id("thr"), tenant_id=tenant_id, channel=CHANNEL, connection_id=connection.id,
                                       external_thread_id=thread_key, counterparty=sender)
                session.add(thread)
                session.flush()
            if thread.last_message_at is None or thread.last_message_at < moment:
                thread.last_message_at, thread.service_window_expires_at = moment, moment + timedelta(hours=24)
            text = str((event.get("text") or {}).get("body", ""))[:100_000]
            attachments = []
            if event.get("type") in SUPPORTED_MEDIA:
                media = event.get(event["type"], {})
                attachments.append({"provider_media_id": str(media.get("id", ""))[:100], "type": event["type"],
                                    "mime_type": str(media.get("mime_type", ""))[:100], "sha256": str(media.get("sha256", ""))[:100],
                                    "status": "requires_authorized_download"})
                text = str(media.get("caption", ""))[:100_000]
            message = session.execute(select(ChannelMessage).where(ChannelMessage.channel == CHANNEL, ChannelMessage.external_message_id == event["id"])).scalar_one_or_none()
            if message is None:
                session.add(ChannelMessage(id=new_id("msg"), tenant_id=tenant_id, thread_id=thread.id, channel=CHANNEL, direction="inbound",
                    external_message_id=event["id"], sender=sender, recipient=connection.account_display, body=text,
                    attachments=attachments, classification="order_candidate" if text else "human_review_required"))
            connection.last_event_at = utcnow()
            automations = []
            for automation in session.execute(select(Automation).where(Automation.skill_id == "multichannel_order_entry",
                    Automation.status.in_(("active", "trial_shadow", "trial_replay", "paused", "waiting_external")))).scalars():
                bound = (automation.config.get("connections") or {}).get("messaging")
                if bound != connection.id or not automation.config.get("whatsapp_ingest_enabled"):
                    continue
                if automation.mode in ("live", "supervised"):
                    grant = session.get(AutomationPolicy, automation.current_policy_id) if automation.current_policy_id else None
                    if (grant is None or grant.status != "active" or not policy.signature_valid(grant) or grant.mode != automation.mode
                            or "whatsapp.message.ingest" not in grant.allowed_actions or (grant.expires_at and grant.expires_at <= utcnow())
                            or (grant.connection_ids and connection.id not in grant.connection_ids)):
                        continue
                automations.append(automation.id)
        # submit_event owns its transaction; a restart safely resubmits the same ID.
        for automation_id in automations:
            lifecycle.submit_event(tenant_id, automation_id, source="whatsapp", source_event_id=f"whatsapp:{event['id']}",
                payload={"text": text, "phone": sender, "channel": "whatsapp", "thread_id": thread.id,
                         "attachments": attachments, "provider_message_id": event["id"]}, actor="whatsapp_webhook")
        with tenant_tx(tenant_id) as session:
            inbox = session.get(WebhookInbox, inbox_id, with_for_update=True)
            inbox.status, inbox.processed_at = "processed", utcnow()


def _apply_status(session: Any, connection: Connection, event: dict) -> None:
    callback = str(event.get("biz_opaque_callback_data", ""))
    query = select(ChannelMessage).join(ChannelThread).where(ChannelMessage.channel == CHANNEL, ChannelMessage.direction == "outbound",
        ChannelThread.connection_id == connection.id)
    query = query.where(ChannelMessage.subject == callback) if callback else query.where(ChannelMessage.external_message_id == event["id"])
    message = session.execute(query.with_for_update()).scalar_one_or_none()
    if message is None:
        return
    if message.external_message_id and message.external_message_id != event["id"]:
        return
    if _sender(event.get("recipient_id", "")) != message.recipient:
        message.status, message.provider_error = "mismatch", "provider_recipient_mismatch"
        return
    status = event.get("status")
    if status not in {"sent", "delivered", "read", "failed"}:
        return
    if status == "failed" and message.status in {"read", "delivered"}:
        message.status, message.provider_error = "mismatch", "contradictory_provider_delivery_status"
        audit.record(session, tenant_id=connection.tenant_id, category="channel", event_type="whatsapp.delivery_conflict",
                     actor="whatsapp_webhook", resource_type="channel_message", resource_id=message.id,
                     data={"provider_message_id": event["id"]})
        return
    if message.status == "read" or (message.status == "delivered" and status == "sent") or message.status == "mismatch":
        return
    message.external_message_id, message.status = event["id"], status
    message.provider_error = "provider_delivery_failed" if status == "failed" else None


def download_media(principal: Principal, connection_id: str, media_id: str, *, transport: httpx.BaseTransport | None = None) -> tuple[bytes, str]:
    principal.require("tasks:read")
    media_id = identifier(media_id)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        connection = session.get(Connection, connection_id)
        if connection is None or connection.provider != "whatsapp_business" or connection.status != "connected" or not connection.account_confirmed_at or not connection.credential_ref:
            raise not_found("WhatsApp connection")
        # A valid media ID is not sufficient authority: it must occur in this tenant's received message.
        messages = session.execute(select(ChannelMessage).join(ChannelThread).where(ChannelThread.connection_id == connection_id,
            ChannelMessage.direction == "inbound", ChannelMessage.channel == CHANNEL)).scalars()
        if not any(any(a.get("provider_media_id") == media_id for a in m.attachments) for m in messages):
            raise not_found("Received media")
        token = vault.read(session, principal.tenant_id, connection.credential_ref)["access_token"]
    graph = MetaClient(token, transport=transport)
    try:
        try:
            metadata = graph.call("GET", media_id)
        except ProviderError as exc:
            raise GluonsError("MEDIA_UNAVAILABLE", "Meta could not authorize or retrieve this media. Reconnect or retry.", status=409) from exc
    finally:
        graph.close()
    mime = str(metadata.get("mime_type", ""))
    try:
        size = int(metadata.get("file_size", MAX_MEDIA_BYTES + 1))
        media_url = str(metadata.get("url", ""))
        parts = urlsplit(media_url)
        secure = parts.scheme == "https" and parts.port in (None, 443) and not parts.username and not parts.password
    except (TypeError, ValueError):
        raise GluonsError("MEDIA_UNAVAILABLE", "Provider media metadata is invalid.", status=409) from None
    if not secure or not 0 <= size <= MAX_MEDIA_BYTES or mime not in {"application/pdf", "text/plain", "image/jpeg", "image/png", "audio/ogg", "audio/mpeg"}:
        raise GluonsError("MEDIA_LIMIT", "This media type or size requires manual review.", status=422)
    client = GuardedClient(("lookaside.fbsbx.com", "mmg.whatsapp.net"), headers={"Authorization": f"Bearer {token}"}, transport=transport)
    try:
        try:
            content, _headers, status = client.get_bytes(media_url, max_bytes=MAX_MEDIA_BYTES)
        except (ResponseTooLarge, HostNotAllowed, httpx.HTTPError) as exc:
            raise GluonsError("MEDIA_UNAVAILABLE", "The provider could not return permitted bounded media.", status=409) from exc
        if status != 200:
            raise GluonsError("MEDIA_UNAVAILABLE", "The provider could not return bounded media.", status=409)
        import base64
        import hashlib
        digest = hashlib.sha256(content)
        if metadata.get("sha256") and metadata["sha256"] not in {digest.hexdigest(), base64.b64encode(digest.digest()).decode()}:
            raise GluonsError("MEDIA_HASH_MISMATCH", "Provider media failed its integrity check.", status=409)
        return content, mime
    finally:
        client.close()
