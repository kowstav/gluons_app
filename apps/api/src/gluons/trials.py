"""Trial evidence from persisted runs. Completion never invents accuracy or savings."""
from __future__ import annotations

from sqlalchemy import select

from gluons import audit
from gluons.authz import Principal, Role
from gluons.core import conflict, fingerprint, not_found, utcnow
from gluons.db.models import ActionReceipt, Automation, Run, Trial
from gluons.db.session import tenant_tx
from gluons.runtime.state import transition_automation


def _report(session, trial: Trial) -> dict:
    query = select(Run).where(Run.automation_id == trial.automation_id, Run.mode == trial.mode,
                              Run.created_at >= trial.created_at)
    if trial.completed_at:
        query = query.where(Run.created_at <= trial.completed_at)
    runs = session.execute(query).scalars().all()
    by_event = {r.source_event_id: r for r in runs}
    expected = trial.comparator.get("expected_outcomes", {})
    if not isinstance(expected, dict):
        expected = {}
    evaluated = [r for event, r in by_event.items() if event in expected and r.status == "completed"]
    correct = sum(fingerprint(r.outcome) == fingerprint(expected[r.source_event_id]) for r in evaluated)
    failures = sum(r.status in ("failed", "reconciliation_required") for r in runs)
    pending = sum(r.status not in ("completed", "failed", "cancelled") for r in runs)
    minimum = max(1, int(trial.comparator.get("minimum_cases", 10)))
    receipts = session.execute(select(ActionReceipt).where(ActionReceipt.run_id.in_([r.id for r in runs]))).scalars().all() if runs else []
    writes = sum(r.status in ("succeeded", "pending", "uncertain") and r.mode in ("supervised", "live") for r in receipts)
    passed = bool(len(evaluated) >= minimum and correct == len(evaluated) == len(expected) and not failures and not pending and not writes)
    return {"trial_id": trial.id, "automation_id": trial.automation_id, "mode": trial.mode, "status": trial.status,
            "measured": {"runs": len(runs), "completed": sum(r.status == "completed" for r in runs), "failures": failures,
                         "pending": pending, "external_writes": writes, "labelled_cases": len(evaluated), "correct_cases": correct,
                         "accuracy": correct / len(evaluated) if evaluated else None},
            "estimated": {"time_saved_minutes": None, "financial_savings_usd": None}, "passed": passed,
            "quality_basis": "Owner-supplied labelled outcomes, compared with persisted run outcomes; no independent domain certification.",
            "missing_evidence": [] if passed else ["Complete the labelled comparison set with no errors or pending runs; minimum cases: " + str(minimum)],
            "baseline": trial.baseline, "created_at": trial.created_at.isoformat(),
            "completed_at": trial.completed_at.isoformat() if trial.completed_at else None}


def get_report(principal: Principal, trial_id: str) -> dict:
    principal.require("trials:read")
    from gluons.assist import require_active
    require_active(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        trial = session.get(Trial, trial_id)
        if trial is None:
            raise not_found("Trial")
        if principal.role == Role.FIELD_REP and trial.created_by != principal.user_id:
            raise not_found("Trial")
        return trial.results if trial.status == "completed" else _report(session, trial)


def list_all(principal: Principal) -> list[dict]:
    principal.require("trials:read")
    from gluons.assist import require_active
    require_active(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        query = select(Trial).order_by(Trial.created_at.desc()).limit(200)
        if principal.role == Role.FIELD_REP:
            query = query.where(Trial.created_by == principal.user_id)
        return [{"id": t.id, "automation_id": t.automation_id, "mode": t.mode, "status": t.status,
                 "created_at": t.created_at.isoformat()} for t in session.execute(query).scalars()]


def complete(principal: Principal, trial_id: str) -> dict:
    principal.require("trials:write")
    from gluons.assist import require_active
    require_active(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        trial = session.get(Trial, trial_id, with_for_update=True)
        if trial is None:
            raise not_found("Trial")
        automation = session.get(Automation, trial.automation_id, with_for_update=True)
        if principal.role == Role.FIELD_REP and (trial.created_by != principal.user_id or automation.created_by != principal.user_id):
            raise not_found("Trial")
        if trial.status == "completed":
            return trial.results
        if automation.status not in ("trial_shadow", "trial_replay"):
            raise conflict("Only an active trial can be completed.")
        report = _report(session, trial)
        if report["measured"]["pending"]:
            raise conflict("Finish or cancel the pending trial runs first.")
        trial.status = "completed"
        trial.completed_at = utcnow()
        report.update(status="completed", completed_at=trial.completed_at.isoformat())
        trial.results = report
        transition_automation(automation, "awaiting_activation")
        audit.record(session, tenant_id=principal.tenant_id, category="automation", event_type="trial.completed",
                     actor=principal, resource_type="trial", resource_id=trial.id, data={"passed": report["passed"], "runs": report["measured"]["runs"]})
        return report
