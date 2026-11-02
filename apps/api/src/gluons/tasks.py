"""Read models for tasks, runs and evidence. REST and MCP share these."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from gluons.authz import Principal
from gluons.core import not_found
from gluons.db.models import (
    ActionReceipt,
    Approval,
    Automation,
    Incident,
    Notification,
    Run,
    RunEvent,
    StepRun,
)
from gluons.db.session import tenant_tx


def serialize_run(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "automation_id": run.automation_id,
        "skill_id": run.skill_id,
        "mode": run.mode,
        "status": run.status,
        "source": run.source,
        "source_event_id": run.source_event_id,
        "cursor": run.cursor,
        "outcome": run.outcome,
        "error": run.error,
        "cost_usd": str(run.cost_usd),
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "version": run.version,
    }


def serialize_receipt(r: ActionReceipt) -> dict[str, Any]:
    return {
        "id": r.id,
        "action": r.action,
        "mode": r.mode,
        "environment": r.environment,
        "provider": r.provider,
        "status": r.status,
        "verified": r.verified,
        "verified_at": r.verified_at.isoformat() if r.verified_at else None,
        "provider_object_id": r.provider_object_id,
        "provider_request_id": r.provider_request_id,
        "readback": r.readback,
        "warnings": r.warnings,
        "irreversible": r.irreversible,
        "amount_minor": r.amount_minor,
        "approval_id": r.approval_id,
        "cost_usd": str(r.cost_usd) if r.cost_usd is not None else None,
        "latency_ms": r.latency_ms,
        "attempts": r.attempts,
        "created_at": r.created_at.isoformat(),
    }


def list_runs(principal: Principal, automation_id: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    principal.require("tasks:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        query = select(Run).order_by(Run.created_at.desc()).limit(limit)
        if automation_id:
            query = query.where(Run.automation_id == automation_id)
        return [serialize_run(r) for r in session.execute(query).scalars()]


def get_run(principal: Principal, run_id: str) -> dict[str, Any]:
    principal.require("tasks:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            raise not_found("Run")
        steps = session.execute(select(StepRun).where(StepRun.run_id == run_id).order_by(StepRun.started_at)).scalars()
        events = session.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)).scalars()
        receipts = session.execute(select(ActionReceipt).where(ActionReceipt.run_id == run_id)).scalars()
        return {
            **serialize_run(run),
            "steps": [
                {
                    "node_id": s.node_id,
                    "primitive": s.primitive,
                    "status": s.status,
                    "attempts": s.attempts,
                    "error": s.error,
                    "output": s.output,
                    "model_identifier": s.model_identifier,
                }
                for s in steps
            ],
            "events": [
                {"seq": e.seq, "type": e.event_type, "engine": e.engine, "data": e.data, "at": e.created_at.isoformat()}
                for e in events
            ],
            "receipts": [serialize_receipt(r) for r in receipts],
        }


def task_status(principal: Principal, automation_id: str) -> dict[str, Any]:
    """One view of a task, identical from the web app and from any assistant."""
    principal.require("tasks:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = session.get(Automation, automation_id)
        if automation is None:
            raise not_found("Task")
        runs = session.execute(select(Run).where(Run.automation_id == automation_id)).scalars().all()
        counts: dict[str, int] = {}
        for run in runs:
            counts[run.status] = counts.get(run.status, 0) + 1
        pending = session.execute(
            select(Approval).where(Approval.automation_id == automation_id, Approval.status == "pending")
        ).scalars().all()
        from gluons.runtime.lifecycle import serialize

        return {
            **serialize(automation),
            "run_counts": counts,
            "pending_approvals": [{"id": a.id, "title": a.title, "risk_tier": a.risk_tier} for a in pending],
        }


def evidence(principal: Principal, automation_id: str) -> dict[str, Any]:
    """Every receipt with its environment label. Nothing here is summarised by a model."""
    principal.require("tasks:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = session.get(Automation, automation_id)
        if automation is None:
            raise not_found("Task")
        receipts = session.execute(
            select(ActionReceipt)
            .where(ActionReceipt.automation_id == automation_id)
            .order_by(ActionReceipt.created_at.desc())
        ).scalars().all()
        verified = [r for r in receipts if r.verified]
        return {
            "task_id": automation_id,
            "receipts": [serialize_receipt(r) for r in receipts],
            "verified_count": len(verified),
            "previews": sum(1 for r in receipts if r.status == "preview"),
            "environments": sorted({r.environment for r in verified}),
        }


def list_incidents(principal: Principal, *, status: str | None = "open") -> list[dict[str, Any]]:
    principal.require("tasks:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        query = select(Incident).order_by(Incident.created_at.desc()).limit(100)
        if status:
            query = query.where(Incident.status == status)
        return [
            {
                "id": i.id,
                "kind": i.kind,
                "severity": i.severity,
                "run_id": i.run_id,
                "automation_id": i.automation_id,
                "summary": i.summary,
                "diagnosis": i.diagnosis,
                "status": i.status,
                "created_at": i.created_at.isoformat(),
            }
            for i in session.execute(query).scalars()
        ]


def list_notifications(principal: Principal, *, unread_only: bool = False) -> list[dict[str, Any]]:
    principal.require("org:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        query = select(Notification).order_by(Notification.created_at.desc()).limit(100)
        if unread_only:
            query = query.where(Notification.read_at.is_(None))
        return [
            {
                "id": n.id,
                "kind": n.kind,
                "severity": n.severity,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "read": n.read_at is not None,
                "created_at": n.created_at.isoformat(),
            }
            for n in session.execute(query).scalars()
        ]
