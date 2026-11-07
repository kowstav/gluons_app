"""Owner-bound, expiring account linking. A connection never grants permission to act."""
from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select

from gluons import audit, vault
from gluons.authz import Principal
from gluons.config import get_settings
from gluons.connections import google
from gluons.core import GluonsError, new_id, new_secret_token, not_found, token_hash, utcnow
from gluons.db.models import Automation, Connection, ConnectionSession, LaunchPlan, Plan, ProviderEventSubscription
from gluons.db.session import tenant_tx

PROVIDER_REQUIREMENTS = {
    "gmail": "Configure Google Web OAuth client and consent screen; authorize the registered callback.",
    "whatsapp_business": "Open this setup in Gluons Web and choose Connect WhatsApp Business. Meta's own window handles the sign-in.",
    "shopify": "Install Gluons from Shopify, then choose this business. Approved pilot shops can also connect here by domain.",
    "elevenlabs": "Set up a phone route on the Business channels page. A real inbound test call is required.",
    "erpnext": "An authorized ERPNext service account and API endpoint are required; hosted connection setup is not built yet.",
}


def _owner_browser(principal: Principal) -> None:
    principal.require("connections:manage")
    if principal.kind != "user" or not principal.session_id:
        raise GluonsError("OWNER_WEB_REQUIRED", "Continue account authorization in Gluons Web.", status=403)


def _session(row: ConnectionSession) -> dict[str, Any]:
    status = "expired" if row.expires_at <= utcnow() and row.consumed_at is None else row.status
    return {
        "session_id": row.id, "provider": row.provider, "status": status,
        "expires_at": row.expires_at.isoformat(), "connection_id": row.connection_id,
        "requested_capabilities": row.requested_capabilities, "requested_scopes": row.requested_scopes,
        "next_step": row.failure_reason or PROVIDER_REQUIREMENTS.get(row.provider),
        "setup_url": f"{get_settings().public_web_url.rstrip('/')}/setup/{row.id}",
    }


def _connection(row: Connection) -> dict[str, Any]:
    return {
        "id": row.id, "provider": row.provider, "account_display": row.account_display,
        "external_account_id": row.external_account_id, "scopes": row.granted_scopes,
        "status": row.status, "environment": row.environment, "version": row.version,
        "account_confirmed": row.account_confirmed_at is not None,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "token_expires_at": row.token_expires_at.isoformat() if row.token_expires_at else None,
        "health": row.status_detail,
    }


def begin_session(principal: Principal, provider: str, *, capabilities: list[str], automation_plan_id: str | None = None) -> dict:
    principal.require("connections:manage")
    if provider == "whatsapp" or provider == "whatsapp_personal":
        raise GluonsError("PERSONAL_WHATSAPP_UNSUPPORTED", "Use the official WhatsApp Business Platform onboarding.", status=422)
    if provider not in PROVIDER_REQUIREMENTS:
        raise GluonsError("PROVIDER_UNSUPPORTED", "This provider is not available.", status=422)
    scopes = google.scopes_for(capabilities) if provider == "gmail" else []
    configured = provider == "gmail" and google.configured()
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        if automation_plan_id and session.get(Plan, automation_plan_id) is None and session.get(LaunchPlan, automation_plan_id) is None:
            raise not_found("Automation plan")
        row = ConnectionSession(
            id=new_id("cns"), tenant_id=principal.tenant_id, initiated_by=principal.actor_id,
            initiated_via=principal.via, binding_id=principal.binding_id, provider=provider,
            requested_capabilities=capabilities, requested_scopes=scopes,
            automation_plan_id=automation_plan_id, setup_handle_hash=token_hash(new_secret_token()),
            status="requires_external_authorization" if configured else "needs_configuration",
            expires_at=utcnow() + timedelta(seconds=get_settings().connection_session_ttl_seconds),
        )
        session.add(row)
        session.flush()
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="connection.session_created",
                     actor=principal, resource_type="connection_session", resource_id=row.id, data={"provider": provider, "scopes": scopes})
        return _session(row)


