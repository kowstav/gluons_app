"""Trial evidence from persisted runs. Completion never invents accuracy or savings.

A trial passes only against criteria stored on it before it ran. Every rate carries its
denominator, and zero runs, missing labels or a missing denominator can never pass.
Observed results stay apart from estimates, and a recommendation authorizes nothing.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select

from gluons import audit, events
from gluons.authz import Principal
from gluons.core import conflict, new_id, not_found, utcnow, validation_failed
from gluons.db.models import ActionReceipt, Approval, Automation, Run, Trial, TrialMeasurement, UsageEvent
from gluons.db.session import tenant_tx
from gluons.policy import NON_LIVE_MODES
from gluons.runtime.state import check_version, transition_automation
from gluons.skills.registry import get_registry

BASES = ("measured", "estimated", "unverified")
ESTIMATE_COST_BASES = frozenset({"estimated", "token_rate_estimate"})
CORRECTION_METRIC = "correction"
MANUAL_EFFORT_METRIC = "manual_effort_minutes"
MAX_LISTED = 50
MAX_TRIAL_DAYS = 90


def validate_criteria(raw: Any) -> dict[str, Any]:
    """Refuses vague targets: a pass has to mean something specific and checkable."""
    if not isinstance(raw, dict):
        raise validation_failed("pass_criteria must state min_cases and min_accuracy.")
    cases, accuracy = raw.get("min_cases"), raw.get("min_accuracy")
    if type(cases) is not int or cases < 1:
        raise validation_failed("min_cases must be a whole number of at least 1.")
    if isinstance(accuracy, bool) or not isinstance(accuracy, (int, float)) or not 0 < accuracy <= 1:
        raise validation_failed("min_accuracy must be above 0 and at most 1.")
    criteria: dict[str, Any] = {"min_cases": cases, "min_accuracy": float(accuracy)}
    for key, default in (("max_failed_runs", 0), ("max_corrections", None)):
        value = raw.get(key, default)
        if value is not None and (type(value) is not int or value < 0):
            raise validation_failed(f"{key} must be a whole number of at least 0.")
        criteria[key] = value
    return criteria


def validate_baseline(raw: Any) -> dict[str, Any]:
    """A human baseline is owner-supplied: useful as a comparator, never proof by itself."""
    if not isinstance(raw, dict) or not str(raw.get("method") or "").strip():
        raise validation_failed("A baseline needs the method used to measure it.")
    cases = raw.get("cases")
    if type(cases) is not int or cases < 1:
        raise validation_failed("A baseline needs the number of cases it was measured on.")
    for key in ("minutes_per_case", "error_rate", "cost_usd_per_case", "hourly_cost_usd"):
        value = raw.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            raise validation_failed(f"{key} must be a non-negative number.")
    if (raw.get("error_rate") or 0) > 1:
        raise validation_failed("error_rate is a share between 0 and 1.")
    return {**raw, "source": raw.get("source") or "owner_supplied"}


def _stored_criteria(trial: Trial) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return validate_criteria(trial.comparator.get("pass_criteria")), None
    except Exception as exc:  # noqa: BLE001 - any malformed stored value simply means no criteria
        return None, getattr(exc, "message", str(exc))


def _matches(outcome: Any, expected: Any) -> bool:
    """Labels name the fields that matter. An empty label can never count as a correct case."""
    if isinstance(expected, dict):
        return bool(expected) and isinstance(outcome, dict) and all(
            key in outcome and (_matches(outcome[key], value) if isinstance(value, dict) and value else outcome[key] == value)
            for key, value in expected.items())
    return expected is not None and outcome == expected


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _expired(trial: Trial, now: datetime) -> bool:
    return trial.expires_at is not None and trial.expires_at <= now and trial.status == "running"


def _window_end(trial: Trial) -> datetime | None:
    return trial.completed_at or trial.expires_at


def _runs(session: Any, trial: Trial) -> list[Run]:
    query = select(Run).where(Run.automation_id == trial.automation_id, Run.mode == trial.mode, Run.created_at >= trial.created_at)
    end = _window_end(trial)
    if end is not None:
        query = query.where(Run.created_at <= end)
    return list(session.execute(query).scalars())


def _receipts(session: Any, trial: Trial, run_ids: list[str]) -> list[ActionReceipt]:
    """Also catches a receipt tied to the automation but no trial run, so a stray write still shows."""
    window = [ActionReceipt.automation_id == trial.automation_id, ActionReceipt.created_at >= trial.created_at]
    end = _window_end(trial)
    if end is not None:
        window.append(ActionReceipt.created_at <= end)
    clause = and_(*window)
    return list(session.execute(select(ActionReceipt).where(or_(ActionReceipt.run_id.in_(run_ids), clause) if run_ids else clause)).scalars())


def _write_check(trial: Trial, receipts: list[ActionReceipt], runs: list[Run]) -> dict[str, Any]:
    if trial.mode in NON_LIVE_MODES:
        writes = [r for r in receipts if r.status != "preview"]
        return {"mode_allows_writes": False, "checked": True, "source": "action_receipts", "receipts_examined": len(receipts),
                "previews": len(receipts) - len(writes), "external_writes": len(writes), "write_receipt_ids": [r.id for r in writes][:MAX_LISTED],
                "verified_zero": bool(runs) and not writes}
    writes = [r for r in receipts if r.status in ("pending", "succeeded", "uncertain", "mismatch")]
    return {"mode_allows_writes": True, "checked": True, "source": "action_receipts", "receipts_examined": len(receipts),
            "previews": sum(r.status == "preview" for r in receipts), "external_writes": len(writes),
            "write_receipt_ids": [r.id for r in writes][:MAX_LISTED], "verified_writes": sum(r.verified for r in writes),
            "unverified_writes": sum(not r.verified for r in writes), "verified_zero": False}


def _costs(session: Any, trial: Trial, run_ids: list[str]) -> dict[str, Any]:
    rows = session.execute(select(UsageEvent.cost_basis, func.sum(UsageEvent.cost_usd), func.count())
                           .where(UsageEvent.run_id.in_(run_ids)).group_by(UsageEvent.cost_basis)).all() if run_ids else []
    reported = sum((Decimal(amount) for basis, amount, _ in rows if basis not in ESTIMATE_COST_BASES), Decimal(0))
    estimated = sum((Decimal(amount) for basis, amount, _ in rows if basis in ESTIMATE_COST_BASES), Decimal(0))
    total = reported + estimated
    return {"events": sum(count for _, _, count in rows), "by_basis": {basis: str(amount) for basis, amount, _ in rows},
            "provider_reported_usd": str(reported), "estimated_usd": str(estimated), "total_usd": str(total),
            "budget_usd": str(trial.budget_usd) if trial.budget_usd is not None else None,
            "over_budget": trial.budget_usd is not None and total > trial.budget_usd,
            "note": "Only provider_reported_usd came from a provider. Everything else is priced by Gluons from recorded usage."}


def _measurements(session: Any, trial: Trial) -> dict[str, Any]:
    rows = session.execute(select(TrialMeasurement).where(TrialMeasurement.trial_id == trial.id)).scalars().all()
    grouped: dict[str, dict[str, dict[str, Any]]] = {basis: {} for basis in BASES}
    for row in rows:
        entry = grouped[row.basis].setdefault(row.metric, {"metric": row.metric, "value": Decimal(0), "unit": row.unit, "denominator": 0, "sources": []})
        entry["value"] += row.value
        entry["denominator"] += 1
        if row.source and row.source not in entry["sources"]:
            entry["sources"].append(row.source)
    return {basis: [{**entry, "value": plain_number(entry["value"])} for entry in metrics.values()] for basis, metrics in grouped.items()}


def plain_number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _metric_total(measurements: list[dict[str, Any]], metric: str) -> Decimal:
    return next((Decimal(row["value"]) for row in measurements if row["metric"] == metric), Decimal(0))


def _comparison(trial: Trial, completed: int, accuracy: float | None, evaluated: int, costs: dict[str, Any], manual_minutes: Decimal) -> dict[str, Any]:
    try:
        baseline = validate_baseline(trial.baseline or {})
    except Exception:  # noqa: BLE001 - an unusable baseline is reported as unavailable, not guessed at
        return {"available": False, "reason": "No usable human baseline with a method and case count is stored on this trial."}
    per_case_cost = _ratio(float(costs["total_usd"]), completed)
    automation_error = round(1 - accuracy, 6) if accuracy is not None else None
    human_error, human_cost = baseline.get("error_rate"), baseline.get("cost_usd_per_case")
    return {"available": True, "basis": "owner_supplied_baseline_unverified", "source": baseline["source"], "method": baseline["method"],
            "human": {"cases": baseline["cases"], "minutes_per_case": baseline.get("minutes_per_case"),
                      "error_rate": human_error, "cost_usd_per_case": human_cost},
            "automation": {"cases": completed, "labelled_cases": evaluated, "error_rate": automation_error,
                           "cost_usd_per_case": per_case_cost, "manual_minutes_per_case": _ratio(float(manual_minutes), completed) if manual_minutes else None},
            "difference": {"error_rate": round(automation_error - human_error, 6) if automation_error is not None and human_error is not None else None,
                           "cost_usd_per_case": round(per_case_cost - human_cost, 6) if per_case_cost is not None and human_cost is not None else None}}


def _projection(trial: Trial, correct: int, manual_minutes: Decimal) -> dict[str, Any]:
    """A projection from a stated baseline. It is labelled so it can never pass as observed."""
    baseline = trial.baseline or {}
    per_case = baseline.get("minutes_per_case")
    empty = {"time_saved_minutes": None, "financial_savings_usd": None, "method": None, "denominator": correct}
    if not correct or per_case is None or not baseline.get("method"):
        return empty
    minutes = Decimal(str(per_case)) * correct - manual_minutes
    hourly = baseline.get("hourly_cost_usd")
    return {"time_saved_minutes": plain_number(minutes), "financial_savings_usd": str((minutes / 60 * Decimal(str(hourly))).quantize(Decimal("0.01"))) if hourly is not None else None,
            "method": "baseline minutes_per_case x correct labelled cases, minus recorded manual effort", "denominator": correct}


def _recommend(trial: Trial, automation: Automation, passed: bool, unrun: bool) -> dict[str, Any]:
    """One step at a time: shadow to supervised, then limited live. Regulated work stays supervised."""
    contract = get_registry().loaded(automation.skill_id).contract
    if contract.execution_scope == "draft_only":
        step, why = "none", "This skill only prepares drafts. It has no provider action graph to activate."
    elif not passed:
        step, why = "stay_in_trial", "Resolve the missing evidence listed in this report first."
    elif trial.mode in NON_LIVE_MODES:
        step, why = "supervised", "Run with a person approving each action before any unattended operation."
    elif contract.regulated:
        step, why = "supervised", "Regulated skills stay supervised by a licensed reviewer."
    else:
        step, why = "limited_live", "Go live with low per-action and monthly limits, keeping approval on R3 actions."
    return {"next_mode": step, "reason": why, "unlabelled_cases_outstanding": unrun, "authorizes_nothing": True,
            "requires": "An owner-signed policy in Gluons Web, whatever this recommends."}


def _blockers(trial: Trial, criteria: dict[str, Any] | None, criteria_error: str | None, *, runs: int, expected: int, evaluated: int,
              accuracy: float | None, failures: int, pending: int, unrun: int, writes: dict[str, Any], corrections: Decimal,
              over_budget: bool, expired: bool) -> list[str]:
    missing = []
    if criteria is None:
        missing.append(f"Store explicit pass_criteria on this trial before it runs: {criteria_error}")
    if not runs:
        missing.append("No runs were recorded, so there is nothing to judge.")
    if not expected:
        missing.append("No labelled ground truth is stored, so accuracy cannot be measured.")
    if unrun:
        missing.append(f"{unrun} labelled case(s) have no completed run, so the denominator is incomplete.")
    if criteria is not None:
        if evaluated < criteria["min_cases"]:
            missing.append(f"Evaluated {evaluated} labelled case(s); the criteria need {criteria['min_cases']}.")
        if accuracy is not None and accuracy < criteria["min_accuracy"]:
            missing.append(f"Accuracy {accuracy} is below the required {criteria['min_accuracy']}.")
        if failures > criteria["max_failed_runs"]:
            missing.append(f"{failures} run(s) failed; the criteria allow {criteria['max_failed_runs']}.")
        if criteria["max_corrections"] is not None and corrections > criteria["max_corrections"]:
            missing.append(f"{corrections} human correction(s) exceed the {criteria['max_corrections']} allowed.")
    if pending:
        missing.append(f"{pending} run(s) are unfinished.")
    if not writes["mode_allows_writes"] and writes["external_writes"]:
        missing.append(f"{writes['external_writes']} external write receipt(s) exist in a mode that must make none.")
    if writes.get("unverified_writes"):
        missing.append(f"{writes['unverified_writes']} external write(s) have no verified readback.")
    if over_budget:
        missing.append("Recorded cost exceeds this trial's budget.")
    if expired:
        missing.append("This trial expired before it was completed. Start a new one.")
    return missing


def _report(session: Any, trial: Trial, *, now: datetime | None = None) -> dict[str, Any]:
    current = now or utcnow()
    runs = _runs(session, trial)
    run_ids = [r.id for r in runs]
    by_event = {r.source_event_id: r for r in runs}
    raw_expected = trial.comparator.get("expected_outcomes", {})
    expected = raw_expected if isinstance(raw_expected, dict) else {}
    evaluated = [r for event, r in by_event.items() if event in expected and r.status == "completed"]
    mismatched = [r for r in evaluated if not _matches(r.outcome, expected[r.source_event_id])]
    correct = len(evaluated) - len(mismatched)
    completed = sum(r.status == "completed" for r in runs)
    failed = sum(r.status == "failed" for r in runs)
    reconciling = sum(r.status == "reconciliation_required" for r in runs)
    pending = sum(r.status not in ("completed", "failed", "cancelled") for r in runs)
    unrun = len(expected) - len(evaluated)
    accuracy = _ratio(correct, len(evaluated))
    writes = _write_check(trial, _receipts(session, trial, run_ids), runs)
    costs = _costs(session, trial, run_ids)
    measurements = _measurements(session, trial)
    corrections = _metric_total(measurements["measured"], CORRECTION_METRIC)
    manual = _metric_total(measurements["measured"], MANUAL_EFFORT_METRIC)
    denied = session.execute(select(func.count()).select_from(Approval).where(Approval.run_id.in_(run_ids), Approval.status == "denied")).scalar_one() if run_ids else 0
    criteria, criteria_error = _stored_criteria(trial)
    expired = _expired(trial, current) or trial.status == "expired"
    missing = _blockers(trial, criteria, criteria_error, runs=len(runs), expected=len(expected), evaluated=len(evaluated), accuracy=accuracy,
                        failures=failed + reconciling, pending=pending, unrun=unrun, writes=writes, corrections=corrections,
                        over_budget=costs["over_budget"], expired=expired)
    passed = not missing
    automation = session.get(Automation, trial.automation_id)
    return {
        "trial_id": trial.id, "automation_id": trial.automation_id, "mode": trial.mode, "status": trial.status, "passed": passed,
        "pass_criteria": criteria,
        "measured": {"runs": len(runs), "completed": completed, "failures": failed + reconciling, "pending": pending,
                     "external_writes": writes["external_writes"], "labelled_cases": len(evaluated), "correct_cases": correct, "accuracy": accuracy},
        "denominators": {"runs": len(runs), "completed_runs": completed, "labelled_expected": len(expected), "labelled_evaluated": len(evaluated),
                         "accuracy_denominator": len(evaluated), "failure_rate_denominator": len(runs)},
        "ground_truth": {"available": bool(expected), "labelled_cases": len(expected), "evaluated_cases": len(evaluated),
                         "coverage_of_completed_runs": _ratio(len(evaluated), completed), "unlabelled_completed_runs": completed - len(evaluated),
                         "labelled_without_completed_run": unrun, "source": "owner_supplied_labels" if expected else None},
        "errors": {"failed_runs": failed, "reconciliation_required": reconciling, "cancelled_runs": sum(r.status == "cancelled" for r in runs),
                   "denied_by_human": denied, "failure_rate": _ratio(failed + reconciling, len(runs)),
                   "mismatched_cases": [{"source_event_id": r.source_event_id, "run_id": r.id} for r in mismatched][:MAX_LISTED]},
        "corrections": {"count": plain_number(corrections), "source": "recorded by a person on this trial",
                        "manual_effort_minutes": plain_number(manual) if manual else None},
        "external_costs": costs, "external_write_check": writes,
        "observed": measurements["measured"],
        "estimated": {**_projection(trial, correct, manual), "recorded": measurements["estimated"]},
        "unverified": measurements["unverified"],
        "human_comparison": _comparison(trial, completed, accuracy, len(evaluated), costs, manual),
        "activation_recommendation": _recommend(trial, automation, passed, bool(unrun)),
        "quality_basis": "Owner-supplied labelled outcomes, compared with persisted run outcomes; no independent domain certification.",
        "missing_evidence": missing, "baseline": trial.baseline,
        "expired": expired, "expires_at": trial.expires_at.isoformat() if trial.expires_at else None,
        "created_at": trial.created_at.isoformat(), "completed_at": trial.completed_at.isoformat() if trial.completed_at else None,
    }


def get_report(principal: Principal, trial_id: str) -> dict[str, Any]:
    principal.require("trials:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        trial = session.get(Trial, trial_id)
        if trial is None:
            raise not_found("Trial")
        return trial.results if trial.status in ("completed", "expired") else _report(session, trial)


def list_all(principal: Principal) -> list[dict[str, Any]]:
    principal.require("trials:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return [{"id": t.id, "automation_id": t.automation_id, "mode": t.mode, "status": t.status,
                 "passed": t.results.get("passed") if t.results else None,
                 "expires_at": t.expires_at.isoformat() if t.expires_at else None, "created_at": t.created_at.isoformat()}
                for t in session.execute(select(Trial).order_by(Trial.created_at.desc()).limit(200)).scalars()]


def _expiry(days: int | None) -> datetime | None:
    if days is None:
        return None
    if type(days) is not int or not 1 <= days <= MAX_TRIAL_DAYS:
        raise validation_failed(f"A trial can run for 1 to {MAX_TRIAL_DAYS} days.")
    return utcnow() + timedelta(days=days)


def configure(principal: Principal, trial_id: str, *, pass_criteria: dict[str, Any] | None = None,
              expected_outcomes: dict[str, Any] | None = None, baseline: dict[str, Any] | None = None,
              expires_in_days: int | None = None) -> dict[str, Any]:
    """Criteria and baseline are fixed before the first run. Labels may arrive later, and that is audited."""
    principal.require("trials:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        trial = session.get(Trial, trial_id, with_for_update=True)
        if trial is None:
            raise not_found("Trial")
        if trial.status != "running":
            raise conflict("Only a running trial can be changed.")
        started = bool(_runs(session, trial))
        if started and (pass_criteria is not None or baseline is not None):
            raise conflict("Pass criteria and baseline are fixed once a run exists. Start a new trial to change them.")
        comparator = dict(trial.comparator)
        if pass_criteria is not None:
            comparator["pass_criteria"] = validate_criteria(pass_criteria)
        if expected_outcomes is not None:
            comparator["expected_outcomes"] = {**(comparator.get("expected_outcomes") or {}), **expected_outcomes}
        trial.comparator = comparator
        if baseline is not None:
            trial.baseline = validate_baseline(baseline)
        expiry = _expiry(expires_in_days)
        if expiry is not None:
            trial.expires_at = expiry
        audit.record(session, tenant_id=principal.tenant_id, category="automation", event_type="trial.configured", actor=principal,
                     resource_type="trial", resource_id=trial.id,
                     data={"criteria": comparator.get("pass_criteria"), "labels_added": len(expected_outcomes or {}), "labels_after_first_run": started and bool(expected_outcomes)})
        return _report(session, trial)


def start_supervised(principal: Principal, automation_id: str, expected_version: int, *, comparator: dict[str, Any] | None = None,
                     baseline: dict[str, Any] | None = None, budget_usd: Decimal | float | None = None,
                     expires_in_days: int | None = None) -> dict[str, Any]:
    """Measures live operation under per-action approval. The signed policy is what permits it."""
    principal.require("trials:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = session.get(Automation, automation_id, with_for_update=True)
        if automation is None:
            raise not_found("Automation")
        check_version(automation.version, expected_version)
        if automation.status != "active" or automation.mode != "supervised":
            raise conflict("A supervised trial needs an automation already active in supervised mode.")
        trial = Trial(id=new_id("trl"), tenant_id=principal.tenant_id, automation_id=automation_id, mode="supervised",
                      comparator=comparator or {}, baseline=baseline or {}, budget_usd=budget_usd,
                      expires_at=_expiry(expires_in_days), created_by=principal.actor_id)
        session.add(trial)
        audit.record(session, tenant_id=principal.tenant_id, category="automation", event_type="trial.started", actor=principal,
                     resource_type="trial", resource_id=trial.id, data={"mode": "supervised"})
        return {"trial_id": trial.id, "automation_id": automation_id, "mode": "supervised", "status": "running"}


def record_measurement(principal: Principal, trial_id: str, *, metric: str, value: Decimal | float | int, unit: str, basis: str,
                       source: str, run_id: str | None = None) -> dict[str, Any]:
    """Keeps a person's time study or correction count with its basis, so bases never blur."""
    principal.require("trials:write")
    if basis not in BASES:
        raise validation_failed("Basis must be measured, estimated or unverified.")
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0 or not metric.strip() or not unit.strip() or not source.strip():
        raise validation_failed("A measurement needs a metric, unit, source and a non-negative value.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        trial = session.get(Trial, trial_id, with_for_update=True)
        if trial is None:
            raise not_found("Trial")
        if trial.status != "running":
            raise conflict("A finished trial cannot change.")
        if run_id is not None and session.get(Run, run_id) is None:
            raise not_found("Run")
        row = TrialMeasurement(id=new_id("msr"), tenant_id=principal.tenant_id, trial_id=trial_id, run_id=run_id, metric=metric[:100],
                               value=amount, unit=unit[:40], basis=basis, source=source)
        session.add(row)
        audit.record(session, tenant_id=principal.tenant_id, category="automation", event_type="trial.measurement_recorded", actor=principal,
                     resource_type="trial", resource_id=trial_id, data={"metric": metric, "basis": basis})
        return {"id": row.id, "trial_id": trial_id, "metric": row.metric, "value": str(amount), "unit": row.unit, "basis": basis, "source": source}


def _close(session: Any, trial: Trial, automation: Automation, status: str, now: datetime) -> dict[str, Any]:
    """Freezes the report first: completing the row must not widen its own evidence window."""
    report = _report(session, trial, now=now)
    trial.status = status
    trial.completed_at = now
    report.update(status=status, completed_at=now.isoformat())
    trial.results = report
    if automation.status in ("trial_shadow", "trial_replay"):
        transition_automation(automation, "awaiting_activation")
    return report


def complete(principal: Principal, trial_id: str) -> dict[str, Any]:
    principal.require("trials:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        trial = session.get(Trial, trial_id, with_for_update=True)
        if trial is None:
            raise not_found("Trial")
        if trial.status in ("completed", "expired"):
            return trial.results
        automation = session.get(Automation, trial.automation_id, with_for_update=True)
        if trial.mode in NON_LIVE_MODES and automation.status not in ("trial_shadow", "trial_replay"):
            raise conflict("Only an active trial can be completed.")
        now = utcnow()
        if _report(session, trial, now=now)["measured"]["pending"]:
            raise conflict("Finish or cancel the pending trial runs first.")
        report = _close(session, trial, automation, "completed", now)
        audit.record(session, tenant_id=principal.tenant_id, category="automation", event_type="trial.completed", actor=principal,
                     resource_type="trial", resource_id=trial.id, data={"passed": report["passed"], "runs": report["measured"]["runs"]})
        return report


def expire_due(tenant_id: str, *, now: datetime | None = None) -> list[str]:
    """Worker sweep. An expired trial can never pass; it stops collecting and the owner is told."""
    current = now or datetime.now(UTC)
    closed = []
    with tenant_tx(tenant_id) as session:
        rows = session.execute(select(Trial).where(Trial.status == "running", Trial.expires_at <= current).with_for_update(skip_locked=True)).scalars().all()
        for trial in rows:
            automation = session.get(Automation, trial.automation_id, with_for_update=True)
            _close(session, trial, automation, "expired", current)
            events.notify(session, tenant_id, kind="trial_expired", severity="action", title=f"The trial for {automation.name} expired",
                          body="It stopped collecting evidence and cannot pass. Start a new trial to continue.",
                          link=f"/trials/{trial.id}", resource_type="trial", resource_id=trial.id)
            audit.record(session, tenant_id=tenant_id, category="automation", event_type="trial.expired", actor="scheduler",
                         resource_type="trial", resource_id=trial.id)
            closed.append(trial.id)
    return closed
