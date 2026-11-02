"""Operational counts and costs from persisted evidence, never generated business results."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from gluons.authz import Principal
from gluons.core import not_found, utcnow
from gluons.db.models import ActionReceipt, Approval, Automation, Run, UsageEvent
from gluons.db.session import tenant_tx


def operational(principal: Principal, *, period_days: int = 30) -> dict:
    principal.require("reports:read")
    period_days = max(1, min(period_days, 366))
    since = utcnow() - timedelta(days=period_days)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        statuses = session.execute(select(Run.status, func.count()).where(Run.created_at >= since).group_by(Run.status)).all()
        environments = session.execute(select(ActionReceipt.environment, func.count()).where(
            ActionReceipt.created_at >= since, ActionReceipt.verified.is_(True)).group_by(ActionReceipt.environment)).all()
        cost = session.execute(select(UsageEvent.cost_basis, func.sum(UsageEvent.cost_usd)).where(
            UsageEvent.occurred_at >= since).group_by(UsageEvent.cost_basis)).all()
        return {"period_days": period_days, "period_start": since.isoformat(), "basis": "measured_database_records",
                "run_counts": dict(statuses), "verified_actions_by_environment": dict(environments),
                "costs_usd_by_basis": {basis: str(amount) for basis, amount in cost},
                "pending_approvals": session.execute(select(func.count()).select_from(Approval).where(Approval.status == "pending", Approval.expires_at > utcnow())).scalar_one(),
                "automations": session.execute(select(func.count()).select_from(Automation)).scalar_one()}


def roi(principal: Principal, automation_id: str | None = None) -> dict:
    principal.require("reports:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        if automation_id and session.get(Automation, automation_id) is None:
            raise not_found("Automation")
        query = select(UsageEvent.cost_basis, func.sum(UsageEvent.cost_usd)).group_by(UsageEvent.cost_basis)
        if automation_id:
            query = query.where(UsageEvent.automation_id == automation_id)
        costs = {basis: str(amount) for basis, amount in session.execute(query).all()}
    return {"automation_id": automation_id, "measured": {"usage_costs_usd_by_basis": costs, "time_saved_minutes": None},
            "estimated": {"time_saved_minutes": None, "financial_savings_usd": None, "roi_percent": None},
            "missing_evidence": ["A documented human-workflow time study and verified counterfactual baseline are required before calculating savings."]}
