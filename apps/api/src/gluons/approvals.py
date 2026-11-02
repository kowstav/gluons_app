"""Human approvals: durable, expiring, single-use, and bound to one exact payload.

Approving one thing can never authorize a different one: the gateway recomputes the
payload fingerprint at execution time and refuses a mismatch. A decision is a signed
record tied to a verified person, not a model saying "approved".
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gluons import audit, events, queue
from gluons.authz import Principal, Role, risk_at_most
from gluons.config import get_settings
from gluons.core import (
    GluonsError,
    canonical_json,
    conflict,
    conflict_version,
    forbidden,
    hmac_sha256_hex,
    new_id,
    not_found,
    utcnow,
    validation_failed,
)
from gluons.db.models import Approval, ApprovalDecision

ROLE_RANK = {Role.APPROVER: 1, Role.ADMIN: 2, Role.OWNER: 3}


def request_approval(
    session: Session,
    *,
    tenant_id: str,
    action: str,
    risk_tier: str,
    title: str,
    payload_hash: str,
    kind: str = "action",
    summary: str = "",
    run_id: str | None = None,
    automation_id: str | None = None,
    node_id: str | None = None,
    external_system: str | None = None,
    connection_id: str | None = None,
    idempotency_key: str | None = None,
    records_before: dict[str, Any] | None = None,
    records_after_preview: dict[str, Any] | None = None,
    source_evidence: list[Any] | None = None,
    estimated_cost_usd: Decimal | float | None = None,
    amount_minor: int | None = None,
    policy_reference: str | None = None,
    required_role: str = "approver",
    required_approvals: int = 1,
    requested_by: str | None = None,
    ttl_seconds: int | None = None,
) -> Approval:
    """Idempotent: a retried step finds its pending approval instead of creating another."""
    existing = session.execute(
        select(Approval).where(
            Approval.tenant_id == tenant_id,
            Approval.payload_hash == payload_hash,
            Approval.run_id == run_id,
            Approval.status.in_(("pending", "approved")),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    ttl = ttl_seconds or get_settings().approval_default_ttl_seconds
    approval = Approval(
        id=new_id("apr"),
        tenant_id=tenant_id,
        kind=kind,
        run_id=run_id,
        automation_id=automation_id,
        node_id=node_id,
        action=action,
        risk_tier=risk_tier,
        title=title,
        summary=summary,
        external_system=external_system,
        connection_id=connection_id,
        payload_hash=payload_hash,
        idempotency_key=idempotency_key,
        records_before=records_before,
        records_after_preview=records_after_preview or {},
        source_evidence=source_evidence or [],
        estimated_cost_usd=estimated_cost_usd,
        amount_minor=amount_minor,
        policy_reference=policy_reference,
        required_role=required_role,
        required_approvals=required_approvals,
        requested_by=requested_by,
        expires_at=utcnow() + timedelta(seconds=ttl),
    )
    session.add(approval)
    session.flush()
    events.notify(
        session,
        tenant_id,
        kind="approval_requested",
        title=title,
        body=summary,
        severity="action",
        link=f"/approvals/{approval.id}",
        resource_type="approval",
        resource_id=approval.id,
    )
    audit.record(
        session,
        tenant_id=tenant_id,
        category="approval",
        event_type="approval.requested",
        actor="runtime",
        resource_type="approval",
        resource_id=approval.id,
        data={"action": action, "risk_tier": risk_tier, "payload_hash": payload_hash, "run_id": run_id},
    )
    return approval


def _sign_decision(approval: Approval, user_id: str, decision: str, decided_at: Any) -> str:
    settings = get_settings()
    document = canonical_json(
        {
            "approval_id": approval.id,
            "tenant_id": approval.tenant_id,
            "payload_hash": approval.payload_hash,
            "user_id": user_id,
            "decision": decision,
            "decided_at": decided_at,
        }
    )
    return hmac_sha256_hex(settings.key_bytes(settings.signing_key_b64), document)


def _check_authority(principal: Principal, approval: Approval, decision: str = "approve") -> None:
    principal.require("approvals:decide")
    role = principal.role
    if role is None or role not in ROLE_RANK:
        raise forbidden("This role cannot decide approvals.")
    required = Role(approval.required_role)
    if ROLE_RANK[role] < ROLE_RANK.get(required, 1):
        raise forbidden(f"This action needs a decision from a {required.value}.")
    # Refusing can only stop an action, so tier limits and the web-only rule guard approval alone.
    if decision == "deny":
        return
    if (approval.action or "").startswith("business_launch.") and principal.kind != "user":
        raise forbidden("Business launch decisions require the owner in Gluons Web.")
    if principal.approval_tier_limit and not risk_at_most(approval.risk_tier, principal.approval_tier_limit):
        raise forbidden("This is above your approval limit.")
    # R3 and R4 always stay on Gluons Web, whatever tier a binding somehow holds.
    if principal.kind == "assistant" and approval.risk_tier in ("R3", "R4"):
        raise forbidden("High-risk approvals stay on Gluons Web.")
    # Maker-checker: nobody but an owner approves a high-risk action they set in motion.
    if (
        approval.requested_by == principal.user_id
        and role != Role.OWNER
        and approval.risk_tier in ("R3", "R4")
    ):
        raise forbidden("You cannot approve a high-risk action you requested.")


def decide(
    session: Session,
    principal: Principal,
    approval_id: str,
    decision: str,
    *,
    reason: str | None = None,
    resolution: dict[str, Any] | None = None,
    expected_version: int | None = None,
) -> Approval:
    if decision not in ("approve", "deny"):
        raise validation_failed("Decision must be approve or deny.")
    approval = session.execute(select(Approval).where(Approval.id == approval_id).with_for_update()).scalar_one_or_none()
    if approval is None:
        raise not_found("Approval")
    if expected_version is not None and approval.version != expected_version:
        raise conflict_version(approval.version)
    now = utcnow()
    if approval.status != "pending":
        raise conflict(f"This approval is already {approval.status}.")
    if approval.expires_at <= now:
        approval.status = "expired"
        approval.version += 1
        raise GluonsError("APPROVAL_EXPIRED", "This approval has expired.", status=409)
    _check_authority(principal, approval, decision)
    if principal.user_id is None:
        raise forbidden("A signed-in person must decide this.")
    already = session.execute(
        select(ApprovalDecision).where(
            ApprovalDecision.approval_id == approval_id, ApprovalDecision.user_id == principal.user_id
        )
    ).scalar_one_or_none()
    if already is not None:
        raise conflict("You have already decided this approval.")
    session.add(
        ApprovalDecision(
            id=new_id("apd"),
            tenant_id=principal.tenant_id,
            approval_id=approval_id,
            user_id=principal.user_id,
            role=principal.role.value if principal.role else "unknown",
            decision=decision,
            reason=reason,
            via=principal.via,
            binding_id=principal.binding_id,
            signature=_sign_decision(approval, principal.user_id, decision, now),
            decided_at=now,
        )
    )
    session.flush()
    approvals_so_far = session.execute(
        select(func.count())
        .select_from(ApprovalDecision)
        .where(ApprovalDecision.approval_id == approval_id, ApprovalDecision.decision == "approve")
    ).scalar_one()
    if decision == "deny":
        approval.status = "denied"
        approval.decided_at = now
    elif approvals_so_far >= approval.required_approvals:
        approval.status = "approved"
        approval.decided_at = now
        approval.resolution = resolution
    approval.version += 1
    audit.record(
        session,
        tenant_id=principal.tenant_id,
        category="approval",
        event_type=f"approval.{decision}",
        actor=principal,
        resource_type="approval",
        resource_id=approval_id,
        decision=decision,
        data={"reason": reason, "status": approval.status, "payload_hash": approval.payload_hash},
    )
    if approval.status in ("approved", "denied") and approval.run_id:
        queue.enqueue(
            session,
            "run.advance",
            {"run_id": approval.run_id},
            tenant_id=principal.tenant_id,
            dedupe_key=f"run.advance:{approval.run_id}",
        )
    return approval


def consume(session: Session, approval: Approval, payload_hash: str) -> None:
    """Single use. Called in the same transaction that writes the pending receipt."""
    if approval.status != "approved":
        raise conflict("This action has not been approved.")
    if approval.expires_at <= utcnow():
        raise GluonsError("APPROVAL_EXPIRED", "This approval has expired.", status=409)
    if approval.payload_hash != payload_hash:
        raise GluonsError(
            "APPROVAL_PAYLOAD_MISMATCH",
            "The action changed after it was approved, so the approval no longer applies.",
            status=409,
        )
    if approval.consumed_at is not None:
        raise conflict("This approval has already been used.")
    approval.consumed_at = utcnow()


def expire_due(session: Session) -> int:
    rows = (
        session.execute(
            select(Approval)
            .where(Approval.status == "pending", Approval.expires_at <= utcnow())
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for approval in rows:
        approval.status = "expired"
        approval.version += 1
        if approval.run_id:
            queue.enqueue(
                session,
                "run.advance",
                {"run_id": approval.run_id},
                tenant_id=approval.tenant_id,
                dedupe_key=f"run.advance:{approval.run_id}",
            )
    return len(rows)


def serialize(approval: Approval, *, include_decisions: list[ApprovalDecision] | None = None) -> dict[str, Any]:
    data = {
        "id": approval.id,
        "kind": approval.kind,
        "run_id": approval.run_id,
        "automation_id": approval.automation_id,
        "action": approval.action,
        "risk_tier": approval.risk_tier,
        "title": approval.title,
        "summary": approval.summary,
        "external_system": approval.external_system,
        "connection_id": approval.connection_id,
        "records_before": approval.records_before,
        "records_after_preview": approval.records_after_preview,
        "source_evidence": approval.source_evidence,
        "estimated_cost_usd": str(approval.estimated_cost_usd) if approval.estimated_cost_usd is not None else None,
        "amount_minor": approval.amount_minor,
        "policy_reference": approval.policy_reference,
        "required_role": approval.required_role,
        "required_approvals": approval.required_approvals,
        "status": approval.status,
        "expires_at": approval.expires_at.isoformat(),
        "created_at": approval.created_at.isoformat(),
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "version": approval.version,
    }
    if include_decisions is not None:
        data["decisions"] = [
            {
                "user_id": d.user_id,
                "role": d.role,
                "decision": d.decision,
                "reason": d.reason,
                "via": d.via,
                "decided_at": d.decided_at.isoformat(),
            }
            for d in include_decisions
        ]
    return data
