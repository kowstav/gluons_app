"""Skill discovery, truthful capability status, and plans.

Status is derived from recorded evidence, never from a label in a file:
  implemented                     runs in shadow and its eval suite passed here
  sandbox_verified                a real provider sandbox receipt exists
  live_verified                   a real production receipt exists
  blocked_by_external_dependency  no evidence yet, and the contract names what is missing
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from gluons import audit
from gluons.actions import get_action
from gluons.authz import Principal
from gluons.core import new_id
from gluons.db.models import Connection, Organization, Plan, SkillEvidence, SkillVersion
from gluons.db.session import public_tx, system_tx, tenant_tx
from gluons.skills.registry import LoadedSkill, get_registry

# Owners search by problem, not by automation name (doc 01, Explore).
PROBLEM_SYNONYMS: dict[str, tuple[str, ...]] = {
    "orders": ("order", "orders", "purchase", "po", "inbox", "wholesale", "distributor"),
    "calls": ("call", "calls", "phone", "missed", "receptionist", "answer", "voice"),
    "inventory": ("inventory", "stock", "waste", "forecast", "food cost"),
    "dispatch": ("dispatch", "technician", "schedule", "route", "hvac", "plumbing"),
    "returns": ("return", "returns", "refund", "exchange", "rma"),
    "marketing": ("marketing", "ads", "seo", "social", "campaign", "creator", "post"),
    "sales": ("lead", "leads", "crm", "outreach", "sales", "cart", "abandoned"),
    "accounting": ("bookkeeping", "reconcile", "reconciliation", "invoice", "vat", "payroll", "tax"),
    "documents": ("report", "inspection", "estimate", "takeoff", "appraisal", "title", "closing"),
    "hiring": ("hiring", "candidate", "recruit", "screening", "applicant"),
    "discovery": ("automate", "repetitive", "workflow", "what can", "find ways", "process"),
    "commerce": ("store", "shopify", "ecommerce", "e-commerce", "shop", "fulfillment", "shipping"),
}


def evidence_by_skill(session: Session) -> dict[str, list[SkillEvidence]]:
    rows = session.execute(select(SkillEvidence).order_by(SkillEvidence.recorded_at.desc())).scalars()
    grouped: dict[str, list[SkillEvidence]] = defaultdict(list)
    for row in rows:
        grouped[row.skill_id].append(row)
    return grouped


def capability_status(skill: LoadedSkill, evidence: list[SkillEvidence]) -> dict[str, Any]:
    version = skill.contract.version
    current = [e for e in evidence if e.version == version and e.passed]
    kinds = {e.kind for e in current}
    last = current[0].recorded_at.isoformat() if current else None
    if skill.contract.execution_scope == "draft_only":
        status = "blocked_by_external_dependency"
    elif "live_receipt" in kinds:
        status = "live_verified"
    elif "sandbox_receipt" in kinds:
        status = "sandbox_verified"
    elif "eval_suite" in kinds:
        status = "implemented"
    else:
        status = "blocked_by_external_dependency" if skill.contract.blockers else "implemented_unverified"
    return {
        "status": status,
        "evidence_kinds": sorted(kinds),
        "last_verified_at": last,
        "blockers": [b.model_dump() for b in skill.contract.blockers],
        "execution_scope": skill.contract.execution_scope,
        "draft_evaluated": "draft_eval_suite" in kinds,
    }


def _summary(skill: LoadedSkill, evidence: list[SkillEvidence]) -> dict[str, Any]:
    contract = skill.contract
    return {
        "id": contract.slug,
        "title": contract.title,
        "version": contract.version,
        "industry": skill.catalog.get("industry") or skill.blueprint.get("industry"),
        "domain": contract.domain,
        "trigger": skill.blueprint.get("trigger"),
        "outcome": skill.blueprint.get("owner_outcome"),
        "risk_tier": contract.risk_tier,
        "regulated": contract.regulated,
        "activation_modes": contract.activation_modes,
        "execution_scope": contract.execution_scope,
        "connectors": [c.model_dump() for c in contract.connectors],
        "capability": capability_status(skill, evidence),
    }


def list_skills(*, industry: str | None = None, capability: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
    with public_tx() as session:
        evidence = evidence_by_skill(session)
    results = []
    for skill in get_registry().all():
        summary = _summary(skill, evidence.get(skill.contract.slug, []))
        if industry and industry.lower() not in (summary["industry"] or "").lower():
            continue
        if capability and summary["capability"]["status"] != capability:
            continue
        if query and _score(skill, query) == 0:
            continue
        results.append(summary)
    return results


def get_skill(skill_id: str) -> dict[str, Any]:
    skill = get_registry().loaded(skill_id)
    with public_tx() as session:
        evidence = evidence_by_skill(session).get(skill.contract.slug, [])
    contract = skill.contract
    return {
        **_summary(skill, evidence),
        "description": skill.blueprint.get("description"),
        "required_data": skill.blueprint.get("required_data", []),
        "workflow": skill.blueprint.get("workflow", []),
        "failure_branches": skill.blueprint.get("failure_branches", []),
        "invariants": skill.blueprint.get("invariants"),
        "metrics": contract.metrics,
        "actions": [_action_view(name) for name in contract.actions],
        "inputs": contract.inputs,
        "outputs": contract.outputs,
        "limitations": contract.limitations,
        "graph": {
            "entry": contract.graph.entry,
            "nodes": [
                {"id": n.id, "primitive": n.primitive, "next": n.next, "branches": n.branches}
                for n in contract.graph.nodes.values()
            ],
        },
    }


def _action_view(name: str) -> dict[str, Any]:
    spec = get_action(name)
    if spec is None:
        return {"name": name}
    return {
        "name": name,
        "permission": spec.permission,
        "risk_tier": spec.risk_tier,
        "capability": spec.capability,
        "description": spec.description,
        "irreversible": spec.irreversible,
        "is_write": spec.is_write,
    }


def _score(skill: LoadedSkill, text: str) -> int:
    words = set(re.findall(r"[a-z0-9-]+", text.lower()))
    lowered = text.lower()
    haystack = " ".join(
        str(skill.blueprint.get(k, "")) for k in ("title", "industry", "domain", "trigger", "owner_outcome", "description")
    ).lower()
    score = sum(2 for word in words if len(word) > 3 and word in haystack)
    for group, terms in PROBLEM_SYNONYMS.items():
        if any(term in lowered for term in terms) and (group in haystack or any(t in haystack for t in terms)):
            score += 3
    return score


def discover(principal: Principal, goal: str, industry: str | None = None, *, limit: int = 5) -> dict[str, Any]:
    """Ranks skills from the real catalog against what this business has actually connected."""
    principal.require("skills:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id)
        connected = {
            c.provider
            for c in session.execute(select(Connection).where(Connection.disconnected_at.is_(None))).scalars()
        }
        org_industry = industry or (org.industry if org else None)
        evidence = evidence_by_skill(session)
    ranked = []
    for skill in get_registry().all():
        score = _score(skill, goal)
        if org_industry and org_industry.lower() in str(skill.blueprint.get("industry", "")).lower():
            score += 4
        if score == 0:
            continue
        needed = [c for c in skill.contract.connectors if c.required_for_live]
        ready = sum(1 for c in needed if connected & set(c.providers))
        feasibility = "ready_to_connect" if ready == len(needed) else "needs_connections"
        ranked.append(
            (
                score + ready,
                {
                    **_summary(skill, evidence.get(skill.contract.slug, [])),
                    "feasibility": feasibility,
                    "missing_connections": [c.capability for c in needed if not connected & set(c.providers)],
                },
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    return {
        "goal": goal,
        "connected_providers": sorted(connected),
        "opportunities": [item for _, item in ranked[:limit]],
        "basis": "Ranked from catalog text and your connected tools. No savings are implied.",
    }


def _cost_estimate(skill: LoadedSkill) -> dict[str, Any]:
    metered = [a for a in skill.contract.actions if (spec := get_action(a)) and spec.capability in ("voice", "messaging")]
    return {
        "basis": "estimated",
        "model_usd_per_task": {"low": 0.002, "high": 0.05},
        "note": "Depends on the chosen model and message length. Provider charges such as calls and messages are billed separately.",
        "metered_actions": metered,
    }


def create_plan(
    principal: Principal, skill_id: str, *, goal: str | None = None, business_context: dict[str, Any] | None = None
) -> dict[str, Any]:
    principal.require("plans:write")
    skill = get_registry().loaded(skill_id)
    contract = skill.contract
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        connections = session.execute(select(Connection).where(Connection.disconnected_at.is_(None))).scalars().all()
        required = []
        for requirement in contract.connectors:
            match = next((c for c in connections if c.provider in requirement.providers), None)
            required.append(
                {
                    "capability": requirement.capability,
                    "providers": requirement.providers,
                    "purpose": requirement.purpose,
                    "required_for_live": requirement.required_for_live,
                    "status": match.status if match else "not_connected",
                    "connection_id": match.id if match else None,
                    "account": match.account_display if match else None,
                }
            )
        side_effects = [_action_view(name) for name in contract.actions]
        plan = Plan(
            id=new_id("pln"),
            tenant_id=principal.tenant_id,
            skill_id=contract.slug,
            skill_version=contract.version,
            goal=goal,
            business_context=business_context or {},
            graph={"entry": contract.graph.entry, "nodes": list(contract.graph.nodes)},
            required_connections=required,
            side_effects=side_effects,
            cost_estimate=_cost_estimate(skill),
            created_by=principal.actor_id,
            created_via=principal.via,
        )
        session.add(plan)
        session.flush()
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="automation",
            event_type="plan.created",
            actor=principal,
            resource_type="plan",
            resource_id=plan.id,
            data={"skill_id": contract.slug, "via": principal.via},
        )
        return serialize_plan(plan)


def serialize_plan(plan: Plan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "skill_id": plan.skill_id,
        "skill_version": plan.skill_version,
        "goal": plan.goal,
        "status": plan.status,
        "required_connections": plan.required_connections,
        "side_effects": plan.side_effects,
        "cost_estimate": plan.cost_estimate,
        "default_mode": "shadow",
        "created_at": plan.created_at.isoformat(),
        "links": {"web": f"/plans/{plan.id}"},
        "next_required_action": "connect" if any(
            r["required_for_live"] and r["status"] != "connected" for r in plan.required_connections
        ) else "start_trial",
    }


def sync_versions() -> int:
    """Pins each validated contract in the database so runs can reference a version."""
    registry = get_registry()
    with system_tx() as session:
        for skill in registry.all():
            session.execute(
                insert(SkillVersion)
                .values(
                    skill_id=skill.contract.slug,
                    version=skill.contract.version,
                    contract_hash=skill.contract_hash,
                    blueprint_hash=skill.blueprint_hash,
                    contract=skill.contract.model_dump(mode="json"),
                )
                .on_conflict_do_update(
                    index_elements=["skill_id", "version"],
                    set_={"contract_hash": skill.contract_hash, "blueprint_hash": skill.blueprint_hash},
                )
            )
    return len(registry)


def record_evidence(skill_id: str, version: str, kind: str, passed: bool, detail: dict[str, Any], recorded_by: str) -> None:
    with system_tx() as session:
        session.add(
            SkillEvidence(
                id=new_id("evd"),
                skill_id=skill_id,
                version=version,
                kind=kind,
                passed=passed,
                detail=detail,
                recorded_by=recorded_by,
            )
        )