def session_status(principal: Principal, session_id: str) -> dict:
    principal.require("connections:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(ConnectionSession, session_id)
        if row is None or row.initiated_by != principal.actor_id:
            raise not_found("Connection session")
        return _session(row)


def authorize(principal: Principal, session_id: str) -> dict:
    _owner_browser(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(ConnectionSession, session_id, with_for_update=True)
        if row is None or row.initiated_by != principal.actor_id:
            raise not_found("Connection session")
        if row.expires_at <= utcnow() or row.consumed_at is not None or row.status == "pending":
            raise GluonsError("CONNECTION_SESSION_INVALID", "Start a new connection session.", status=409)
        if row.provider != "gmail" or not google.configured():
            raise GluonsError("CONFIGURATION_REQUIRED", PROVIDER_REQUIREMENTS[row.provider], status=409)
        verifier = new_secret_token(nbytes=48)
        state = new_secret_token()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        row.state_hash = token_hash(state)
        row.pkce_verifier_ref = vault.store(session, principal.tenant_id, "google_oauth_pkce", {"verifier": verifier})
        row.extra = {"browser_session_id": principal.session_id}
        row.status = "pending"
        return {"authorization_url": google.authorization_url(row.requested_scopes, state, challenge), **_session(row)}


def cancel_session(principal: Principal, session_id: str) -> dict:
    _owner_browser(principal)
    connection_id = None
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(ConnectionSession, session_id, with_for_update=True)
        if row is None or row.initiated_by != principal.actor_id:
            raise not_found("Connection session")
        row.consumed_at = row.consumed_at or utcnow()
        row.status = "cancelled"
        if row.pkce_verifier_ref:
            vault.destroy(session, principal.tenant_id, row.pkce_verifier_ref)
        connection_id = row.connection_id
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="connection.session_cancelled", actor=principal, resource_type="connection_session", resource_id=row.id)
        result = _session(row)
    if connection_id:
        result["disconnect"] = disconnect(principal, connection_id)
    return result


def callback(principal: Principal, *, state: str, code: str | None, error: str | None = None) -> dict:
    _owner_browser(principal)
    failure: GluonsError | None = None
    result: dict = {}
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.execute(select(ConnectionSession).where(ConnectionSession.state_hash == token_hash(state)).with_for_update()).scalar_one_or_none()
        if (row is None or row.initiated_by != principal.actor_id or row.extra.get("browser_session_id") != principal.session_id
                or row.expires_at <= utcnow() or row.consumed_at is not None or row.status != "pending"):
            raise GluonsError("OAUTH_STATE_INVALID", "This authorization is expired, already used, or belongs to another session.", status=400)
        row.consumed_at = utcnow()
        try:
            if error or not code:
                raise GluonsError("OAUTH_DECLINED", "Google authorization was declined. Start again when ready.", status=400)
            verifier = vault.read(session, principal.tenant_id, row.pkce_verifier_ref)["verifier"]
            credential = google.exchange(code, verifier)
            granted = set(credential.get("scope", "").split())
            required = {s for s in row.requested_scopes if s.startswith("https://www.googleapis.com/auth/gmail.")}
            if not required <= granted:
                raise GluonsError("SCOPE_INSUFFICIENT", "Google did not grant the requested Gmail permissions. Reconnect with the required scopes.", status=409)
            account = google.identity(credential["access_token"])
            connection = session.execute(select(Connection).where(Connection.provider == "gmail", Connection.external_account_id == account["sub"], Connection.disconnected_at.is_(None)).with_for_update()).scalar_one_or_none()
            if connection is None:
                connection = Connection(id=new_id("con"), tenant_id=principal.tenant_id, provider="gmail", external_account_id=account["sub"], account_display=account["email"], created_by=principal.actor_id)
                session.add(connection)
            elif connection.credential_ref:
                old_credential = vault.read(session, principal.tenant_id, connection.credential_ref)
                if not credential.get("refresh_token") and old_credential.get("refresh_token"):
                    credential["refresh_token"] = old_credential["refresh_token"]
                vault.destroy(session, principal.tenant_id, connection.credential_ref)
            connection.credential_ref = vault.store(session, principal.tenant_id, "gmail", credential)
            connection.granted_scopes = sorted(granted)
            connection.status = "connected"
            connection.status_detail = "Confirm the displayed account before activation. Gmail public launch requires provider verification."
            connection.account_display = account["email"]
            connection.metadata_json = {"account_email": account["email"]}
            connection.account_confirmed_at = None
            connection.account_confirmed_by = None
            connection.token_expires_at = utcnow() + timedelta(seconds=int(credential.get("expires_in", 3600)))
            connection.last_checked_at = utcnow()
            session.flush()
            row.connection_id = connection.id
            row.status = "authorized"
            audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="connection.authorized",
                         actor=principal, resource_type="connection", resource_id=connection.id, data={"provider": "gmail", "scopes": sorted(granted)})
            result = _session(row)
        except (GluonsError, httpx.HTTPError) as exc:
            failure = exc if isinstance(exc, GluonsError) else GluonsError("EXTERNAL_UNAVAILABLE", "Google is unavailable. Start a new connection session.", status=502)
            row.status = "failed"
            row.failure_reason = failure.message
            audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="connection.authorization_failed",
                         actor=principal, resource_type="connection_session", resource_id=row.id, data={"reason": failure.code})
        finally:
            if row.pkce_verifier_ref:
                vault.destroy(session, principal.tenant_id, row.pkce_verifier_ref)
    if failure:
        raise failure
    return result


