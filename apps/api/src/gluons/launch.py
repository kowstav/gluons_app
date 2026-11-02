"""Persistent commerce launch planning and owner/provider handoffs.

The service creates real Gluons plans, approvals and audit records. Unimplemented
Shopify/payment/supplier actions stay blocked. A plan's spending estimate grants no
permission to spend, and a person's click can never verify merchant KYC or checkout.
"""
from __future__ import annotations

import re
from datetime import UTC
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gluons import approvals, audit
from gluons.authz import Principal
from gluons.core import conflict, fingerprint, forbidden, new_id, not_found, utcnow, validation_failed
from gluons.db.models import Approval, ApprovalDecision, AuditEvent, Connection, LaunchEvidence, LaunchPlan
from gluons.db.session import tenant_tx
from gluons.runtime.state import check_version

STAGES = (
    ("market_research", "Research market and product evidence", "research_sources"),
    ("owner_plan_approval", "Approve business requirements and planning budget", "owner_approval"),
    ("shopify_signup_or_partner_transfer", "Owner completes Shopify merchant signup or official Partner transfer", "owner_provider_handoff"),
    ("verify_store_connection", "Verify store identity and authorized scopes", "provider_connection"),
    ("draft_storefront_and_catalog", "Stage storefront and catalog drafts", "shopify_connector"),
    ("source_suppliers_and_compare", "Collect dated sources and compare landed supplier costs", "supplier_sources"),
    ("owner_approved_supplier_outreach", "Approve recipient, content, entity and sending account before outreach", "owner_approval"),
    ("negotiate_within_bounds", "Compare quotes and approve commercial commitments", "supplier_connector"),
    ("verify_payment_eligibility_and_merchant_kyc", "Owner/provider completes and verifies payment eligibility, identity and banking", "provider_verified_kyc"),
    ("verify_tax_shipping_checkout", "Verify taxes, shipping and a permitted test checkout", "provider_test_receipts"),
    ("owner_go_live_approval", "Owner approves exact catalog and store publication", "owner_approval"),
    ("publish_catalog", "Publish only the approved catalog and verify provider readback", "shopify_connector"),
    ("monitor_orders_and_fulfillment", "Verify payments, supplier acceptance, tracking and physical fulfillment", "fulfillment_connector"),
    ("reconcile_customer_notifications_and_costs", "Reconcile approved notifications and measured direct costs", "verified_provider_receipts"),
)
FULFILLMENT_MODELS = frozenset({"inventory", "dropship", "3pl", "undecided"})


def _load(session: Session, plan_id: str, *, lock: bool = False) -> LaunchPlan:
    plan = session.get(LaunchPlan, plan_id, with_for_update=lock)
    if plan is None:
        raise not_found("Business launch plan")
    return plan


def _brief(plan: LaunchPlan) -> dict[str, Any]:
    return {
        "plan_id": plan.id, "goal": plan.goal, "country": plan.country,
        "product_category": plan.product_category, "budget_usd": str(plan.budget_usd) if plan.budget_usd is not None else None,
        "fulfillment_model": plan.fulfillment_model,
        "scope": "Research and setup planning only. No spending, outreach, KYC or publication is authorized.",
    }


def _touch(session: Session, principal: Principal, plan: LaunchPlan, event: str, detail: dict[str, Any] | None = None) -> None:
    plan.version += 1
    plan.updated_at = utcnow()
    audit.record(
        session, tenant_id=principal.tenant_id, category="business_launch", event_type=f"launch.{event}",
        actor=principal, resource_type="launch_plan", resource_id=plan.id,
        data={"stage": plan.stage, "branch_status": plan.branch_status, **(detail or {})},
    )


