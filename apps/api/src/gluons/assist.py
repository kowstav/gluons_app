"""Field representative mode: a consented, time-boxed, audited setup visit (docs 01, 03, 08).

A representative only reaches a business through a session its owner consented to, and only
for as long as that session lasts. They never see credentials, business facts or customer
data, and the one thing they can start is a shadow trial, which cannot touch an external
system. The completion report separates what Gluons recorded from what the rep wrote down.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gluons import audit, events
from gluons.authz import Principal, Role
from gluons.core import GluonsError, conflict, forbidden, new_id, not_found, utcnow, validation_failed
from gluons.db.models import AssistSession, Connection, Organization
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle
from gluons.skills.registry import get_registry

REQUEST_TTL = timedelta(minutes=30)
SESSION_TTL = timedelta(minutes=60)
MAX_TRIALS_PER_SESSION = 5
DATA_BASES = ("synthetic", "owner_consented_sample")
REPORT_FIELDS = ("notes", "demonstrated", "open_questions", "owner_next_steps")
RESTRICTIONS = (
    "No access to credentials, tokens or provider accounts.",
    "Cannot sign policies, activate automations, change budgets or spend funds.",
    "Can start shadow trials only, which make zero external writes.",
)
_CREDENTIAL_LIKE = re.compile(r"\b(password|passcode|api[ _-]?key|secret|bearer|sk-[a-z0-9]{8,}|otp)\b", re.I)
_OPEN = ("requested", "active")


def _view(row: AssistSession) -> dict[str, Any]:
    return {"id": row.id, "status": row.status, "rep_user_id": row.rep_user_id,
            "owner_consent_by": row.owner_consent_by,
            "owner_consent_at": row.owner_consent_at.isoformat() if row.owner_consent_at else None,
            "consent": row.checklist.get("consent", {}), "trials": row.checklist.get("trials", []),
            "expires_at": row.expires_at.isoformat(), "created_at": row.created_at.isoformat(),
            "ended_at": row.ended_at.isoformat() if row.ended_at else None,
            "completion_report": row.completion_report, "restrictions": list(RESTRICTIONS)}


def _load(session: Session, principal: Principal, session_id: str, *, lock: bool = False) -> AssistSession:
    row = session.get(AssistSession, session_id, with_for_update=lock)
    # A rep cannot learn about another rep's visit, even that it exists.
    if row is None or not (principal.can("assist:consent") or row.rep_user_id == principal.user_id):
        raise not_found("Assist session")
    return row


def _record(session: Session, principal: Principal, row: AssistSession, event: str, **data: Any) -> None:
    audit.record(session, tenant_id=row.tenant_id, category="permission", event_type=f"assist.{event}", actor=principal,
                 resource_type="assist_session", resource_id=row.id, data=data)


def _while_open[Result](principal: Principal, session_id: str, work: Callable[[Session, AssistSession], Result]) -> Result:
    """Expiry must commit before the caller is told, so it cannot raise inside the transaction."""
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = _load(session, principal, session_id, lock=True)
        if row.status not in _OPEN:
            raise conflict(f"This assist session is {row.status}.")
        if row.expires_at > utcnow():
            return work(session, row)
        row.status = "expired"
        row.ended_at = utcnow()
        _record(session, principal, row, "expired")
    raise GluonsError("ASSIST_SESSION_EXPIRED", "This assist session has ended. The owner can consent to a new one.", status=409)


def request_session(principal: Principal) -> dict[str, Any]:
    principal.require("assist:use")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        existing = session.execute(select(AssistSession).where(
            AssistSession.rep_user_id == principal.user_id, AssistSession.status.in_(_OPEN),
            AssistSession.expires_at > utcnow())).scalars().first()
        if existing is not None:
            return _view(existing)
        row = AssistSession(id=new_id("ast"), tenant_id=principal.tenant_id, rep_user_id=principal.user_id,
                            expires_at=utcnow() + REQUEST_TTL, checklist={"consent": {}, "trials": []})
        session.add(row)
        session.flush()
        _record(session, principal, row, "requested")
        events.notify(session, principal.tenant_id, kind="assist_requested", severity="action",
                      title="A field representative asked to help with setup",
                      body="Nothing starts until you consent. The session ends automatically.",
                      link=f"/assist/{row.id}", resource_type="assist_session", resource_id=row.id)
        return _view(row)


def consent(principal: Principal, session_id: str, *, allow_shadow_trial: bool = False, allow_sample_data: bool = False) -> dict[str, Any]:
    """Owner only. Trial setup and real sample data are separate, explicit permissions."""
    principal.require("assist:consent")
    if principal.kind != "user":
        raise forbidden("The owner consents in Gluons Web.")

    def grant(session: Session, row: AssistSession) -> dict[str, Any]:
        if row.status != "requested":
            raise conflict("This assist session has already been decided.")
        if row.rep_user_id == principal.user_id:
            raise forbidden("A representative cannot consent to their own session.")
        row.status = "active"
        row.owner_consent_by = principal.actor_id
        row.owner_consent_at = utcnow()
        row.expires_at = utcnow() + SESSION_TTL
        row.checklist = {**row.checklist, "consent": {"shadow_trial": allow_shadow_trial, "sample_data": allow_sample_data}}
        _record(session, principal, row, "consented", shadow_trial=allow_shadow_trial, sample_data=allow_sample_data)
        return _view(row)

    return _while_open(principal, session_id, grant)


def revoke(principal: Principal, session_id: str) -> dict[str, Any]:
    """The owner can end a visit at any moment. Trials already started keep running in shadow."""
    principal.require("assist:consent")

    def end(session: Session, row: AssistSession) -> dict[str, Any]:
        row.status = "revoked"
        row.ended_at = utcnow()
        _record(session, principal, row, "revoked")
        return _view(row)

    return _while_open(principal, session_id, end)


def list_sessions(principal: Principal) -> list[dict[str, Any]]:
    if not (principal.can("assist:consent") or principal.can("assist:use")):
        principal.require("assist:use")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        query = select(AssistSession).order_by(AssistSession.created_at.desc()).limit(100)
        if not principal.can("assist:consent"):
            query = query.where(AssistSession.rep_user_id == principal.user_id)
        return [_view(row) for row in session.execute(query).scalars()]


def get_session(principal: Principal, session_id: str) -> dict[str, Any]:
    if not principal.can("assist:consent"):
        principal.require("assist:use")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return _view(_load(session, principal, session_id))


def _require_consented(row: AssistSession) -> None:
    if row.status != "active" or row.owner_consent_at is None:
        raise GluonsError("ASSIST_CONSENT_REQUIRED", "The owner must consent to this session first.", status=403)


def workspace(principal: Principal, session_id: str) -> dict[str, Any]:
    """Only readiness facts: no business facts, account identifiers or credential references."""
    principal.require("assist:use")

    def build(session: Session, row: AssistSession) -> dict[str, Any]:
        _require_consented(row)
        org = session.get(Organization, principal.tenant_id)
        connections = session.execute(select(Connection.provider, Connection.status, Connection.environment)
                                      .where(Connection.disconnected_at.is_(None))).all()
        return {"session": _view(row), "business": {"name": org.name, "industry": org.industry, "country": org.country},
                "connections": [{"provider": c.provider, "status": c.status, "environment": c.environment} for c in connections],
                "secrets_visible": False}

    return _while_open(principal, session_id, build)


def setup_shadow_trial(principal: Principal, session_id: str, skill_id: str, *, data_basis: str = "synthetic") -> dict[str, Any]:
    """The rep's only write. Runs with operator rights for these two calls and nothing else."""
    principal.require("assist:use")
    if data_basis not in DATA_BASES:
        raise validation_failed("Use synthetic data or a sample the owner consented to.")
    skill = get_registry().loaded(skill_id)

    def check(session: Session, row: AssistSession) -> None:
        _require_consented(row)
        granted = row.checklist.get("consent", {})
        if not granted.get("shadow_trial"):
            raise GluonsError("ASSIST_CONSENT_REQUIRED", "The owner has not consented to a shadow trial.", status=403)
        if data_basis == "owner_consented_sample" and (not granted.get("sample_data") or skill.contract.regulated):
            raise GluonsError("ASSIST_CONSENT_REQUIRED", "This sample needs explicit owner consent and cannot be regulated data.", status=403)
        if len(row.checklist.get("trials", [])) >= MAX_TRIALS_PER_SESSION:
            raise conflict("This session has reached its trial limit.")

    _while_open(principal, session_id, check)
    delegated = replace(principal, role=Role.OPERATOR)
    automation = lifecycle.create(delegated, skill_id=skill.contract.slug, name=f"{skill.contract.title} (assisted setup)",
                                  config={"assist_session_id": session_id, "data_basis": data_basis})
    trial = lifecycle.start_trial(delegated, automation["id"], "shadow", automation["version"])
    entry = {"automation_id": automation["id"], "trial_id": trial["trial_id"], "skill_id": skill.contract.slug,
             "data_basis": data_basis, "mode": "shadow", "started_at": utcnow().isoformat()}

    def remember(session: Session, row: AssistSession) -> dict[str, Any]:
        row.checklist = {**row.checklist, "trials": [*row.checklist.get("trials", []), entry]}
        _record(session, principal, row, "shadow_trial_started", trial_id=entry["trial_id"], skill_id=entry["skill_id"], data_basis=data_basis)
        return {**entry, "external_writes_possible": False,
                "next_step": "The owner adds pass criteria and reviews the trial. It cannot pass without them."}

    return _while_open(principal, session_id, remember)


