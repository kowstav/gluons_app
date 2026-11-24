"""Skills, plans, automations, policies. `/v1/tasks` mirrors `/v1/automations` (doc 12)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import AwareDatetime, BaseModel, Field

from gluons import audit, passkeys, policy, tasks
from gluons.actions import get_action
from gluons.api.deps import IdempotencyKey, PrincipalDep, SessionDep, idempotent
from gluons.core import GluonsError, auth_required, conflict, not_found, validation_failed
from gluons.db.models import AutomationPolicy, Plan
from gluons.db.session import tenant_tx
from gluons.runtime import engine, lifecycle
from gluons.skills import catalog, registry

router = APIRouter(prefix="/v1", tags=["automations"])


class Discover(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    industry: str | None = None


class CreatePlan(BaseModel):
    skill_id: str
    goal: str | None = Field(default=None, max_length=2000)
    business_context: dict[str, Any] = Field(default_factory=dict)


class CreateAutomation(BaseModel):
    skill_id: str
    name: str | None = Field(default=None, max_length=200)
    plan_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    trigger: dict[str, Any] = Field(default_factory=dict)


class Configure(BaseModel):
    config: dict[str, Any]
    expected_version: int


class Versioned(BaseModel):
    expected_version: int


class StartTrial(BaseModel):
    mode: Literal["historical_replay", "shadow"] = "shadow"
    expected_version: int
    comparator: dict[str, Any] = Field(default_factory=dict)
    baseline: dict[str, Any] = Field(default_factory=dict)
    budget_usd: float | None = Field(default=None, ge=0)


class Activate(BaseModel):
    mode: Literal["supervised", "live"]
    expected_version: int


class SubmitEvent(BaseModel):
    source_event_id: str = Field(min_length=1, max_length=300)
    source: str = Field(default="manual", max_length=40)
    payload: dict[str, Any]


class PolicyProposal(BaseModel):
    mode: Literal["supervised", "live"]
    allowed_actions: list[str]
    connection_ids: list[str] = Field(default_factory=list)
    resource_filters: dict[str, Any] = Field(default_factory=dict)
    requires_approval_for: list[str] = Field(default_factory=list)
    auto_limits: dict[str, Any] = Field(default_factory=dict)
    spend_limit_minor_per_month: int | None = Field(default=None, ge=0)
    per_action_limit_minor: int | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    data_disclosures: list[Any] = Field(default_factory=list)
    expires_at: AwareDatetime | None = None


@router.get("/skills", tags=["skills"])
def list_skills(view: SessionDep, industry: str | None = None, capability: str | None = None, q: str | None = None) -> dict[str, Any]:
    return {"skills": catalog.list_skills(industry=industry, capability=capability, query=q)}


@router.get("/skills/{skill_id}", tags=["skills"])
def get_skill(skill_id: str, view: SessionDep) -> dict[str, Any]:
    return catalog.get_skill(skill_id)


@router.post("/discover", tags=["skills"])
def discover(body: Discover, principal: PrincipalDep) -> dict[str, Any]:
    return catalog.discover(principal, body.goal, body.industry)


@router.post("/plans", status_code=201)
def create_plan(body: CreatePlan, principal: PrincipalDep) -> dict[str, Any]:
    return catalog.create_plan(principal, body.skill_id, goal=body.goal, business_context=body.business_context)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("plans:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise not_found("Plan")
        return catalog.serialize_plan(plan)


def _create(body: CreateAutomation, principal: PrincipalDep, key: str) -> dict[str, Any]:
    return idempotent(
        principal,
        key,
        body.model_dump(),
        lambda: lifecycle.create(
            principal,
            skill_id=body.skill_id,
            name=body.name,
            plan_id=body.plan_id,
            config=body.config,
            monthly_budget_usd=body.monthly_budget_usd,
            trigger=body.trigger,
        ),
    )


@router.post("/automations", status_code=201)
@router.post("/tasks", status_code=201)
def create_automation(body: CreateAutomation, principal: PrincipalDep, key: IdempotencyKey) -> dict[str, Any]:
    return _create(body, principal, key)


@router.get("/automations")
@router.get("/tasks")
def list_automations(principal: PrincipalDep) -> dict[str, Any]:
    return {"automations": lifecycle.list_all(principal)}


@router.get("/automations/{automation_id}")
@router.get("/tasks/{automation_id}")
def get_automation(automation_id: str, principal: PrincipalDep) -> dict[str, Any]:
    return tasks.task_status(principal, automation_id)


@router.patch("/automations/{automation_id}")
def configure(automation_id: str, body: Configure, principal: PrincipalDep) -> dict[str, Any]:
    return lifecycle.configure(principal, automation_id, body.config, body.expected_version)


@router.post("/automations/{automation_id}/start-trial")
@router.post("/tasks/{automation_id}/start-trial")
def start_trial(automation_id: str, body: StartTrial, principal: PrincipalDep) -> dict[str, Any]:
    return lifecycle.start_trial(
        principal,
        automation_id,
        body.mode,
        body.expected_version,
        comparator=body.comparator,
        baseline=body.baseline,
        budget_usd=body.budget_usd,
    )


@router.post("/automations/{automation_id}/activate")
def activate(automation_id: str, body: Activate, principal: PrincipalDep) -> dict[str, Any]:
    return lifecycle.activate(principal, automation_id, body.mode, body.expected_version)


@router.get("/automations/{automation_id}/readiness")
def readiness(automation_id: str, mode: str, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("automations:read")
    from gluons.db.models import Automation

    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = session.get(Automation, automation_id)
        if automation is None:
            raise not_found("Automation")
        return {"mode": mode, "missing": lifecycle.readiness(session, automation, mode)}


@router.post("/automations/{automation_id}/pause")
@router.post("/tasks/{automation_id}/pause")
def pause(automation_id: str, body: Versioned, principal: PrincipalDep) -> dict[str, Any]:
    return lifecycle.pause(principal, automation_id, body.expected_version)


@router.post("/automations/{automation_id}/resume")
@router.post("/tasks/{automation_id}/resume")
def resume(automation_id: str, body: Versioned, principal: PrincipalDep) -> dict[str, Any]:
    return lifecycle.resume(principal, automation_id, body.expected_version)


@router.post("/automations/{automation_id}/cancel")
@router.post("/tasks/{automation_id}/cancel")
def cancel(automation_id: str, body: Versioned, principal: PrincipalDep) -> dict[str, Any]:
    return lifecycle.cancel(principal, automation_id, body.expected_version)


@router.get("/automations/{automation_id}/runs")
def runs(automation_id: str, principal: PrincipalDep) -> dict[str, Any]:
    return {"runs": tasks.list_runs(principal, automation_id)}


@router.post("/automations/{automation_id}/events", status_code=202)
def submit_event(automation_id: str, body: SubmitEvent, principal: PrincipalDep) -> dict[str, Any]:
    """Manual or replay input. Provider events arrive through signed webhooks instead."""
    principal.require("trials:write")
    return lifecycle.submit_event(
        principal.tenant_id,
        automation_id,
        source=body.source,
        source_event_id=body.source_event_id,
        payload=body.payload,
        actor=principal,
    )


@router.get("/automations/{automation_id}/evidence")
@router.get("/tasks/{automation_id}/evidence")
def evidence(automation_id: str, principal: PrincipalDep) -> dict[str, Any]:
    return tasks.evidence(principal, automation_id)


@router.get("/runs/{run_id}")
def get_run(run_id: str, principal: PrincipalDep) -> dict[str, Any]:
    return tasks.get_run(principal, run_id)


@router.get("/runs/{run_id}/diagnosis")
def diagnosis(run_id: str, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("tasks:read")
    return engine.diagnose(principal.tenant_id, run_id)


@router.post("/automation-policies", status_code=201)
def propose_policy(automation_id: str, body: PolicyProposal, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("policies:write")
    if body.auto_limits:
        registry.get_registry()
    for action, allowance in body.auto_limits.items():
        if action == "dual_control" and isinstance(allowance, bool):
            continue
        spec = get_action(action)
        ceiling = allowance.get("max_amount_minor") if isinstance(allowance, dict) else None
        if (body.mode != "live" or not body.connection_ids or spec is None or not spec.is_write or spec.risk_tier != "R3"
                or spec.amount_minor is None or "currency" not in spec.input_model.model_fields
                or action not in body.allowed_actions or action in body.requires_approval_for
                or spec.permission in body.requires_approval_for or type(ceiling) is not int or ceiling <= 0
                or body.spend_limit_minor_per_month is None or body.spend_limit_minor_per_month <= 0):
            raise validation_failed("An unattended spending allowance needs an eligible live action, a named connection, and positive per-action and monthly limits.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return policy.serialize(policy.propose(session, principal, automation_id, body.model_dump()))


@router.get("/automations/{automation_id}/policies")
def list_policies(automation_id: str, principal: PrincipalDep) -> dict[str, Any]:
    from sqlalchemy import select

    principal.require("policies:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        rows = session.execute(
            select(AutomationPolicy)
            .where(AutomationPolicy.automation_id == automation_id)
            .order_by(AutomationPolicy.policy_version.desc())
        ).scalars()
        return {"policies": [policy.serialize(p) for p in rows]}


@router.post("/automation-policies/{policy_id}/passkey-options")
def sign_policy_options(policy_id: str, principal: PrincipalDep) -> dict[str, Any]:
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        draft = policy.owner_policy_draft(session, principal, policy_id)
        if not policy.requires_spending_passkey(draft):
            raise conflict("This policy does not require a spending passkey.")
        if not principal.user_id or not principal.session_id:
            raise auth_required()
        return passkeys.begin_policy(session, principal.user_id, principal.session_id, principal.tenant_id,
                                     policy.policy_challenge_hash(draft))


@router.post("/automation-policies/{policy_id}/sign")
def sign_policy(policy_id: str, principal: PrincipalDep, body: passkeys.Proof | None = None) -> dict[str, Any]:
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        draft = policy.owner_policy_draft(session, principal, policy_id)
        if policy.requires_spending_passkey(draft):
            if body is None or not principal.user_id or not principal.session_id:
                raise GluonsError("PASSKEY_REQUIRED", "Verify this spending allowance with your passkey.", status=403)
            passkeys.verify_policy(session, principal.user_id, principal.session_id, principal.tenant_id,
                                   policy.policy_challenge_hash(draft), body)
            audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="policy.passkey_verified",
                         actor=principal, resource_type="automation_policy", resource_id=draft.id,
                         data={"policy_version": draft.policy_version})
        return policy.serialize(policy.sign(session, principal, policy_id))


@router.post("/automation-policies/{policy_id}/revoke")
def revoke_policy(policy_id: str, principal: PrincipalDep) -> dict[str, Any]:
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return policy.serialize(policy.revoke(session, principal, policy_id))
