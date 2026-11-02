"""The deterministic authorization gate for every side effect.

No model output reaches this module as a decision input: it sees action names, payload
fingerprints and stored owner grants. Prose in an email, a prompt or a tool argument
cannot change an outcome.

Doc 13 section 5 separates connecting an account from permission to act: holding
`gmail.send` grants nothing here until an owner signs a policy allowing that action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gluons import audit
from gluons.actions import ActionSpec
from gluons.authz import Principal, Role
from gluons.billing import month_start
from gluons.config import get_settings
from gluons.core import (
    canonical_json,
    conflict,
    constant_time_equals,
    forbidden,
    hmac_sha256_hex,
    new_id,
    not_found,
    utcnow,
    validation_failed,
)
from gluons.db.models import ActionReceipt, Automation, AutomationPolicy, Connection, Organization

NON_LIVE_MODES = ("historical_replay", "shadow")
# Statuses that have consumed, or may yet consume, the policy's spend allowance.
SPEND_COUNTED = ("pending", "succeeded", "uncertain", "mismatch")


class Outcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    PREVIEW = "preview"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reasons: tuple[str, ...] = ()
    policy_id: str | None = None
    policy_version: int | None = None
    required_role: str = "approver"
    required_approvals: int = 1


@dataclass(frozen=True)
class PolicyInput:
    tenant_id: str
    automation_id: str | None
    mode: str
    action: ActionSpec
    connection_id: str | None
    amount_minor: int | None = None
    resources: dict[str, Any] = field(default_factory=dict)
    pinned_policy_id: str | None = None
    skill_actions: tuple[str, ...] | None = None


def deny(*reasons: str, policy: AutomationPolicy | None = None) -> Decision:
    return Decision(
        Outcome.DENY,
        reasons,
        policy.id if policy else None,
        policy.policy_version if policy else None,
    )


def _signing_key() -> bytes:
    settings = get_settings()
    return settings.key_bytes(settings.signing_key_b64)


def _policy_document(policy: AutomationPolicy) -> str:
    return canonical_json(
        {
            "id": policy.id,
            "tenant_id": policy.tenant_id,
            "automation_id": policy.automation_id,
            "policy_version": policy.policy_version,
            "mode": policy.mode,
            "allowed_actions": sorted(policy.allowed_actions),
            "connection_ids": sorted(policy.connection_ids),
            "resource_filters": policy.resource_filters,
            "requires_approval_for": sorted(policy.requires_approval_for),
            "auto_limits": policy.auto_limits,
            "spend_limit_minor_per_month": policy.spend_limit_minor_per_month,
            "per_action_limit_minor": policy.per_action_limit_minor,
            "currency": policy.currency,
            "approved_by": policy.approved_by,
            "approved_at": policy.approved_at,
            "expires_at": policy.expires_at,
        }
    )


def signature_valid(policy: AutomationPolicy) -> bool:
    if not policy.signature:
        return False
    return constant_time_equals(policy.signature, hmac_sha256_hex(_signing_key(), _policy_document(policy)))


def _resource_violations(policy: AutomationPolicy, action: ActionSpec, resources: dict[str, Any]) -> list[str]:
    rules = policy.resource_filters.get(action.name) or {}
    violations: list[str] = []
    allowed = rules.get("allowed_recipients")
    recipient = resources.get("recipient")
    if allowed is not None and recipient not in allowed:
        violations.append("RECIPIENT_NOT_ALLOWED")
    domains = rules.get("allowed_recipient_domains")
    if domains is not None and (not recipient or str(recipient).rsplit("@", 1)[-1].lower() not in domains):
        violations.append("RECIPIENT_DOMAIN_NOT_ALLOWED")
    if rules.get("recipient_must_be_verified_customer") and not resources.get("recipient_verified"):
        violations.append("RECIPIENT_NOT_VERIFIED_CUSTOMER")
    if rules.get("requires_verified_order") and not resources.get("order_verified"):
        violations.append("ORDER_NOT_VERIFIED")
    if rules.get("requires_opt_in") and not resources.get("opted_in"):
        violations.append("CONSENT_MISSING")
    return violations


def evaluate(session: Session, request: PolicyInput) -> Decision:
    """Order matters: stops that apply to everyone run before per-automation grants."""
    action = request.action
    if request.skill_actions is not None and action.name not in request.skill_actions:
        return deny("ACTION_NOT_DECLARED_BY_SKILL")

    # Shadow and replay are decided first: they can never reach a provider write, so a
    # missing grant, connection or budget must not stop an owner from trying a skill.
    if request.mode in NON_LIVE_MODES:
        return Decision(Outcome.PREVIEW, ("MODE_FORBIDS_EXTERNAL_WRITES",))

    if action.risk_tier == "R4":
        return deny("R4_HUMAN_ONLY")

    org = session.get(Organization, request.tenant_id)
    if org is None:
        return deny("TENANT_MISSING")
    if action.is_write and org.emergency_stop_at is not None:
        return deny("EMERGENCY_STOP_ACTIVE")
    if action.is_write and action.permission in org.paused_categories:
        return deny("CATEGORY_PAUSED")

    automation = session.get(Automation, request.automation_id) if request.automation_id else None
    if automation is None:
        return deny("AUTOMATION_REQUIRED")
    if automation.status in ("paused", "cancel_requested", "cancelled", "failed", "completed"):
        return deny("AUTOMATION_NOT_RUNNABLE")

    policy_id = request.pinned_policy_id or automation.current_policy_id
    if policy_id is None:
        return deny("NO_ACTIVE_POLICY")
    # Locked so concurrent spend checks against this policy serialise.
    policy = session.execute(
        select(AutomationPolicy).where(AutomationPolicy.id == policy_id).with_for_update()
    ).scalar_one_or_none()
    if policy is None or policy.automation_id != automation.id:
        return deny("NO_ACTIVE_POLICY")
    if policy.status == "revoked" or policy.revoked_at is not None:
        return deny("POLICY_REVOKED", policy=policy)
    if policy.status not in ("active", "superseded") or policy.approved_by is None:
        return deny("POLICY_NOT_APPROVED", policy=policy)
    if not signature_valid(policy):
        return deny("POLICY_SIGNATURE_INVALID", policy=policy)
    if policy.expires_at is not None and policy.expires_at <= utcnow():
        return deny("POLICY_EXPIRED", policy=policy)
    if policy.mode != request.mode:
        return deny("POLICY_MODE_MISMATCH", policy=policy)
    if action.name not in policy.allowed_actions:
        return deny("ACTION_NOT_GRANTED", policy=policy)

    connection = session.get(Connection, request.connection_id) if request.connection_id else None
    if connection is None or connection.tenant_id != request.tenant_id:
        return deny("CONNECTION_REQUIRED", policy=policy)
    if connection.status != "connected" or connection.disconnected_at is not None:
        return deny("CONNECTION_NOT_HEALTHY", policy=policy)
    if policy.connection_ids and connection.id not in policy.connection_ids:
        return deny("CONNECTION_NOT_IN_POLICY", policy=policy)
    if action.is_write and connection.account_confirmed_at is None:
        return deny("ACCOUNT_IDENTITY_UNCONFIRMED", policy=policy)
    required_scopes = action.required_scopes.get(connection.provider, ())
    missing = [scope for scope in required_scopes if scope not in connection.granted_scopes]
    if missing:
        # Provider scope is a hard ceiling: no owner grant can exceed it.
        return deny("PROVIDER_SCOPE_MISSING", policy=policy)

    violations = _resource_violations(policy, action, request.resources)
    if violations:
        return deny(*violations, policy=policy)

    amount = request.amount_minor
    if amount is not None:
        if policy.per_action_limit_minor is not None and amount > policy.per_action_limit_minor:
            return deny("PER_ACTION_LIMIT_EXCEEDED", policy=policy)
        if policy.spend_limit_minor_per_month is not None:
            spent = session.execute(
                select(func.coalesce(func.sum(ActionReceipt.amount_minor), 0)).where(
                    ActionReceipt.policy_id == policy.id,
                    ActionReceipt.status.in_(SPEND_COUNTED),
                    ActionReceipt.created_at >= month_start(),
                )
            ).scalar_one()
            if int(spent) + amount > policy.spend_limit_minor_per_month:
                return deny("MONTHLY_SPEND_LIMIT_EXCEEDED", policy=policy)

    return _approval_requirement(policy, action, amount, request.mode)


def _approval_requirement(
    policy: AutomationPolicy, action: ActionSpec, amount: int | None, mode: str
) -> Decision:
    decided = {"policy_id": policy.id, "policy_version": policy.policy_version}
    dual = action.risk_tier == "R3" and policy.auto_limits.get("dual_control") is True
    approval = Decision(
        Outcome.REQUIRE_APPROVAL,
        required_role="owner" if action.risk_tier == "R3" else "approver",
        required_approvals=2 if dual else 1,
        **decided,
    )
    if not action.is_write:
        return Decision(Outcome.ALLOW, ("READ_GRANTED",), **decided)
    if mode == "supervised":
        return _with_reason(approval, "SUPERVISED_MODE")
    if action.name in policy.requires_approval_for or action.permission in policy.requires_approval_for:
        return _with_reason(approval, "POLICY_REQUIRES_APPROVAL")
    if action.risk_tier == "R3":
        limit = (policy.auto_limits.get(action.name) or {}).get("max_amount_minor")
        if limit is None or amount is None or amount > limit:
            return _with_reason(approval, "R3_REQUIRES_APPROVAL")
    return Decision(Outcome.ALLOW, ("WITHIN_POLICY",), **decided)


def _with_reason(decision: Decision, reason: str) -> Decision:
    return Decision(
        decision.outcome,
        (reason,),
        decision.policy_id,
        decision.policy_version,
        decision.required_role,
        decision.required_approvals,
    )


# --- Owner-facing policy lifecycle ------------------------------------------------------


def propose(session: Session, principal: Principal, automation_id: str, spec: dict[str, Any]) -> AutomationPolicy:
    principal.require("policies:write")
    automation = session.get(Automation, automation_id)
    if automation is None:
        raise not_found("Automation")
    if spec["mode"] not in ("supervised", "live"):
        raise validation_failed("A policy covers supervised or live operation.")
    latest = session.execute(
        select(func.coalesce(func.max(AutomationPolicy.policy_version), 0)).where(
            AutomationPolicy.automation_id == automation_id
        )
    ).scalar_one()
    policy = AutomationPolicy(
        id=new_id("pol"),
        tenant_id=principal.tenant_id,
        automation_id=automation_id,
        policy_version=latest + 1,
        status="proposed",
        mode=spec["mode"],
        allowed_actions=sorted(set(spec.get("allowed_actions", []))),
        connection_ids=sorted(set(spec.get("connection_ids", []))),
        resource_filters=spec.get("resource_filters", {}),
        requires_approval_for=sorted(set(spec.get("requires_approval_for", []))),
        auto_limits=spec.get("auto_limits", {}),
        spend_limit_minor_per_month=spec.get("spend_limit_minor_per_month"),
        per_action_limit_minor=spec.get("per_action_limit_minor"),
        currency=spec.get("currency", "USD"),
        data_disclosures=spec.get("data_disclosures", []),
        expires_at=spec.get("expires_at"),
        supersedes=automation.current_policy_id,
        created_by=principal.actor_id,
    )
    session.add(policy)
    session.flush()
    audit.record(
        session,
        tenant_id=principal.tenant_id,
        category="permission",
        event_type="policy.proposed",
        actor=principal,
        resource_type="automation_policy",
        resource_id=policy.id,
        data={"automation_id": automation_id, "mode": policy.mode, "allowed_actions": policy.allowed_actions},
    )
    return policy


def sign(session: Session, principal: Principal, policy_id: str) -> AutomationPolicy:
    """The owner's explicit grant. Reachable only from an authenticated web session."""
    principal.require("policies:sign")
    if principal.kind != "user" or principal.role != Role.OWNER:
        raise forbidden("Only an owner signed in to Gluons can grant this.")
    policy = session.get(AutomationPolicy, policy_id, with_for_update=True)
    if policy is None:
        raise not_found("Policy")
    if policy.status != "proposed":
        raise conflict("This policy version has already been decided.")
    policy.approved_by = principal.actor_id
    policy.approved_at = utcnow()
    if policy.expires_at is None:
        policy.expires_at = policy.approved_at + timedelta(days=365)
    policy.signature = hmac_sha256_hex(_signing_key(), _policy_document(policy))
    policy.status = "active"
    automation = session.get(Automation, policy.automation_id, with_for_update=True)
    if automation is None:
        raise not_found("Automation")
    previous_id = automation.current_policy_id
    if previous_id:
        previous = session.get(AutomationPolicy, previous_id, with_for_update=True)
        if previous is not None and previous.status == "active":
            previous.status = "superseded"  # in-flight runs stay pinned to what was approved
    automation.current_policy_id = policy.id
    automation.version += 1
    audit.record(
        session,
        tenant_id=principal.tenant_id,
        category="permission",
        event_type="policy.signed",
        actor=principal,
        resource_type="automation_policy",
        resource_id=policy.id,
        decision="granted",
        data={"policy_version": policy.policy_version, "supersedes": previous_id},
    )
    return policy


