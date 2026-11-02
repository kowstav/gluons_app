"""Operational counts, ROI and anomaly alerts from persisted evidence, never generated results.

ROI keeps three bases apart: measured (someone recorded it with a source), estimated (derived
from a stated baseline by a stated method) and unverified (an opportunity nobody has checked).
No basis ever feeds another, each states its denominator, and no data gives empty results.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from gluons import audit, events
from gluons.authz import Principal
from gluons.core import new_id, not_found, utcnow
from gluons.db.models import ActionReceipt, Approval, Automation, Incident, Run, Trial, TrialMeasurement, UsageEvent
from gluons.db.session import tenant_tx
from gluons.trials import plain_number

ANOMALY_PREFIX = "anomaly_"
SPEND_SPIKE_RATIO = Decimal(3)
SPEND_SPIKE_FLOOR_USD = Decimal(1)
NO_HISTORY_SPEND_FLOOR_USD = Decimal(25)
TOKEN_LOOP_CALLS_PER_HOUR = 25
FAILED_RUNS_MIN = 5
FAILED_RUNS_SHARE = Decimal("0.5")
TIME_SAVED_METRIC = "time_saved_minutes"


def operational(principal: Principal, *, period_days: int = 30) -> dict[str, Any]:
    principal.require("reports:read")
    period_days = max(1, min(period_days, 366))
    since = utcnow() - timedelta(days=period_days)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        statuses = dict(session.execute(select(Run.status, func.count()).where(Run.created_at >= since).group_by(Run.status)).all())
        environments = dict(session.execute(select(ActionReceipt.environment, func.count()).where(
            ActionReceipt.created_at >= since, ActionReceipt.verified.is_(True)).group_by(ActionReceipt.environment)).all())
        cost = session.execute(select(UsageEvent.cost_basis, func.sum(UsageEvent.cost_usd)).where(
            UsageEvent.occurred_at >= since).group_by(UsageEvent.cost_basis)).all()
        total = sum(statuses.values())
        failed = statuses.get("failed", 0) + statuses.get("reconciliation_required", 0)
        return {"period_days": period_days, "period_start": since.isoformat(), "basis": "measured_database_records",
                "run_counts": statuses, "verified_actions_by_environment": environments,
                "denominators": {"runs": total, "verified_actions": sum(environments.values())},
                "failure_rate": {"failed_runs": failed, "of_runs": total, "rate": round(failed / total, 6) if total else None},
                "costs_usd_by_basis": {basis: str(amount) for basis, amount in cost},
                "pending_approvals": session.execute(select(func.count()).select_from(Approval).where(Approval.status == "pending", Approval.expires_at > utcnow())).scalar_one(),
                "automations": session.execute(select(func.count()).select_from(Automation)).scalar_one()}


def _aggregate(rows: Iterable[TrialMeasurement]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        entry = merged.setdefault((row.metric, row.unit), {"metric": row.metric, "unit": row.unit, "value": Decimal(0), "denominator": 0, "sources": []})
        entry["value"] += row.value
        entry["denominator"] += 1
        if row.source and row.source not in entry["sources"]:
            entry["sources"].append(row.source)
    return [{**entry, "value": plain_number(entry["value"])} for entry in merged.values()]


def _estimate(trials: list[Trial], usage_total: Decimal) -> dict[str, Any]:
    """Sums only what completed trials already labelled as estimates, with their denominators."""
    minutes, savings, denominator, used = Decimal(0), Decimal(0), 0, []
    priced = False
    for trial in trials:
        estimate = (trial.results or {}).get("estimated") or {}
        if estimate.get("time_saved_minutes") is None:
            continue
        minutes += Decimal(estimate["time_saved_minutes"])
        denominator += int(estimate.get("denominator") or 0)
        used.append(trial.id)
        if estimate.get("financial_savings_usd") is not None:
            savings += Decimal(estimate["financial_savings_usd"])
            priced = True
    if not used:
        return {"time_saved_minutes": None, "financial_savings_usd": None, "roi_percent": None, "method": None, "denominator": 0, "trials_used": []}
    roi = (savings - usage_total) / usage_total * 100 if priced and usage_total > 0 else None
    return {"time_saved_minutes": plain_number(minutes), "financial_savings_usd": str(savings) if priced else None,
            "roi_percent": str(roi.quantize(Decimal("0.01"))) if roi is not None else None,
            "method": "Owner-supplied baseline minutes_per_case x correct labelled trial cases, minus recorded manual effort, less recorded usage cost for roi_percent.",
            "denominator": denominator, "denominator_unit": "correct labelled trial cases", "trials_used": used}


def roi(principal: Principal, automation_id: str | None = None) -> dict[str, Any]:
    principal.require("reports:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        if automation_id and session.get(Automation, automation_id) is None:
            raise not_found("Automation")
        usage = select(UsageEvent.cost_basis, func.sum(UsageEvent.cost_usd)).group_by(UsageEvent.cost_basis)
        trial_query = select(Trial).where(Trial.status == "completed")
        if automation_id:
            usage = usage.where(UsageEvent.automation_id == automation_id)
            trial_query = trial_query.where(Trial.automation_id == automation_id)
        rows = session.execute(usage).all()
        trials = list(session.execute(trial_query).scalars())
        measurements = session.execute(select(TrialMeasurement).where(TrialMeasurement.trial_id.in_([t.id for t in trials]))).scalars().all() if trials else []
    costs = {basis: str(amount) for basis, amount in rows}
    usage_total = sum((Decimal(amount) for _, amount in rows), Decimal(0))
    measured = [m for m in measurements if m.basis == "measured"]
    saved = [m for m in measured if m.metric == TIME_SAVED_METRIC]
    estimated = _estimate(trials, usage_total)
    missing = []
    if not saved:
        missing.append("A documented human-workflow time study is required before reporting measured time saved.")
    if estimated["time_saved_minutes"] is None:
        missing.append("A completed trial with an owner baseline and correct labelled cases is required before estimating savings.")
    return {"automation_id": automation_id,
            "measured": {"usage_costs_usd_by_basis": costs, "time_saved_minutes": plain_number(sum((m.value for m in saved), Decimal(0))) if saved else None,
                         "metrics": _aggregate(measured), "denominator": len(measured), "denominator_unit": "recorded measurements",
                         "method": "Values a person recorded with a named source on a completed trial." if measured else None},
            "estimated": estimated,
            "unverified": {"opportunities": _aggregate(m for m in measurements if m.basis == "unverified"),
                           "note": "Nobody has checked these. They count toward nothing above."},
            "recorded_estimates": _aggregate(m for m in measurements if m.basis == "estimated"),
            "missing_evidence": missing}


def _open(session: Any, tenant_id: str, *, kind: str, key: str, severity: str, summary: str, facts: dict[str, Any],
          automation_id: str | None = None, run_id: str | None = None) -> dict[str, Any] | None:
    """One open incident per condition per day, so a repeated scan cannot page anyone twice."""
    seen = session.execute(select(Incident.id).where(Incident.kind == kind, Incident.status == "open",
                                                     Incident.diagnosis["key"].as_string() == key)).first()
    if seen is not None:
        return None
    incident = Incident(id=new_id("inc"), tenant_id=tenant_id, kind=kind, severity=severity, run_id=run_id, automation_id=automation_id,
                        summary=summary, diagnosis={**facts, "key": key, "basis": "measured_database_records"})
    session.add(incident)
    session.flush()
    events.notify(session, tenant_id, kind=kind, severity="action", title=summary, link=f"/incidents/{incident.id}",
                  resource_type="incident", resource_id=incident.id)
    audit.record(session, tenant_id=tenant_id, category="run", event_type="anomaly.detected", actor="anomaly_scan",
                 resource_type="incident", resource_id=incident.id, data={"kind": kind})
    return {"id": incident.id, "kind": kind, "severity": severity, "summary": summary, **facts}


def _spend_spike(session: Any, tenant_id: str, now: datetime) -> dict[str, Any] | None:
    day_ago, week_ago = now - timedelta(days=1), now - timedelta(days=8)
    recent = Decimal(session.execute(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(UsageEvent.occurred_at > day_ago, UsageEvent.occurred_at <= now)).scalar_one())
    prior = Decimal(session.execute(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(UsageEvent.occurred_at > week_ago, UsageEvent.occurred_at <= day_ago)).scalar_one())
    daily = prior / 7
    spike = recent >= NO_HISTORY_SPEND_FLOOR_USD if daily == 0 else recent >= SPEND_SPIKE_FLOOR_USD and recent >= daily * SPEND_SPIKE_RATIO
    if not spike:
        return None
    return _open(session, tenant_id, kind=f"{ANOMALY_PREFIX}spend_spike", key=f"spend:{now.date()}", severity="warning",
                 summary=f"Spend reached {recent:.2f} USD in 24 hours against a {daily:.2f} USD daily average.",
                 facts={"last_24h_usd": str(recent), "prior_7d_daily_usd": str(daily), "ratio_threshold": str(SPEND_SPIKE_RATIO)})


def _token_loops(session: Any, tenant_id: str, now: datetime) -> list[dict[str, Any]]:
    rows = session.execute(select(UsageEvent.run_id, UsageEvent.automation_id, func.count(), func.sum(UsageEvent.quantity))
                           .where(UsageEvent.category == "model", UsageEvent.run_id.is_not(None),
                                  UsageEvent.occurred_at > now - timedelta(hours=1), UsageEvent.occurred_at <= now)
                           .group_by(UsageEvent.run_id, UsageEvent.automation_id).having(func.count() >= TOKEN_LOOP_CALLS_PER_HOUR)).all()
    found = [_open(session, tenant_id, kind=f"{ANOMALY_PREFIX}token_loop", key=f"loop:{run_id}", severity="critical", run_id=run_id, automation_id=automation_id,
                   summary=f"One run made {calls} model calls within an hour.",
                   facts={"model_calls_last_hour": calls, "tokens": str(tokens), "call_threshold": TOKEN_LOOP_CALLS_PER_HOUR})
             for run_id, automation_id, calls, tokens in rows]
    return [item for item in found if item]


def _failed_events(session: Any, tenant_id: str, now: datetime) -> dict[str, Any] | None:
    since = now - timedelta(days=1)
    total = session.execute(select(func.count()).select_from(Run).where(Run.created_at > since, Run.created_at <= now)).scalar_one()
    failed = session.execute(select(func.count()).select_from(Run).where(Run.created_at > since, Run.created_at <= now, Run.status == "failed")).scalar_one()
    if failed < FAILED_RUNS_MIN or Decimal(failed) < Decimal(total) * FAILED_RUNS_SHARE:
        return None
    return _open(session, tenant_id, kind=f"{ANOMALY_PREFIX}failed_events", key=f"failed:{now.date()}", severity="critical",
                 summary=f"{failed} of {total} runs failed in the last 24 hours.",
                 facts={"failed_runs": failed, "of_runs": total, "share_threshold": str(FAILED_RUNS_SHARE)})


def detect_anomalies(tenant_id: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Deterministic thresholds over recorded usage. Callers derive tenant_id from a verified source."""
    current = now or utcnow()
    with tenant_tx(tenant_id) as session:
        found = [_spend_spike(session, tenant_id, current), *_token_loops(session, tenant_id, current), _failed_events(session, tenant_id, current)]
    return [item for item in found if item]


def scan_anomalies(principal: Principal) -> dict[str, Any]:
    principal.require("reports:read")
    return {"opened": detect_anomalies(principal.tenant_id), "open": list_anomalies(principal)}


def list_anomalies(principal: Principal) -> list[dict[str, Any]]:
    principal.require("reports:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        rows = session.execute(select(Incident).where(Incident.kind.startswith(ANOMALY_PREFIX), Incident.status == "open")
                               .order_by(Incident.created_at.desc()).limit(100)).scalars()
        return [{"id": r.id, "kind": r.kind, "severity": r.severity, "summary": r.summary, "run_id": r.run_id, "automation_id": r.automation_id,
                 "facts": r.diagnosis, "created_at": r.created_at.isoformat()} for r in rows]