def _serialize(plan: LaunchPlan) -> dict[str, Any]:
    return {
        "id": plan.id, "goal": plan.goal, "country": plan.country, "product_category": plan.product_category,
        "budget_usd": str(plan.budget_usd) if plan.budget_usd is not None else None,
        "fulfillment_model": plan.fulfillment_model, "stage": plan.stage, "branch_status": plan.branch_status,
        "checklist": plan.checklist, "state": plan.state, "version": plan.version,
        "created_at": plan.created_at.astimezone(UTC).isoformat(), "updated_at": plan.updated_at.astimezone(UTC).isoformat(),
        "links": {"web": f"/business-launch/plans/{plan.id}"},
        "capability": {
            "planning": "implemented", "external_commerce": "blocked_by_external_dependency",
            "blockers": ["Shopify provider connector and approved app access", "Supplier outreach permissions and connector", "Payment-provider verified status", "Test checkout and fulfillment provider receipts"],
        },
        "payment_status": "unverified", "store_live": False,
    }


def create_plan(
    principal: Principal, *, goal: str, country: str | None = None, product_category: str | None = None,
    budget_usd: float | Decimal | None = None, fulfillment_model: str | None = None,
) -> dict[str, Any]:
    principal.require("launch:write")
    if not isinstance(goal, str) or not 3 <= len(goal.strip()) <= 10000:
        raise validation_failed("Provide a business goal between 3 and 10,000 characters.")
    if country is not None and not re.fullmatch(r"[A-Za-z]{2}", country):
        raise validation_failed("Country must be a two-letter country code.")
    if product_category is not None and len(product_category) > 200:
        raise validation_failed("Product category must be at most 200 characters.")
    if fulfillment_model is not None and fulfillment_model not in FULFILLMENT_MODELS:
        raise validation_failed("Choose inventory, dropship, 3pl or undecided fulfillment.")
    try:
        budget = Decimal(str(budget_usd)) if budget_usd is not None else None
        if budget is not None and (not budget.is_finite() or budget < 0 or budget > Decimal("9999999999.99")):
            raise ValueError
    except (InvalidOperation, ValueError):
        raise validation_failed("Budget must be a finite nonnegative USD amount.") from None
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = LaunchPlan(
            id=new_id("lnp"), tenant_id=principal.tenant_id, goal=goal.strip(), country=country.upper() if country else None,
            product_category=product_category, budget_usd=budget, fulfillment_model=fulfillment_model,
            checklist=[{"id": key, "title": title, "requires": dependency, "status": "pending"} for key, title, dependency in STAGES],
            state={"started": False, "external_actions_performed": 0}, created_by=principal.actor_id, created_via=principal.via,
        )
        session.add(plan)
        session.flush()
        audit.record(
            session, tenant_id=principal.tenant_id, category="business_launch", event_type="launch.created",
            actor=principal, resource_type="launch_plan", resource_id=plan.id, data={"stage": plan.stage},
        )
        return _serialize(plan)