def revoke(session: Session, principal: Principal, policy_id: str) -> AutomationPolicy:
    principal.require("tasks:control")
    policy = session.get(AutomationPolicy, policy_id, with_for_update=True)
    if policy is None:
        raise not_found("Policy")
    if policy.status == "revoked":
        return policy
    policy.status = "revoked"
    policy.revoked_at = utcnow()
    policy.revoked_by = principal.actor_id
    automation = session.get(Automation, policy.automation_id, with_for_update=True)
    if automation is not None and automation.current_policy_id == policy.id:
        automation.current_policy_id = None
    audit.record(
        session,
        tenant_id=principal.tenant_id,
        category="permission",
        event_type="policy.revoked",
        actor=principal,
        resource_type="automation_policy",
        resource_id=policy.id,
        decision="revoked",
    )
    return policy


def serialize(policy: AutomationPolicy) -> dict[str, Any]:
    return {
        "id": policy.id,
        "automation_id": policy.automation_id,
        "policy_version": policy.policy_version,
        "status": policy.status,
        "mode": policy.mode,
        "allowed_actions": policy.allowed_actions,
        "connection_ids": policy.connection_ids,
        "resource_filters": policy.resource_filters,
        "requires_approval_for": policy.requires_approval_for,
        "auto_limits": policy.auto_limits,
        "spend_limit_minor_per_month": policy.spend_limit_minor_per_month,
        "per_action_limit_minor": policy.per_action_limit_minor,
        "currency": policy.currency,
        "data_disclosures": policy.data_disclosures,
        "approved_by": policy.approved_by,
        "approved_at": policy.approved_at.isoformat() if policy.approved_at else None,
        "expires_at": policy.expires_at.isoformat() if policy.expires_at else None,
    }