def list_connections(principal: Principal) -> list[dict]:
    principal.require("connections:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return [_connection(c) for c in session.execute(select(Connection).order_by(Connection.created_at.desc())).scalars()]


def get_credential(session: Any, tenant_id: str, connection: Connection) -> dict:
    if connection.status != "connected" or connection.disconnected_at or not connection.credential_ref:
        raise GluonsError("CONNECTION_REQUIRED", "Reconnect this account in Gluons.", status=409)
    credential = vault.read(session, tenant_id, connection.credential_ref)
    if connection.provider == "gmail" and connection.token_expires_at and connection.token_expires_at <= utcnow() + timedelta(seconds=30):
        credential = google.refresh(credential)
        vault.replace(session, tenant_id, connection.credential_ref, credential)
        connection.token_expires_at = utcnow() + timedelta(seconds=int(credential.get("expires_in", 3600)))
    return credential


def verify_connection(principal: Principal, connection_id: str) -> dict:
    principal.require("connections:manage")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        provider_row = session.get(Connection, connection_id)
        provider_name = provider_row.provider if provider_row else None
    if provider_name == "whatsapp_business":
        from gluons.whatsapp.service import verify_connection as verify_whatsapp
        return verify_whatsapp(principal, connection_id)
    if provider_name == "shopify":
        from gluons.commerce.service import verify_connection as verify_shopify
        return verify_shopify(principal, connection_id)
    failure = None
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(Connection, connection_id, with_for_update=True)
        if row is None:
            raise not_found("Connection")
        if row.provider != "gmail":
            raise GluonsError("VERIFICATION_UNAVAILABLE", "This provider requires a real provider-specific verification test.", status=409)
        try:
            credential = get_credential(session, principal.tenant_id, row)
            account = google.identity(credential["access_token"])
            if account["sub"] != row.external_account_id:
                raise GluonsError("PROVIDER_IDENTITY_MISMATCH", "The authorized account changed. Reconnect.", status=409)
            row.last_checked_at = utcnow()
        except (GluonsError, httpx.HTTPError) as exc:
            failure = exc if isinstance(exc, GluonsError) else GluonsError("EXTERNAL_UNAVAILABLE", "Google is unavailable.", status=502)
            if failure.code in ("CONNECTION_REAUTH_REQUIRED", "PROVIDER_IDENTITY_MISMATCH", "PROVIDER_IDENTITY_UNVERIFIED"):
                row.status = "needs_reconnect"
            row.status_detail = failure.message
        result = _connection(row)
    if failure:
        raise failure
    return result


def confirm_account(principal: Principal, connection_id: str) -> dict:
    _owner_browser(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(Connection, connection_id, with_for_update=True)
        if row is None:
            raise not_found("Connection")
        if row.status != "connected":
            raise GluonsError("CONNECTION_REQUIRED", "Verify the provider connection first.", status=409)
        row.account_confirmed_at = utcnow()
        row.account_confirmed_by = principal.actor_id
        row.version += 1
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="connection.account_confirmed", actor=principal, resource_type="connection", resource_id=row.id)
        return _connection(row)


def disconnect(principal: Principal, connection_id: str) -> dict:
    principal.require("connections:manage")
    credential = None
    provider = None
    affected = []
    # The local fence commits before any potentially failing provider revocation.
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(Connection, connection_id, with_for_update=True)
        if row is None:
            raise not_found("Connection")
        if row.disconnected_at:
            return {"status": "disconnected", "affected_automations": []}
        provider = row.provider
        if row.credential_ref:
            credential = vault.read(session, principal.tenant_id, row.credential_ref)
            vault.destroy(session, principal.tenant_id, row.credential_ref)
            row.credential_ref = None
        row.status = "disconnected"
        row.disconnected_at = utcnow()
        row.disconnected_by = principal.actor_id
        row.version += 1
        for automation in session.execute(select(Automation).with_for_update()).scalars():
            bound = set((automation.config.get("connections") or {}).values()) | set(automation.source_connections or [])
            if connection_id in bound and automation.status not in ("cancelled", "cancel_requested", "completed", "failed_terminal"):
                automation.status = "waiting_external"
                automation.version += 1
                affected.append(automation.id)
        for subscription in session.execute(select(ProviderEventSubscription).where(ProviderEventSubscription.connection_id == connection_id)).scalars():
            subscription.status = "disabled"
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="connection.disconnected", actor=principal, resource_type="connection", resource_id=row.id, data={"affected_automations": affected})
    revocation = "not_supported"
    if provider == "gmail" and credential:
        try:
            google.revoke(credential)
            revocation = "revoked"
        except (GluonsError, httpx.HTTPError):
            revocation = "manual_provider_revocation_required"
    return {"status": "disconnected", "provider_revocation": revocation, "affected_automations": affected,
            "disclosure": "New provider actions are blocked. Actions already in progress may finish; sent messages cannot be recalled."}
