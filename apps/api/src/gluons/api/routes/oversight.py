"""Approvals, audit, incidents, notifications and spending."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from gluons import approvals, audit, billing, tasks
from gluons.api.deps import PrincipalDep
from gluons.core import not_found, utcnow
from gluons.db.models import Approval, ApprovalDecision, AuditEvent, Incident, Notification
from gluons.db.session import tenant_tx

router = APIRouter(prefix="/v1", tags=["oversight"])


class Decide(BaseModel):
    decision: Literal["approve", "deny"]
    reason: str | None = Field(default=None, max_length=2000)
    resolution: dict[str, Any] | None = None
    expected_version: int | None = None


class ResolveIncident(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@router.get("/approvals")
def list_approvals(principal: PrincipalDep, status: str = "pending") -> dict[str, Any]:
    principal.require("approvals:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        rows = session.execute(
            select(Approval).where(Approval.status == status).order_by(Approval.created_at.desc()).limit(200)
        ).scalars()
        return {"approvals": [approvals.serialize(a) for a in rows]}


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("approvals:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        approval = session.get(Approval, approval_id)
        if approval is None:
            raise not_found("Approval")
        decisions = session.execute(
            select(ApprovalDecision).where(ApprovalDecision.approval_id == approval_id)
        ).scalars().all()
        return approvals.serialize(approval, include_decisions=list(decisions))


@router.post("/approvals/{approval_id}/decide")
@router.post("/approvals/{approval_id}/decision")
def decide(approval_id: str, body: Decide, principal: PrincipalDep) -> dict[str, Any]:
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        approval = approvals.decide(
            session,
            principal,
            approval_id,
            body.decision,
            reason=body.reason,
            resolution=body.resolution,
            expected_version=body.expected_version,
        )
        return approvals.serialize(approval)


def _audit_rows(principal: PrincipalDep, category: str | None, limit: int) -> list[dict[str, Any]]:
    principal.require("audit:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        query = select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(limit)
        if category:
            query = query.where(AuditEvent.category == category)
        return [
            {
                "seq": e.seq,
                "category": e.category,
                "event_type": e.event_type,
                "actor_kind": e.actor_kind,
                "actor_id": e.actor_id,
                "binding_id": e.binding_id,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "decision": e.decision,
                "data": e.data,
                "hash": e.hash,
                "created_at": e.created_at.isoformat(),
            }
            for e in session.execute(query).scalars()
        ]


@router.get("/audit/events")
def audit_events(principal: PrincipalDep, category: str | None = None, limit: int = 200) -> dict[str, Any]:
    return {"events": _audit_rows(principal, category, min(limit, 1000))}


@router.get("/audit/permission-events")
def permission_events(principal: PrincipalDep, limit: int = 200) -> dict[str, Any]:
    return {"events": _audit_rows(principal, "permission", min(limit, 1000))}


@router.get("/audit/verify")
def audit_verify(principal: PrincipalDep) -> dict[str, Any]:
    principal.require("audit:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return audit.verify_chain(session, principal.tenant_id)


@router.post("/audit/export")
def audit_export(principal: PrincipalDep) -> dict[str, Any]:
    principal.require("audit:export")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="data",
            event_type="audit.exported",
            actor=principal,
            resource_type="organization",
            resource_id=principal.tenant_id,
        )
        chain = audit.verify_chain(session, principal.tenant_id)
    return {"chain": chain, "events": _audit_rows(principal, None, 1000), "exported_at": utcnow().isoformat()}


@router.get("/incidents")
def incidents(principal: PrincipalDep, status: str | None = "open") -> dict[str, Any]:
    return {"incidents": tasks.list_incidents(principal, status=status)}


@router.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str, body: ResolveIncident, principal: PrincipalDep) -> dict[str, str]:
    principal.require("tasks:control")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        incident = session.get(Incident, incident_id, with_for_update=True)
        if incident is None:
            raise not_found("Incident")
        incident.status = "resolved"
        incident.resolved_by = principal.actor_id
        incident.resolved_at = utcnow()
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="run",
            event_type="incident.resolved",
            actor=principal,
            resource_type="incident",
            resource_id=incident_id,
            data={"note": body.note},
        )
    return {"status": "resolved"}


@router.get("/notifications")
def notifications(principal: PrincipalDep, unread_only: bool = False) -> dict[str, Any]:
    return {"notifications": tasks.list_notifications(principal, unread_only=unread_only)}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: str, principal: PrincipalDep) -> dict[str, str]:
    principal.require("org:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(Notification, notification_id)
        if row is None:
            raise not_found("Notification")
        row.read_at = utcnow()
    return {"status": "read"}


@router.get("/billing/usage")
def billing_usage(principal: PrincipalDep) -> dict[str, Any]:
    principal.require("billing:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return billing.spending_summary(session, principal.tenant_id)
