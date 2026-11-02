"""Owner operations on an automation. REST and MCP both call these same functions."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from gluons import audit, events, queue, run_log
from gluons.authz import Principal, Role
from gluons.core import GluonsError, conflict, new_id, not_found, utcnow, validation_failed
from gluons.db.models import (
    ActionReceipt,
    Approval,
    Automation,
    AutomationPolicy,
    Connection,
    Organization,
    PhoneRoute,
    Plan,
    Run,
    Trial,
)
from gluons.db.session import tenant_tx
from gluons.runtime.state import TERMINAL_RUN_STATES, check_version, transition_automation
from gluons.skills.registry import get_registry

TRIAL_MODES = {"historical_replay": "trial_replay", "shadow": "trial_shadow"}
LIVE_MODES = ("supervised", "live")
ACCEPTS_EVENTS = frozenset({"trial_replay", "trial_shadow", "active", "paused", "waiting_external"})


def _load(session: Session, automation_id: str, *, lock: bool = False) -> Automation:
    automation = session.get(Automation, automation_id, with_for_update=lock)
    if automation is None:
        raise not_found("Automation")
    return automation


def serialize(automation: Automation) -> dict[str, Any]:
    return {
        "id": automation.id,
        "tenant_id": automation.tenant_id,
        "name": automation.name,
        "skill_id": automation.skill_id,
        "skill_version": automation.skill_version,
        "status": automation.status,
        "mode": automation.mode,
        "risk_tier": automation.risk_tier,
        "config": automation.config,
        "source_connections": automation.source_connections,
        "trigger": automation.trigger,
        "monthly_budget_usd": str(automation.monthly_budget_usd) if automation.monthly_budget_usd is not None else None,
        "per_action_limit_usd": str(automation.per_action_limit_usd) if automation.per_action_limit_usd is not None else None,
        "model_policy": automation.model_policy,
        "current_policy_id": automation.current_policy_id,
        "execution_backend": automation.execution_backend,
        "created_via": automation.created_via,
        "created_at": automation.created_at.isoformat(),
        "version": automation.version,
        "next_required_action": _next_action(automation),
        "links": {"web": f"/tasks/{automation.id}"},
    }


def _next_action(automation: Automation) -> str | None:
    return {
        "draft": "configure",
        "configuring": "configure",
        "ready": "start_trial",
        "trial_replay": "review_trial",
        "trial_shadow": "review_trial",
        "awaiting_activation": "owner_activation",
        "waiting_external": "reconnect_provider",
        "reconciliation_required": "review_incident",
        "paused": "resume_or_cancel",
    }.get(automation.status)


def create(
    principal: Principal,
    *,
    skill_id: str,
    name: str | None = None,
    plan_id: str | None = None,
    config: dict[str, Any] | None = None,
    monthly_budget_usd: Decimal | float | None = None,
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    principal.require("automations:write")
    from gluons.assist import require_active
    require_active(principal)
    skill = get_registry().get(skill_id)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        if plan_id is not None:
            plan = session.get(Plan, plan_id)
            if plan is None:
                raise not_found("Plan")
            if plan.skill_id != skill.slug:
                raise validation_failed("That plan is for a different skill.")
        automation = Automation(
            id=new_id("aut"),
            tenant_id=principal.tenant_id,
            plan_id=plan_id,
            skill_id=skill.slug,
            skill_version=skill.version,
            name=name or skill.title,
            risk_tier=skill.risk_tier,
            config=config or {},
            trigger=trigger or {},
            monthly_budget_usd=monthly_budget_usd,
            mode="shadow",
            status="draft",
            created_by=principal.actor_id,
            created_via=principal.via,
        )
        session.add(automation)
        session.flush()
        transition_automation(automation, "ready")
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="automation",
            event_type="automation.created",
            actor=principal,
            resource_type="automation",
            resource_id=automation.id,
            data={"skill_id": skill.slug, "skill_version": skill.version, "via": principal.via},
        )
        return serialize(automation)


def get(principal: Principal, automation_id: str) -> dict[str, Any]:
    principal.require("automations:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return serialize(_load(session, automation_id))


def list_all(principal: Principal) -> list[dict[str, Any]]:
    principal.require("automations:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        rows = session.execute(select(Automation).order_by(Automation.created_at.desc())).scalars()
        return [serialize(a) for a in rows]


def configure(principal: Principal, automation_id: str, config: dict[str, Any], expected_version: int) -> dict[str, Any]:
    principal.require("automations:write")
    from gluons.assist import require_active
    require_active(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = _load(session, automation_id, lock=True)
        if principal.role == Role.FIELD_REP and (automation.created_by != principal.user_id or automation.mode not in TRIAL_MODES):
            raise GluonsError("FORBIDDEN", "A representative can configure only their own replay/shadow automation.", status=403)
        check_version(automation.version, expected_version)
        if automation.status not in ("draft", "configuring", "ready", "awaiting_activation"):
            raise conflict("Pause this automation before changing its configuration.")
        automation.config = {**automation.config, **config}
        automation.version += 1
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="automation",
            event_type="automation.configured",
            actor=principal,
            resource_type="automation",
            resource_id=automation_id,
            data={"keys": sorted(config)},
        )
        return serialize(automation)


def start_trial(
    principal: Principal,
    automation_id: str,
    mode: str,
    expected_version: int,
    *,
    comparator: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    budget_usd: Decimal | float | None = None,
) -> dict[str, Any]:
    """Replay and shadow need no provider grant: neither can touch an external system."""
    principal.require("trials:write")
    from gluons.assist import require_active
    require_active(principal)
    if mode not in TRIAL_MODES:
        raise validation_failed("A trial runs in historical_replay or shadow mode.")
    comparator = comparator or {}
    minimum = comparator.get("minimum_cases", 10)
    if type(minimum) is not int or not 1 <= minimum <= 10000:
        raise validation_failed("Trial minimum_cases must be an integer from 1 to 10000.")
    if not isinstance(comparator.get("expected_outcomes", {}), dict):
        raise validation_failed("Trial expected_outcomes must map source event IDs to labelled outcomes.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = _load(session, automation_id, lock=True)
        if principal.role == Role.FIELD_REP and automation.created_by != principal.user_id:
            raise GluonsError("FORBIDDEN", "A representative can trial only their own automation.", status=403)
        check_version(automation.version, expected_version)
        target = TRIAL_MODES[mode]
        if automation.status != target:
            transition_automation(automation, target)
        automation.mode = mode
        trial = Trial(
            id=new_id("trl"),
            tenant_id=principal.tenant_id,
            automation_id=automation_id,
            mode=mode,
            comparator=comparator or {},
            baseline=baseline or {},
            budget_usd=budget_usd,
            created_by=principal.actor_id,
        )
        session.add(trial)
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="automation",
            event_type="trial.started",
            actor=principal,
            resource_type="trial",
            resource_id=trial.id,
            data={"mode": mode},
        )
        return {**serialize(automation), "trial_id": trial.id}


def readiness(session: Session, automation: Automation, mode: str) -> list[dict[str, Any]]:
    """Everything still missing before this mode can start. Empty means ready."""
    skill = get_registry().get(automation.skill_id, automation.skill_version)
    missing: list[dict[str, Any]] = []
    if getattr(skill, "execution_scope", "provider_actions") == "draft_only":
        missing.append({"kind": "implementation", "detail": "This skill currently supports replay/shadow draft processing only."})
    policy = session.get(AutomationPolicy, automation.current_policy_id) if automation.current_policy_id else None
    if policy is None or policy.status != "active":
        missing.append({"kind": "policy", "detail": "An owner must sign an action policy."})
    elif policy.mode != mode:
        missing.append({"kind": "policy", "detail": f"The signed policy covers {policy.mode} mode, not {mode}."})

    bound = dict(automation.config.get("connections") or {})
    for requirement in skill.connectors:
        if not requirement.required_for_live:
            continue
        connection_id = bound.get(requirement.capability)
        connection = session.get(Connection, connection_id) if connection_id else None
        if connection is None or connection.disconnected_at is not None:
            missing.append(
                {
                    "kind": "connection",
                    "capability": requirement.capability,
                    "providers": requirement.providers,
                    "detail": requirement.purpose,
                }
            )
            continue
        if connection.status != "connected":
            missing.append({"kind": "connection_health", "capability": requirement.capability, "detail": connection.status})
        if connection.account_confirmed_at is None:
            missing.append(
                {
                    "kind": "account_confirmation",
                    "capability": requirement.capability,
                    "detail": f"Confirm that {connection.account_display} is the right account.",
                }
            )
        if requirement.capability == "voice":
            route = session.execute(
                select(PhoneRoute).where(PhoneRoute.automation_id == automation.id, PhoneRoute.state.in_(("verified", "active")))
            ).scalar_one_or_none()
            if route is None:
                missing.append({"kind": "phone_route", "detail": "A test call must reach the agent first."})

    if mode == "live":
        if skill.regulated:
            missing.append({"kind": "regulated", "detail": "Regulated skills stay supervised by a licensed reviewer."})
        if automation.monthly_budget_usd is None:
            missing.append({"kind": "budget", "detail": "Set a monthly spending limit."})
        passed = session.execute(
            select(Trial).where(
                Trial.automation_id == automation.id,
                Trial.status == "completed",
                Trial.results["passed"].as_boolean().is_(True),
            )
        ).first()
        if passed is None:
            missing.append({"kind": "trial", "detail": "Complete a passing trial before going live."})
    return missing


def activate(principal: Principal, automation_id: str, mode: str, expected_version: int) -> dict[str, Any]:
    principal.require("automations:activate")
    if mode not in LIVE_MODES:
        raise validation_failed("Activation is for supervised or live mode.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = _load(session, automation_id, lock=True)
        check_version(automation.version, expected_version)
        missing = readiness(session, automation, mode)
        if missing:
            raise GluonsError(
                "ACTIVATION_BLOCKED",
                "This automation is not ready to run yet.",
                status=409,
                details={"missing": missing},
            )
        if automation.status in ("trial_replay", "trial_shadow"):
            transition_automation(automation, "awaiting_activation")
        transition_automation(automation, "active")
        automation.mode = mode
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="permission",
            event_type="automation.activated",
            actor=principal,
            resource_type="automation",
            resource_id=automation_id,
            decision="activated",
            data={"mode": mode, "policy_id": automation.current_policy_id},
        )
        return serialize(automation)


def _in_flight(session: Session, automation_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        select(ActionReceipt).where(
            ActionReceipt.automation_id == automation_id, ActionReceipt.status.in_(("pending", "uncertain"))
        )
    ).scalars()
    return [{"receipt_id": r.id, "action": r.action, "status": r.status} for r in rows]


def pause(principal: Principal, automation_id: str, expected_version: int) -> dict[str, Any]:
    principal.require("tasks:control")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = _load(session, automation_id, lock=True)
        check_version(automation.version, expected_version)
        transition_automation(automation, "paused")
        in_flight = _in_flight(session, automation_id)
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="automation",
            event_type="automation.paused",
            actor=principal,
            resource_type="automation",
            resource_id=automation_id,
            data={"in_flight": in_flight},
        )
        return {
            **serialize(automation),
            "in_flight_actions": in_flight,
            "disclosure": (
                "No new actions will start. An action already in progress may still finish, "
                "and actions already completed are not undone."
            ),
        }


def resume(principal: Principal, automation_id: str, expected_version: int) -> dict[str, Any]:
    principal.require("tasks:control")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = _load(session, automation_id, lock=True)
        check_version(automation.version, expected_version)
        if automation.status != "paused":
            raise conflict("Only a paused automation can resume.")
        if automation.mode in LIVE_MODES:
            missing = [m for m in readiness(session, automation, automation.mode) if m["kind"] != "trial"]
            if missing:
                raise GluonsError(
                    "ACTIVATION_BLOCKED",
                    "This automation cannot resume until these are fixed.",
                    status=409,
                    details={"missing": missing},
                )
            transition_automation(automation, "active")
        else:
            transition_automation(automation, TRIAL_MODES[automation.mode])
        paused_runs = session.execute(
            select(Run.id).where(Run.automation_id == automation_id, Run.status == "paused")
        ).scalars()
        for run_id in paused_runs:
            queue.enqueue(
                session, "run.advance", {"run_id": run_id}, tenant_id=principal.tenant_id, dedupe_key=f"run.advance:{run_id}"
            )
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="automation",
            event_type="automation.resumed",
            actor=principal,
            resource_type="automation",
            resource_id=automation_id,
        )
        return serialize(automation)


def cancel(principal: Principal, automation_id: str, expected_version: int) -> dict[str, Any]:
    principal.require("tasks:control")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = _load(session, automation_id, lock=True)
        check_version(automation.version, expected_version)
        if automation.status not in ("cancel_requested", "cancelled"):
            if automation.status in ("draft", "configuring", "ready"):
                transition_automation(automation, "cancelled")
            else:
                transition_automation(automation, "cancel_requested")
        pending = session.execute(
            select(Approval).where(Approval.automation_id == automation_id, Approval.status == "pending").with_for_update()
        ).scalars()
        for approval in pending:
            approval.status = "cancelled"
            approval.version += 1
        queue.enqueue(
            session,
            "automation.finalize_cancel",
            {"automation_id": automation_id},
            tenant_id=principal.tenant_id,
            dedupe_key=f"finalize_cancel:{automation_id}",
        )
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="automation",
            event_type="automation.cancel_requested",
            actor=principal,
            resource_type="automation",
            resource_id=automation_id,
        )
        return {**serialize(automation), **_effects_report(session, automation_id)}


def _effects_report(session: Session, automation_id: str) -> dict[str, Any]:
    completed = session.execute(
        select(ActionReceipt).where(ActionReceipt.automation_id == automation_id, ActionReceipt.status == "succeeded")
    ).scalars()
    irreversible = [
        {"receipt_id": r.id, "action": r.action, "provider_object_id": r.provider_object_id}
        for r in completed
        if r.irreversible
    ]
    return {
        "irreversible_effects": irreversible,
        "in_flight_actions": _in_flight(session, automation_id),
        "disclosure": (
            "Queued work has stopped. Actions already completed are not undone; "
            "sent messages and calls cannot be recalled."
        ),
    }


def finalize_cancel(tenant_id: str, automation_id: str) -> bool:
    """Returns False while a worker still holds a run, so the job retries."""
    with tenant_tx(tenant_id) as session:
        automation = _load(session, automation_id, lock=True)
        runs = (
            session.execute(
                select(Run)
                .where(Run.automation_id == automation_id, Run.status.not_in(tuple(TERMINAL_RUN_STATES)))
                .with_for_update()
            )
            .scalars()
            .all()
        )
        now = utcnow()
        if any(r.status == "running" and r.lease_until is not None and r.lease_until > now for r in runs):
            return False
        for run in runs:
            run.status = "cancelled"
            run.finished_at = now
            run.version += 1
            run_log.append(session, tenant_id, run.id, "run.cancelled", {"reason": "owner_cancelled"})
        if automation.status == "cancel_requested":
            transition_automation(automation, "cancelled")
        events.notify(
            session,
            tenant_id,
            kind="automation_cancelled",
            title=f"{automation.name} was cancelled",
            link=f"/tasks/{automation_id}",
            resource_type="automation",
            resource_id=automation_id,
        )
    return True


def set_emergency_stop(principal: Principal, active: bool) -> None:
    """Stops every write for the whole business, immediately."""
    principal.require("emergency:stop")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id, with_for_update=True)
        org.emergency_stop_at = utcnow() if active else None
        org.emergency_stop_by = principal.actor_id if active else None
        org.version += 1
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="automation",
            event_type="emergency_stop.engaged" if active else "emergency_stop.released",
            actor=principal,
            resource_type="organization",
            resource_id=principal.tenant_id,
        )
        if not active:
            for run_id in session.execute(select(Run.id).where(Run.status == "paused")).scalars():
                queue.enqueue(
                    session, "run.advance", {"run_id": run_id}, tenant_id=principal.tenant_id, dedupe_key=f"run.advance:{run_id}"
                )


def set_paused_categories(principal: Principal, categories: list[str]) -> list[str]:
    """For requests like 'pause all outgoing customer communications'."""
    principal.require("tasks:control")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id, with_for_update=True)
        before = list(org.paused_categories)
        org.paused_categories = sorted(set(categories))
        org.version += 1
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="automation",
            event_type="categories.paused",
            actor=principal,
            resource_type="organization",
            resource_id=principal.tenant_id,
            data={"before": before, "after": org.paused_categories},
        )
        return org.paused_categories


def submit_event(
    tenant_id: str,
    automation_id: str,
    *,
    source: str,
    source_event_id: str,
    payload: dict[str, Any],
    actor: Principal | str = "webhook",
) -> dict[str, Any]:
    """Turns an inbound event into at most one run, however many times it is delivered."""
    if isinstance(actor, Principal):
        from gluons.assist import require_active
        require_active(actor)
    with tenant_tx(tenant_id) as session:
        automation = _load(session, automation_id)
        if isinstance(actor, Principal) and actor.role == Role.FIELD_REP and (automation.created_by != actor.user_id or automation.mode not in TRIAL_MODES):
            raise GluonsError("FORBIDDEN", "Representatives may submit sample events only to their own replay/shadow trial.", status=403)
        if automation.status not in ACCEPTS_EVENTS:
            raise conflict(f"This automation is {automation.status} and is not accepting work.")
        run_id = new_id("run")
        inserted = session.execute(
            insert(Run)
            .values(
                id=run_id,
                tenant_id=tenant_id,
                automation_id=automation_id,
                skill_id=automation.skill_id,
                skill_version=automation.skill_version,
                policy_id=automation.current_policy_id if automation.mode in LIVE_MODES else None,
                policy_version=None,
                mode=automation.mode,
                source=source,
                source_event_id=source_event_id,
                input=payload,
            )
            .on_conflict_do_nothing(constraint="uq_run_source_event")
            .returning(Run.id)
        ).first()
        if inserted is None:
            existing = session.execute(
                select(Run.id).where(Run.automation_id == automation_id, Run.source_event_id == source_event_id)
            ).scalar_one()
            return {"run_id": existing, "duplicate": True}
        run_log.append(session, tenant_id, run_id, "run.created", {"source": source, "mode": automation.mode})
        queue.enqueue(session, "run.advance", {"run_id": run_id}, tenant_id=tenant_id, dedupe_key=f"run.advance:{run_id}")
        audit.record(
            session,
            tenant_id=tenant_id,
            category="run",
            event_type="run.created",
            actor=actor,
            resource_type="run",
            resource_id=run_id,
            data={"source": source, "automation_id": automation_id},
        )
        return {"run_id": run_id, "duplicate": False}