def list_plans(principal: Principal) -> list[dict[str, Any]]:
    principal.require("launch:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return [_serialize(plan) for plan in session.scalars(select(LaunchPlan).order_by(LaunchPlan.created_at.desc()))]


def get_plan(principal: Principal, plan_id: str) -> dict[str, Any]:
    principal.require("launch:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return _serialize(_load(session, plan_id))


def start(principal: Principal, plan_id: str, expected_version: int) -> dict[str, Any]:
    principal.require("launch:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = _load(session, plan_id, lock=True)
        check_version(plan.version, expected_version)
        if plan.branch_status in {"PAUSED", "CANCELLED"}:
            raise conflict("This plan has been paused or cancelled.")
        if plan.state.get("started"):
            old = session.get(Approval, plan.state.get("approval_id"), with_for_update=True)
            if old and old.status == "pending" and old.expires_at <= utcnow():
                old.status = "expired"
                old.version += 1
            if plan.stage != "INTENT_CAPTURED" or old is None or old.status not in {"expired", "denied"}:
                raise conflict("This plan already has an active planning approval.")
        brief = _brief(plan)
        approval = approvals.request_approval(
            session, tenant_id=principal.tenant_id, kind="launch_plan", action="business_launch.plan_approve", risk_tier="R2",
            title="Approve this business launch planning brief", summary=brief["scope"], payload_hash=fingerprint(brief),
            records_after_preview=brief, required_role="owner", requested_by=principal.actor_id,
            source_evidence=[{"plan_id": plan.id, "source": "owner_supplied_objective"}],
        )
        session.add(LaunchEvidence(
            id=new_id("lev"), tenant_id=principal.tenant_id, plan_id=plan.id, stage=plan.stage,
            kind="planning_brief", is_test=True, source="gluons", detail={**brief, "market_research_performed": False},
        ))
        plan.state = {**plan.state, "started": True, "planning_brief": brief, "approval_id": approval.id, "next_required_action": "owner_plan_approval"}
        plan.branch_status = "WAITING_FOR_OWNER"
        _touch(session, principal, plan, "started", {"approval_id": approval.id})
        return _serialize(plan)


def _advance_after_owner_approval(session: Session, principal: Principal, plan: LaunchPlan) -> None:
    if plan.stage != "INTENT_CAPTURED":
        return
    approval = session.get(Approval, plan.state.get("approval_id"), with_for_update=True)
    if approval and approval.status == "pending" and approval.expires_at <= utcnow():
        approval.status = "expired"
        approval.version += 1
    if approval is None or approval.status == "pending":
        plan.branch_status = "WAITING_FOR_OWNER"
        return
    if approval.status != "approved":
        plan.branch_status = "NEEDS_REVIEW"
        plan.state = {**plan.state, "next_required_action": f"planning_approval_{approval.status}"}
        return
    # Plan approval stays on Gluons Web, even if an assistant has general approval
    # scope. An owner/provider must still separately authorize every later action.
    owner_decision = session.scalar(select(ApprovalDecision).where(
        ApprovalDecision.approval_id == approval.id, ApprovalDecision.role == "owner",
        ApprovalDecision.via == "web", ApprovalDecision.decision == "approve",
    ))
    if owner_decision is None:
        raise forbidden("The business owner must approve the planning brief in Gluons Web.")
    approvals.consume(session, approval, fingerprint(_brief(plan)))
    plan.stage = "STORE_ONBOARDING_REQUIRED"
    plan.branch_status = "WAITING_FOR_OWNER"
    plan.checklist = [{**item, "status": "approved" if item["id"] == "owner_plan_approval" else item["status"]} for item in plan.checklist]
    plan.state = {**plan.state, "next_required_action": "owner_shopify_signup_or_partner_transfer", "planning_approval_consumed": True}
    session.add(LaunchEvidence(
        id=new_id("lev"), tenant_id=principal.tenant_id, plan_id=plan.id, stage="BUSINESS_PLAN_APPROVED",
        kind="owner_approval", is_test=False, source="gluons", detail={"approval_id": approval.id, "scope": "planning_only"},
    ))


def pause(principal: Principal, plan_id: str, expected_version: int) -> dict[str, Any]:
    principal.require("launch:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = _load(session, plan_id, lock=True)
        check_version(plan.version, expected_version)
        if plan.branch_status in {"PAUSED", "CANCELLED"}:
            raise conflict("This plan is already paused or cancelled.")
        plan.state = {**plan.state, "branch_before_pause": plan.branch_status}
        plan.branch_status = "PAUSED"
        _touch(session, principal, plan, "paused")
        return {**_serialize(plan), "disclosure": "Future plan work is paused. Completed external actions cannot be undone."}


def resume(principal: Principal, plan_id: str, expected_version: int) -> dict[str, Any]:
    principal.require("launch:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = _load(session, plan_id, lock=True)
        check_version(plan.version, expected_version)
        if plan.branch_status == "CANCELLED" or not plan.state.get("started"):
            raise conflict("Start an uncancelled plan before resuming it.")
        if plan.branch_status == "PAUSED":
            plan.branch_status = plan.state.get("branch_before_pause")
        _advance_after_owner_approval(session, principal, plan)
        if plan.stage == "STORE_CONNECTED":
            connection = session.get(Connection, plan.state.get("store_connection_id"))
            if connection is None or connection.disconnected_at is not None or connection.status != "connected":
                plan.stage = "STORE_ONBOARDING_REQUIRED"
                plan.branch_status = "WAITING_FOR_OWNER"
                plan.state = {**plan.state, "next_required_action": "reconnect_store"}
            else:
                plan.branch_status = "WAITING_FOR_PROVIDER"
        _touch(session, principal, plan, "resumed")
        return _serialize(plan)


def bind_store(principal: Principal, plan_id: str, connection_id: str, expected_version: int) -> dict[str, Any]:
    principal.require("launch:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = _load(session, plan_id, lock=True)
        check_version(plan.version, expected_version)
        if plan.branch_status in {"PAUSED", "CANCELLED"} or plan.stage != "STORE_ONBOARDING_REQUIRED":
            raise conflict("This plan is not waiting for a store connection.")
        connection = session.get(Connection, connection_id)
        if connection is None:
            raise not_found("Connection")
        if (
            connection.provider != "shopify" or connection.status != "connected" or connection.disconnected_at is not None
            or connection.account_confirmed_at is None or connection.last_checked_at is None or not connection.credential_ref
        ):
            raise conflict("A provider-checked, owner-confirmed Shopify connection is required.")
        plan.stage = "STORE_CONNECTED"
        plan.branch_status = "WAITING_FOR_PROVIDER"
        plan.state = {**plan.state, "store_connection_id": connection.id, "next_required_action": "implement_and_verify_storefront_adapter"}
        plan.checklist = [{**item, "status": "verified_connection" if item["id"] == "verify_store_connection" else item["status"]} for item in plan.checklist]
        session.add(LaunchEvidence(
            id=new_id("lev"), tenant_id=principal.tenant_id, plan_id=plan.id, stage=plan.stage, kind="store_connection",
            is_test=connection.environment != "production", source="shopify", detail={
                "connection_id": connection.id, "account_display": connection.account_display,
                "environment": connection.environment, "checked_at": connection.last_checked_at.astimezone(UTC).isoformat(),
                "payment_or_kyc_verified": False,
            },
        ))
        _touch(session, principal, plan, "store_bound", {"connection_id": connection.id})
        return _serialize(plan)


def cancel(principal: Principal, plan_id: str, expected_version: int) -> dict[str, Any]:
    principal.require("launch:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = _load(session, plan_id, lock=True)
        check_version(plan.version, expected_version)
        if plan.branch_status == "CANCELLED":
            return _serialize(plan)
        approval_id = plan.state.get("approval_id")
        approval = session.get(Approval, approval_id, with_for_update=True) if approval_id else None
        if approval and approval.status == "pending":
            approval.status = "cancelled"
            approval.version += 1
        plan.branch_status = "CANCELLED"
        plan.state = {**plan.state, "next_required_action": None}
        _touch(session, principal, plan, "cancelled")
        return {**_serialize(plan), "disclosure": "Plan work is cancelled. Completed actions are retained in the audit trail; provider accounts remain independently managed."}


def timeline(principal: Principal, plan_id: str) -> dict[str, Any]:
    principal.require("launch:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = _load(session, plan_id)
        rows = session.scalars(select(AuditEvent).where(AuditEvent.resource_type == "launch_plan", AuditEvent.resource_id == plan.id).order_by(AuditEvent.seq))
        return {"plan_id": plan.id, "events": [{"id": row.id, "event_type": row.event_type, "at": row.created_at.astimezone(UTC).isoformat(), "data": row.data} for row in rows]}


def evidence(principal: Principal, plan_id: str) -> dict[str, Any]:
    principal.require("launch:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        _load(session, plan_id)
        rows = session.scalars(select(LaunchEvidence).where(LaunchEvidence.plan_id == plan_id).order_by(LaunchEvidence.recorded_at))
        return {"plan_id": plan_id, "items": [{
            "id": row.id, "stage": row.stage, "kind": row.kind, "is_test": row.is_test,
            "source": row.source, "detail": row.detail, "receipt_id": row.receipt_id, "recorded_at": row.recorded_at.astimezone(UTC).isoformat(),
        } for row in rows], "payment_verified": False, "live_store_verified": False, "verified_fulfillments": 0}
