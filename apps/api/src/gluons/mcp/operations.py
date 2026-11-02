"""Readiness, cost and policy views shared by MCP tools and REST.

Everything here reports or requests. Nothing signs a policy, activates an automation or
widens a limit on an assistant's say-so: doc 13 keeps those decisions with the owner.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from gluons import audit, billing, events, model_gateway, policy
from gluons.actions import get_action
from gluons.authz import Principal
from gluons.config import get_settings
from gluons.core import GluonsError, not_found, validation_failed
from gluons.db.models import Automation, AutomationPolicy, Connection, Organization
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle
from gluons.runtime.state import check_version
from gluons.skills.registry import get_registry

ASSUMED_TOKENS = {"input_tokens": 2000, "output_tokens": 500}
LIVE_MODES = ("supervised", "live")
MAX_BUDGET_USD = Decimal("9999999999.99")


def _web(path: str) -> str:
    return f"{get_settings().public_web_url.rstrip('/')}{path}"


def _action(name: str) -> dict[str, Any]:
    spec = get_action(name)
    if spec is None:
        return {"name": name, "risk_tier": None, "side_effect": "unknown_action"}
    return {"name": name, "risk_tier": spec.risk_tier, "permission": spec.permission, "capability": spec.capability,
            "description": spec.description, "irreversible": spec.irreversible, "is_write": spec.is_write}


def estimate_cost(principal: Principal, skill_id: str) -> dict[str, Any]:
    """Priced only from registered, evaluated models; an empty registry returns no number."""
    principal.require("skills:read")
    skill = get_registry().loaded(skill_id)
    contract = skill.contract
    metered = [a["name"] for a in map(_action, contract.actions) if a.get("capability") in ("voice", "messaging")]
    if contract.execution_scope == "draft_only":
        model_cost: dict[str, Any] = {"status": "deterministic_no_model", "usd_per_task_low": "0", "usd_per_task_high": "0"}
    else:
        priced = [model_gateway.task_cost(row, ASSUMED_TOKENS) for row in model_gateway.eligible_models(principal.tenant_id, contract.slug)]
        model_cost = {"status": "no_evaluated_model_configured", "usd_per_task_low": None, "usd_per_task_high": None}
        if priced:
            model_cost = {"status": "estimated", "usd_per_task_low": str(min(priced)), "usd_per_task_high": str(max(priced)),
                          "assumed_tokens_per_task": ASSUMED_TOKENS}
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id)
        budget = str(org.monthly_budget_usd) if org and org.monthly_budget_usd is not None else None
    return {"skill_id": contract.slug, "basis": "estimated", "model_cost": model_cost, "metered_actions": metered,
            "provider_charges": "Calls, messages and other provider charges are billed by each provider and are not included.",
            "monthly_budget_usd": budget, "links": {"web": _web(f"/skills/{contract.slug}")}}


def missing_connections(principal: Principal, *, skill_id: str | None = None, automation_id: str | None = None) -> dict[str, Any]:
    """Uses this business's real connections; a bound automation counts only what it is bound to."""
    principal.require("connections:read")
    if bool(skill_id) == bool(automation_id):
        raise validation_failed("Name either a skill or an automation.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        bound: dict[str, str] | None = None
        if automation_id:
            automation = session.get(Automation, automation_id)
            if automation is None:
                raise not_found("Automation")
            skill_id = automation.skill_id
            bound = dict(automation.config.get("connections") or {})
        connections = session.execute(select(Connection).where(Connection.disconnected_at.is_(None))).scalars().all()
        contract = get_registry().loaded(skill_id).contract
        missing, ready = [], []
        for requirement in contract.connectors:
            usable = [c for c in connections if c.provider in requirement.providers and c.status == "connected"]
            if bound is not None:
                usable = [c for c in usable if c.id == bound.get(requirement.capability)]
            row = {"capability": requirement.capability, "providers": requirement.providers,
                   "purpose": requirement.purpose, "required_for_live": requirement.required_for_live}
            if usable:
                ready.append({**row, "connection_id": usable[0].id, "account": usable[0].account_display,
                              "account_confirmed": usable[0].account_confirmed_at is not None})
            else:
                missing.append(row)
    return {"skill_id": contract.slug, "automation_id": automation_id, "missing": missing, "ready": ready,
            "next_step": "Use connection_begin; the owner completes provider consent in a browser." if missing else None,
            "notice": "A connection grants provider access only. An owner must still sign a policy before anything acts."}


def _load(session: Any, automation_id: str) -> Automation:
    automation = session.get(Automation, automation_id)
    if automation is None:
        raise not_found("Automation")
    return automation


def _current(session: Any, automation: Automation) -> AutomationPolicy | None:
    return session.get(AutomationPolicy, automation.current_policy_id) if automation.current_policy_id else None


def policy_preview(principal: Principal, automation_id: str) -> dict[str, Any]:
    principal.require("policies:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = _load(session, automation_id)
        contract = get_registry().get(automation.skill_id, automation.skill_version)
        loaded = get_registry().loaded(automation.skill_id).contract
        current = _current(session, automation)
        actions = [_action(name) for name in contract.actions]
        return {
            "automation_id": automation.id, "skill_id": automation.skill_id, "execution_scope": loaded.execution_scope,
            "proposed": {"default_mode": "shadow", "actions": actions,
                         "always_require_approval": [a["name"] for a in actions if a.get("risk_tier") in ("R3", "R4")],
                         "activation_modes": loaded.activation_modes},
            "limits": {"monthly_budget_usd": str(automation.monthly_budget_usd) if automation.monthly_budget_usd is not None else None,
                       "per_action_limit_usd": str(automation.per_action_limit_usd) if automation.per_action_limit_usd is not None else None},
            "data_disclosures": current.data_disclosures if current else [
                {"capability": c.capability, "providers": c.providers, "purpose": c.purpose} for c in loaded.connectors],
            "current_policy": policy.serialize(current) if current else None,
            "grants_nothing": True, "links": {"web": _web(f"/tasks/{automation.id}/policy")},
        }


def policy_status(principal: Principal, automation_id: str) -> dict[str, Any]:
    principal.require("policies:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = _load(session, automation_id)
        current = _current(session, automation)
        return {
            "automation_id": automation.id, "automation_status": automation.status, "mode": automation.mode,
            "live": automation.status == "active" and automation.mode in LIVE_MODES,
            "signed_policy": policy.serialize(current) if current else None,
            "approved_actions": current.allowed_actions if current else [],
            "limits": {"spend_limit_minor_per_month": current.spend_limit_minor_per_month if current else None,
                       "per_action_limit_minor": current.per_action_limit_minor if current else None,
                       "monthly_budget_usd": str(automation.monthly_budget_usd) if automation.monthly_budget_usd is not None else None},
            "version": automation.version, "links": {"web": _web(f"/tasks/{automation.id}/policy")},
        }


def _owner_steps(automation_id: str, mode: str) -> list[str]:
    return [f"Sign in to Gluons Web and open {_web(f'/tasks/{automation_id}/policy')}.",
            f"Review the actions, limits and data disclosures for {mode} mode.",
            "Sign the policy yourself. An assistant cannot sign it.",
            "Finish anything listed under missing, then activate."]


def request_activation(principal: Principal, automation_id: str, mode: str) -> dict[str, Any]:
    """Tells the owner what is being asked for. The request itself authorizes nothing."""
    principal.require("plans:write")
    if mode not in LIVE_MODES:
        raise validation_failed("Activation covers supervised or live mode.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = _load(session, automation_id)
        missing = lifecycle.readiness(session, automation, mode)
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="policy.activation_requested",
                     actor=principal, resource_type="automation", resource_id=automation.id, data={"mode": mode, "via": principal.via})
        events.notify(session, principal.tenant_id, kind="policy_activation_requested", severity="action",
                      title=f"Activation requested for {automation.name}",
                      body=f"{principal.via} asked to run this in {mode} mode. Nothing changes until you sign a policy.",
                      link=f"/tasks/{automation.id}/policy", resource_type="automation", resource_id=automation.id)
    return {"status": "owner_confirmation_required", "self_approved": False, "automation_id": automation_id, "mode": mode,
            "missing": missing, "required_owner_steps": _owner_steps(automation_id, mode),
            "links": {"web": _web(f"/tasks/{automation_id}/policy")}}


def request_policy_change(principal: Principal, automation_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Doc 02 `set_task_policy`. Records the diff for the owner; only the owner can sign it."""
    principal.require("plans:write")
    if spec.get("mode") not in LIVE_MODES:
        raise validation_failed("A policy covers supervised or live operation.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = _load(session, automation_id)
        declared = set(get_registry().get(automation.skill_id, automation.skill_version).actions)
        requested = sorted(set(spec.get("allowed_actions") or []))
        undeclared = [name for name in requested if name not in declared]
        if undeclared:
            raise validation_failed("This skill does not declare those actions.", undeclared=undeclared)
        current = _current(session, automation)
        before = set(current.allowed_actions) if current else set()
        diff = {"mode": {"before": current.mode if current else None, "after": spec["mode"]},
                "actions_added": sorted(set(requested) - before), "actions_removed": sorted(before - set(requested)),
                "per_action_limit_minor": {"before": current.per_action_limit_minor if current else None,
                                           "after": spec.get("per_action_limit_minor")},
                "spend_limit_minor_per_month": {"before": current.spend_limit_minor_per_month if current else None,
                                                "after": spec.get("spend_limit_minor_per_month")}}
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="policy.change_requested",
                     actor=principal, resource_type="automation", resource_id=automation.id, data={"diff": diff})
        events.notify(session, principal.tenant_id, kind="policy_change_requested", severity="action",
                      title=f"Policy change requested for {automation.name}",
                      body=f"{principal.via} asked for a policy change. Review it and sign it in Gluons Web if you agree.",
                      link=f"/tasks/{automation.id}/policy", resource_type="automation", resource_id=automation.id)
    return {"status": "owner_signature_required", "applied": False, "automation_id": automation_id, "diff": diff,
            "required_owner_steps": _owner_steps(automation_id, spec["mode"]),
            "links": {"web": _web(f"/tasks/{automation_id}/policy")}}


def _money(value: Decimal | float | int | str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise validation_failed("Provide a budget in US dollars.") from exc
    if amount < 0 or amount > MAX_BUDGET_USD:
        raise validation_failed("A budget must be between 0 and the platform limit.")
    return amount


def set_budget(principal: Principal, monthly_budget_usd: Decimal | float | int | str, *, automation_id: str | None = None,
               expected_version: int | None = None) -> dict[str, Any]:
    """Anyone who can stop a task can tighten its budget. Raising one is financial authority."""
    principal.require("tasks:control")
    amount = _money(monthly_budget_usd)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row: Any = session.get(Automation, automation_id, with_for_update=True) if automation_id \
            else session.get(Organization, principal.tenant_id, with_for_update=True)
        if row is None:
            raise not_found("Automation")
        check_version(row.version, expected_version)
        before = row.monthly_budget_usd
        if before is not None and amount > before and not principal.can("billing:manage"):
            raise GluonsError("OWNER_WEB_REQUIRED", "Only the owner can raise a budget, in Gluons Web.", status=403,
                              details={"current_usd": str(before), "requested_usd": str(amount),
                                       "web_url": _web("/settings/billing")})
        row.monthly_budget_usd = amount
        row.version += 1
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="budget.changed", actor=principal,
                     resource_type="automation" if automation_id else "organization",
                     resource_id=automation_id or principal.tenant_id,
                     data={"before_usd": str(before) if before is not None else None, "after_usd": str(amount), "via": principal.via})
        return {"automation_id": automation_id, "monthly_budget_usd": str(amount),
                "previous_budget_usd": str(before) if before is not None else None,
                "spent_this_month": billing.spending_summary(session, principal.tenant_id)["total_usd"],
                "version": row.version}
