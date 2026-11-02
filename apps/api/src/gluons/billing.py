"""Usage ledger and budget enforcement.

Holds reserve an estimate under a row lock before a metered call, so concurrent runs
cannot collectively overshoot a limit. Usage events are append-only and deduplicated,
so a replayed event never charges twice. `cost_basis` separates vendor-reported cost
from Gluons' own estimate.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import Automation, BudgetHold, Organization, UsageEvent

HOLD_TTL = timedelta(minutes=15)


def month_start(now: datetime | None = None) -> datetime:
    current = now or utcnow()
    return datetime(current.year, current.month, 1, tzinfo=UTC)


def record_usage(
    session: Session,
    *,
    tenant_id: str,
    dedupe_key: str,
    category: str,
    provider: str,
    meter: str,
    quantity: Decimal | float | int,
    cost_usd: Decimal | float,
    cost_basis: str = "estimated",
    automation_id: str | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
    skill_id: str | None = None,
    unit_price_usd: Decimal | float | None = None,
    price_version: str | None = None,
    latency_ms: int | None = None,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Returns False when this event was already recorded."""
    result = session.execute(
        insert(UsageEvent)
        .values(
            id=new_id("use"),
            tenant_id=tenant_id,
            dedupe_key=dedupe_key,
            automation_id=automation_id,
            run_id=run_id,
            step_id=step_id,
            skill_id=skill_id,
            category=category,
            provider=provider,
            meter=meter,
            quantity=quantity,
            unit_price_usd=unit_price_usd,
            cost_usd=cost_usd,
            cost_basis=cost_basis,
            price_version=price_version,
            latency_ms=latency_ms,
            detail=detail or {},
        )
        .on_conflict_do_nothing(constraint="uq_usage_dedupe")
        .returning(UsageEvent.id)
    ).first()
    return result is not None


def _spent(session: Session, tenant_id: str, automation_id: str | None) -> Decimal:
    query = select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
        UsageEvent.tenant_id == tenant_id, UsageEvent.occurred_at >= month_start()
    )
    if automation_id:
        query = query.where(UsageEvent.automation_id == automation_id)
    return Decimal(session.execute(query).scalar_one())


def _held(session: Session, tenant_id: str, automation_id: str | None) -> Decimal:
    query = select(func.coalesce(func.sum(BudgetHold.amount_usd), 0)).where(
        BudgetHold.tenant_id == tenant_id, BudgetHold.expires_at > utcnow()
    )
    if automation_id:
        query = query.where(BudgetHold.automation_id == automation_id)
    return Decimal(session.execute(query).scalar_one())


def place_hold(
    session: Session, tenant_id: str, automation_id: str | None, amount_usd: Decimal | float, run_id: str | None = None
) -> str:
    """Raises BUDGET_EXCEEDED rather than letting a metered call start past a limit."""
    amount = Decimal(str(amount_usd))
    # Row locks serialise concurrent holds on the same budget.
    org = session.execute(select(Organization).where(Organization.id == tenant_id).with_for_update()).scalar_one()
    if org.monthly_budget_usd is not None:
        committed = _spent(session, tenant_id, None) + _held(session, tenant_id, None)
        if committed + amount > org.monthly_budget_usd:
            raise GluonsError(
                "BUDGET_EXCEEDED",
                "This business has reached its monthly budget.",
                status=402,
                details={"limit_usd": str(org.monthly_budget_usd), "committed_usd": str(committed)},
            )
    if automation_id:
        automation = session.execute(
            select(Automation).where(Automation.id == automation_id).with_for_update()
        ).scalar_one()
        if automation.monthly_budget_usd is not None:
            committed = _spent(session, tenant_id, automation_id) + _held(session, tenant_id, automation_id)
            if committed + amount > automation.monthly_budget_usd:
                raise GluonsError(
                    "BUDGET_EXCEEDED",
                    "This automation has reached its monthly budget.",
                    status=402,
                    details={"limit_usd": str(automation.monthly_budget_usd), "committed_usd": str(committed)},
                )
    hold = BudgetHold(
        id=new_id("hld"),
        tenant_id=tenant_id,
        automation_id=automation_id,
        run_id=run_id,
        amount_usd=amount,
        expires_at=utcnow() + HOLD_TTL,
    )
    session.add(hold)
    session.flush()
    return hold.id


def release_hold(session: Session, hold_id: str) -> None:
    hold = session.get(BudgetHold, hold_id)
    if hold is not None:
        session.delete(hold)


def spending_summary(session: Session, tenant_id: str) -> dict[str, Any]:
    since = month_start()
    rows = session.execute(
        select(
            UsageEvent.category,
            UsageEvent.provider,
            UsageEvent.cost_basis,
            func.sum(UsageEvent.cost_usd),
            func.count(),
        )
        .where(UsageEvent.tenant_id == tenant_id, UsageEvent.occurred_at >= since)
        .group_by(UsageEvent.category, UsageEvent.provider, UsageEvent.cost_basis)
    ).all()
    org = session.get(Organization, tenant_id)
    total = sum((Decimal(r[3]) for r in rows), Decimal(0))
    return {
        "period_start": since.isoformat(),
        "total_usd": str(total),
        "monthly_budget_usd": str(org.monthly_budget_usd) if org and org.monthly_budget_usd is not None else None,
        "breakdown": [
            {"category": r[0], "provider": r[1], "cost_basis": r[2], "cost_usd": str(r[3]), "events": r[4]} for r in rows
        ],
    }
