"""Durable owner setup state; provider authority stays with connection sessions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from gluons import audit
from gluons.authz import Principal, Role
from gluons.connections import google
from gluons.connections import service as connection_service
from gluons.core import (
    conflict,
    conflict_version,
    constant_time_equals,
    fingerprint,
    forbidden,
    new_id,
    new_secret_token,
    not_found,
    token_hash,
    utcnow,
    validation_failed,
)
from gluons.db.models import Connection, ConnectionSession, LaunchPlan, Organization
from gluons.db.session import tenant_tx

MAX_SESSIONS = 50


def _required_scopes(connection: ConnectionSession) -> set[str]:
    if connection.provider == "gmail":
        return {scope for scope in connection.requested_scopes if scope.startswith("https://www.googleapis.com/auth/gmail.")}
    return set(connection.requested_scopes)


def _gmail_account_action(account: Connection | None, required_scopes: set[str]) -> str | None:
    if (account is None or account.provider != "gmail" or not account.external_account_id
            or account.status != "connected" or account.disconnected_at is not None or not account.credential_ref
            or account.metadata_json.get("account_email") != account.account_display
            or (account.token_expires_at is not None and account.token_expires_at <= utcnow())):
        return "reconnect_account"
    if account.account_confirmed_at is None:
        return "confirm_account"
    if (account.last_checked_at is None or account.last_checked_at < utcnow() - timedelta(hours=24)
            or not required_scopes.issubset(set(account.granted_scopes))):
        return "verify_account"
    return None


def _owner(principal: Principal) -> None:
    principal.require("org:manage")
    if principal.kind != "user" or principal.role != Role.OWNER or not principal.user_id or not principal.session_id:
        raise forbidden("An authenticated business owner must manage setup in Gluons Web.")


def _org(session, principal: Principal, *, lock: bool = False) -> Organization:
    org = session.get(Organization, principal.tenant_id, with_for_update=lock)
    if org is None:
        raise not_found("Organization")
    return org


def _expiry(item: dict[str, Any]) -> datetime:
    # Earlier development records did not persist a deadline.
    if item.get("expires_at"):
        return datetime.fromisoformat(item["expires_at"])
    return datetime.fromisoformat(item["created_at"]) + timedelta(days=30)


def _item(org: Organization, setup_id: str, principal: Principal, *, active: bool = False) -> dict[str, Any]:
    item = org.setup_sessions.get(setup_id)
    if item is None or item["created_by"] != principal.user_id:
        raise not_found("Setup session")
    if active and _expiry(item) <= utcnow():
        raise conflict("This setup expired. Start a new setup for the existing plan.")
    return deepcopy(item)


def _view(item: dict[str, Any], connection: ConnectionSession | None = None, account: Connection | None = None) -> dict[str, Any]:
    view = {key: item.get(key) for key in ("id", "intent", "launch_plan_id", "status", "version", "created_at", "expires_at", "connection_session_id")}
    if connection is not None:
        status = "expired" if connection.expires_at <= utcnow() and connection.consumed_at is None else connection.status
        view["connection"] = {"id": connection.id, "provider": connection.provider, "status": status,
                              "connection_id": connection.connection_id, "requested_scopes": connection.requested_scopes,
                              "owner_url": f"/setup/{connection.id}"}
        if status == "needs_configuration":
            actions = [{"code": "configure_provider", "label": "Configure the provider integration"}]
        elif status in {"expired", "cancelled", "failed"}:
            actions = [{"code": "restart_connection", "label": "Restart the connection"}]
        elif status == "authorized":
            if (account is None or account.provider != connection.provider or not account.external_account_id
                    or account.status != "connected" or account.disconnected_at is not None or not account.credential_ref
                    or (account.provider == "gmail" and account.metadata_json.get("account_email") != account.account_display)
                    or (account.token_expires_at is not None and account.token_expires_at <= utcnow())):
                actions = [{"code": "reconnect_account", "label": "Reconnect the account", "owner_url": "/connections"}]
            elif account.account_confirmed_at is None:
                actions = [{"code": "confirm_account", "label": "Review and confirm the connected account", "owner_url": "/connections"}]
            elif (account.last_checked_at is None or account.last_checked_at < utcnow() - timedelta(hours=24)
                  or not _required_scopes(connection).issubset(set(account.granted_scopes))):
                actions = [{"code": "verify_account", "label": "Verify the account and required access", "owner_url": "/connections"}]
            else:
                actions = []
        else:
            actions = [{"code": "continue_connection", "label": "Continue connecting the account", "owner_url": f"/setup/{connection.id}"}]
    else:
        view["connection"] = None
        actions = [{"code": "connect_provider", "label": "Choose a provider to connect"}]
    view["expires_at"] = _expiry(item).isoformat()
    if _expiry(item) <= utcnow():
        view["status"] = "expired"
        actions = [{"code": "restart_setup", "label": "Start a new setup for this plan"}]
    view["next_actions"] = actions
    view["notice"] = "Setup state does not authorize provider access, sharing, or business actions."
    return view


def _connection(session, item: dict[str, Any], principal: Principal) -> ConnectionSession | None:
    session_id = item.get("connection_session_id")
    row = session.get(ConnectionSession, session_id) if session_id else None
    if row is not None and row.initiated_by != principal.user_id:
        return None
    return row


def _render(session, item: dict[str, Any], principal: Principal) -> dict[str, Any]:
    connection = _connection(session, item, principal)
    account = session.get(Connection, connection.connection_id) if connection and connection.connection_id else None
    result = _view(item, connection, account)
    reused_id = item.get("reused_connection_id")
    if connection is None and reused_id and result["status"] != "expired":
        account = session.get(Connection, reused_id)
        scopes = set(google.scopes_for(item["reused_capabilities"]))
        required = {scope for scope in scopes if scope.startswith("https://www.googleapis.com/auth/gmail.")}
        action = _gmail_account_action(account, required)
        result["connection"] = {"kind": "existing_connection", "id": reused_id, "provider": "gmail",
                                "account_display": account.account_display if account else None,
                                "status": "ready" if action is None else "action_required", "owner_url": "/connections"}
        labels = {"reconnect_account": "Reconnect the account", "confirm_account": "Review and confirm the connected account",
                  "verify_account": "Verify the account and required access"}
        result["next_actions"] = [] if action is None else [{"code": action, "label": labels[action], "owner_url": "/connections"}]
    return result


def start(principal: Principal, *, intent: str, launch_plan_id: str | None, idempotency_key: str) -> dict[str, Any]:
    _owner(principal)
    if intent != "business_launch" or not 8 <= len(idempotency_key) <= 200:
        raise validation_failed("Use a supported setup intent and a valid idempotency key.")
    key_hash = fingerprint({"owner": principal.user_id, "key": idempotency_key})
    request_hash = fingerprint({"intent": intent, "launch_plan_id": launch_plan_id})
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = _org(session, principal, lock=True)
        records = deepcopy(org.setup_sessions)
        for item in records.values():
            if item.get("idempotency_hash") == key_hash:
                if item.get("request_hash") != request_hash:
                    raise conflict("This setup idempotency key belongs to another request.")
                return _render(session, item, principal)
        if len(records) >= MAX_SESSIONS:
            raise conflict("This business has reached its setup-session limit.")
        if launch_plan_id and session.get(LaunchPlan, launch_plan_id) is None:
            raise not_found("Business launch plan")
        item = {"id": new_id("set"), "intent": intent, "launch_plan_id": launch_plan_id, "status": "in_progress",
                "version": 1, "created_at": utcnow().isoformat(), "expires_at": (utcnow() + timedelta(days=30)).isoformat(), "created_by": principal.user_id,
                "connection_session_id": None, "reused_connection_id": None, "reused_capabilities": [],
                "idempotency_hash": key_hash, "request_hash": request_hash,
                "link_hash": None, "link_expires_at": None}
        records[item["id"]] = item
        org.setup_sessions = records
        audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="setup.created", actor=principal,
                     resource_type="setup_session", resource_id=item["id"], data={"intent": intent})
        return _view(item)


def get(principal: Principal, setup_id: str) -> dict[str, Any]:
    _owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        item = _item(_org(session, principal), setup_id, principal)
        return _render(session, item, principal)


def list_sessions(principal: Principal) -> list[dict[str, Any]]:
    _owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = _org(session, principal)
        records = [deepcopy(item) for item in org.setup_sessions.values() if item.get("created_by") == principal.user_id]
        return [_render(session, item, principal) for item in sorted(records, key=lambda row: row["created_at"], reverse=True)]


def record_ui_measurement(principal: Principal, setup_id: str, *, kind: str, seconds: int | None = None) -> dict[str, str]:
    """Record bounded, client-estimated interaction metrics without field values."""
    _owner(principal)
    if ((kind == "active_seconds" and (type(seconds) is not int or not 1 <= seconds <= 300))
            or (kind == "help_opened" and seconds is not None)
            or kind not in {"active_seconds", "help_opened"}):
        raise validation_failed("Choose a supported setup measurement.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        _item(_org(session, principal), setup_id, principal, active=True)
        detail: dict[str, Any] = {"basis": "client_estimated"}
        if seconds is not None:
            detail["seconds"] = seconds
        audit.record(session, tenant_id=principal.tenant_id, category="setup_measurement", event_type=f"setup.{kind}",
                     actor=principal, resource_type="setup_session", resource_id=setup_id, data=detail)
    return {"status": "recorded", "basis": "client_estimated"}


def remove_expired(principal: Principal, setup_id: str, expected_version: int) -> dict[str, Any]:
    """Remove expired setup progress without disconnecting a provider account."""
    _owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = _org(session, principal, lock=True)
        item = _item(org, setup_id, principal)
        if item["version"] != expected_version:
            raise conflict_version(item["version"])
        if _expiry(item) > utcnow():
            raise conflict("Only expired setup progress can be removed.")
        connection = _connection(session, item, principal)
        if (connection is not None and connection.consumed_at is None and connection.expires_at > utcnow()
                and connection.status not in {"cancelled", "failed", "authorized"}):
            raise conflict("Finish or cancel the active connection handoff before removing setup progress.")
        records = deepcopy(org.setup_sessions)
        del records[setup_id]
        org.setup_sessions = records
        audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="setup.expired_removed", actor=principal,
                     resource_type="setup_session", resource_id=setup_id, data={})
        return {"id": setup_id, "removed": True, "notice": "Provider accounts and completed actions are unchanged."}


def attach_connection(principal: Principal, setup_id: str, connection_session_id: str, expected_version: int) -> dict[str, Any]:
    _owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = _org(session, principal, lock=True)
        item = _item(org, setup_id, principal, active=True)
        if item["version"] != expected_version:
            raise conflict_version(item["version"])
        connection = session.get(ConnectionSession, connection_session_id)
        if (connection is None or connection.initiated_by != principal.user_id
                or (item["launch_plan_id"] and connection.automation_plan_id != item["launch_plan_id"])):
            raise not_found("Connection session")
        if item.get("reused_connection_id") or item["connection_session_id"] not in (None, connection.id):
            raise conflict("This setup already references a different connection session.")
        item.update(connection_session_id=connection.id, reused_connection_id=None, reused_capabilities=[])
        item["version"] += 1
        records = deepcopy(org.setup_sessions)
        records[setup_id] = item
        org.setup_sessions = records
        audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="setup.connection_attached", actor=principal,
                     resource_type="setup_session", resource_id=setup_id, data={"provider": connection.provider})
        return _render(session, item, principal)


def reuse_connection(principal: Principal, setup_id: str, *, connection_id: str, capabilities: list[str],
                     expected_version: int) -> dict[str, Any]:
    """Bind only an owner-selected, confirmed and recently verified Gmail account."""
    _owner(principal)
    scopes = set(google.scopes_for(capabilities))
    required = {scope for scope in scopes if scope.startswith("https://www.googleapis.com/auth/gmail.")}
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = _org(session, principal, lock=True)
        item = _item(org, setup_id, principal, active=True)
        if (item.get("reused_connection_id") == connection_id
                and item.get("reused_capabilities") == sorted(set(capabilities))):
            return _render(session, item, principal)
        if item["version"] != expected_version:
            raise conflict_version(item["version"])
        if item.get("connection_session_id") or item.get("reused_connection_id"):
            raise conflict("This setup already references an account or connection handoff.")
        account = session.get(Connection, connection_id, with_for_update=True)
        if _gmail_account_action(account, required) is not None:
            raise conflict("Select a confirmed Gmail account with current health and the required access.")
        item.update(reused_connection_id=account.id, reused_capabilities=sorted(set(capabilities)), version=item["version"] + 1)
        records = deepcopy(org.setup_sessions)
        records[setup_id] = item
        org.setup_sessions = records
        audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="setup.connection_reused",
                     actor=principal, resource_type="setup_session", resource_id=setup_id,
                     data={"provider": "gmail", "capabilities": sorted(set(capabilities))})
        return _render(session, item, principal)


def request_connection(principal: Principal, setup_id: str, *, provider: str, capabilities: list[str],
                       expected_version: int, idempotency_key: str) -> dict[str, Any]:
    _owner(principal)
    if provider != "gmail" or not 8 <= len(idempotency_key) <= 200:
        raise validation_failed("Choose the supported Gmail handoff and a valid idempotency key.")
    requested_scopes = set(google.scopes_for(capabilities))
    requested_gmail_scopes = {scope for scope in requested_scopes if scope.startswith("https://www.googleapis.com/auth/gmail.")}
    request_hash = fingerprint({"provider": provider, "capabilities": sorted(capabilities)})
    key_hash = fingerprint({"owner": principal.user_id, "key": idempotency_key})
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = _org(session, principal, lock=True)
        item = _item(org, setup_id, principal, active=True)
        existing = _connection(session, item, principal)
        if item.get("connection_key_hash") == key_hash:
            if item.get("connection_request_hash") != request_hash:
                raise conflict("This connection request key belongs to a different request.")
            return _render(session, item, principal)
        if item["version"] != expected_version:
            raise conflict_version(item["version"])
        if item.get("reused_connection_id"):
            account = session.get(Connection, item["reused_connection_id"])
            if _gmail_account_action(account, requested_gmail_scopes) is None:
                raise conflict("Continue with the confirmed account already selected for this setup.")
        if existing is not None and existing.status not in {"cancelled", "failed"} and existing.expires_at > utcnow():
            account = session.get(Connection, existing.connection_id) if existing.connection_id else None
            if (existing.status != "authorized" or (account is not None and account.provider == existing.provider
                    and account.external_account_id and account.status == "connected"
                    and account.disconnected_at is None and account.credential_ref
                    and (account.token_expires_at is None or account.token_expires_at > utcnow())
                    and (account.provider != "gmail" or account.metadata_json.get("account_email") == account.account_display)
                    and requested_gmail_scopes.issubset(set(account.granted_scopes)))):
                raise conflict("Continue the existing connection handoff before starting another.")
        created = connection_service.begin_session_in_transaction(session, principal, provider, capabilities=capabilities,
                                                                  automation_plan_id=item["launch_plan_id"])
        connection = session.get(ConnectionSession, created["session_id"])
        item.update(connection_session_id=connection.id, reused_connection_id=None, reused_capabilities=[], connection_key_hash=key_hash,
                    connection_request_hash=request_hash, version=item["version"] + 1)
        records = deepcopy(org.setup_sessions)
        records[setup_id] = item
        org.setup_sessions = records
        audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="setup.connection_requested",
                     actor=principal, resource_type="setup_session", resource_id=setup_id, data={"provider": provider})
        return _render(session, item, principal)


def issue_resume_token(principal: Principal, setup_id: str, expected_version: int) -> dict[str, Any]:
    _owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = _org(session, principal, lock=True)
        item = _item(org, setup_id, principal, active=True)
        if item["version"] != expected_version:
            raise conflict_version(item["version"])
        secret = new_secret_token()
        expires = utcnow() + timedelta(minutes=15)
        item.update(link_hash=token_hash(secret), link_expires_at=expires.isoformat(), version=item["version"] + 1)
        records = deepcopy(org.setup_sessions)
        records[setup_id] = item
        org.setup_sessions = records
        audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="setup.resume_token_issued", actor=principal,
                     resource_type="setup_session", resource_id=setup_id, data={"expires_at": expires.isoformat()})
        return {"setup_id": setup_id, "version": item["version"], "resume_token": secret, "expires_at": expires.isoformat()}


def redeem(principal: Principal, setup_id: str, secret: str) -> dict[str, Any]:
    _owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = _org(session, principal, lock=True)
        item = _item(org, setup_id, principal, active=True)
        expiry = item.get("link_expires_at")
        if (not item.get("link_hash") or not expiry or datetime.fromisoformat(expiry) <= utcnow()
                or not constant_time_equals(item["link_hash"], token_hash(secret))):
            raise conflict("This setup link is invalid, expired, or already used.")
        item.update(link_hash=None, link_expires_at=None, version=item["version"] + 1)
        records = deepcopy(org.setup_sessions)
        records[setup_id] = item
        org.setup_sessions = records
        audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="setup.resumed", actor=principal,
                     resource_type="setup_session", resource_id=setup_id, data={})
        return _render(session, item, principal)
