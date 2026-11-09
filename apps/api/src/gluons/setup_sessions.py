"""Durable owner setup state; provider authority stays with connection sessions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from gluons import audit
from gluons.authz import Principal, Role
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
from gluons.db.models import ConnectionSession, LaunchPlan, Organization
from gluons.db.session import tenant_tx

MAX_SESSIONS = 50


def _owner(principal: Principal) -> None:
    principal.require("org:manage")
    if principal.kind != "user" or principal.role != Role.OWNER or not principal.user_id or not principal.session_id:
        raise forbidden("An authenticated business owner must manage setup in Gluons Web.")


def _org(session, principal: Principal, *, lock: bool = False) -> Organization:
    org = session.get(Organization, principal.tenant_id, with_for_update=lock)
    if org is None:
        raise not_found("Organization")
    return org


def _item(org: Organization, setup_id: str, principal: Principal) -> dict[str, Any]:
    item = org.setup_sessions.get(setup_id)
    if item is None or item["created_by"] != principal.user_id:
        raise not_found("Setup session")
    return deepcopy(item)


def _view(item: dict[str, Any], connection: ConnectionSession | None = None) -> dict[str, Any]:
    view = {key: item.get(key) for key in ("id", "intent", "launch_plan_id", "status", "version", "created_at", "connection_session_id")}
    if connection is not None:
        status = "expired" if connection.expires_at <= utcnow() and connection.consumed_at is None else connection.status
        view["connection"] = {"id": connection.id, "provider": connection.provider, "status": status,
                              "connection_id": connection.connection_id}
    else:
        view["connection"] = None
    view["notice"] = "Setup state does not authorize provider access, sharing, or business actions."
    return view


def _connection(session, item: dict[str, Any], principal: Principal) -> ConnectionSession | None:
    session_id = item.get("connection_session_id")
    row = session.get(ConnectionSession, session_id) if session_id else None
    if row is not None and row.initiated_by != principal.user_id:
        return None
    return row


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
                return _view(item, _connection(session, item, principal))
        if len(records) >= MAX_SESSIONS:
            raise conflict("This business has reached its setup-session limit.")
        if launch_plan_id and session.get(LaunchPlan, launch_plan_id) is None:
            raise not_found("Business launch plan")
        item = {"id": new_id("set"), "intent": intent, "launch_plan_id": launch_plan_id, "status": "in_progress",
                "version": 1, "created_at": utcnow().isoformat(), "created_by": principal.user_id,
                "connection_session_id": None, "idempotency_hash": key_hash, "request_hash": request_hash,
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
        return _view(item, _connection(session, item, principal))


def attach_connection(principal: Principal, setup_id: str, connection_session_id: str, expected_version: int) -> dict[str, Any]:
    _owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = _org(session, principal, lock=True)
        item = _item(org, setup_id, principal)
        if item["version"] != expected_version:
            raise conflict_version(item["version"])
        connection = session.get(ConnectionSession, connection_session_id)
        if (connection is None or connection.initiated_by != principal.user_id
                or (item["launch_plan_id"] and connection.automation_plan_id != item["launch_plan_id"])):
            raise not_found("Connection session")
        if item["connection_session_id"] not in (None, connection.id):
            raise conflict("This setup already references a different connection session.")
        item["connection_session_id"] = connection.id
        item["version"] += 1
        records = deepcopy(org.setup_sessions)
        records[setup_id] = item
        org.setup_sessions = records
        audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="setup.connection_attached", actor=principal,
                     resource_type="setup_session", resource_id=setup_id, data={"provider": connection.provider})
        return _view(item, connection)


def issue_resume_token(principal: Principal, setup_id: str, expected_version: int) -> dict[str, Any]:
    _owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = _org(session, principal, lock=True)
        item = _item(org, setup_id, principal)
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
        item = _item(org, setup_id, principal)
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
        return _view(item, _connection(session, item, principal))