def _clean_report(report: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(report) - set(REPORT_FIELDS))
    if unknown:
        raise validation_failed("Report fields are limited.", allowed=list(REPORT_FIELDS), unknown=unknown)
    if _CREDENTIAL_LIKE.search(str(report)):
        raise validation_failed("Do not put credentials in a report. Owners enter those themselves.")
    return audit.redact(report)


def end_session(principal: Principal, session_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """Facts come from the session record. The rep's words stay labelled as theirs."""
    if not principal.can("assist:consent"):
        principal.require("assist:use")
    notes = _clean_report(report)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = _load(session, principal, session_id, lock=True)
        if row.status not in _OPEN:
            raise conflict(f"This assist session is {row.status}.")
        now = utcnow()
        row.status = "completed" if row.status == "active" and row.expires_at > now else "expired"
        row.ended_at = now
        row.completion_report = {
            "recorded": {"owner_consent_at": row.owner_consent_at.isoformat() if row.owner_consent_at else None,
                         "started_at": row.created_at.isoformat(), "ended_at": now.isoformat(),
                         "trials_started": row.checklist.get("trials", []), "external_writes_by_rep": 0},
            "rep_supplied_unverified": notes,
        }
        _record(session, principal, row, "ended", status=row.status, trials=len(row.checklist.get("trials", [])))
        events.notify(session, principal.tenant_id, kind="assist_completed", title="Assisted setup finished",
                      body="Review the completion report and any shadow trials it started.",
                      link=f"/assist/{row.id}", resource_type="assist_session", resource_id=row.id)
        return _view(row)
