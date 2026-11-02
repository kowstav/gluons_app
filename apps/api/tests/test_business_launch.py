"""Persistent launch plans, signed owner handoffs and honest external blockers."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from gluons import approvals, audit, launch
from gluons.authz import Principal, Role
from gluons.core import GluonsError, utcnow
from gluons.db.models import ActionReceipt, Approval, Connection, LaunchPlan
from gluons.db.session import tenant_tx
from helpers import World


def make_plan(world):
    return launch.create_plan(world.owner, goal="Research a ceramics shop", country="in", product_category="Ceramics", budget_usd=Decimal("2000.00"), fulfillment_model="inventory")


def approve(world, approval_id):
    with tenant_tx(world.tenant_id, world.user_id) as session:
        return approvals.decide(session, world.owner, approval_id, "approve")


def test_plans_persist_and_do_not_fabricate_market_or_payment_results(world):
    plan = make_plan(world)
    assert plan["country"] == "IN" and plan["stage"] == "INTENT_CAPTURED"
    assert len(plan["checklist"]) == 14
    assert not plan["store_live"] and plan["payment_status"] == "unverified"
    started = launch.start(world.owner, plan["id"], plan["version"])
    assert started["branch_status"] == "WAITING_FOR_OWNER"
    fetched = launch.get_plan(world.owner, plan["id"])
    assert fetched == started
    assert launch.list_plans(world.owner)[0]["id"] == plan["id"]
    proof = launch.evidence(world.owner, plan["id"])
    assert proof["items"][0]["detail"]["market_research_performed"] is False
    assert not proof["payment_verified"] and proof["verified_fulfillments"] == 0
    with tenant_tx(world.tenant_id) as session:
        assert not session.scalars(select(ActionReceipt)).all()


def test_owner_approval_advances_to_real_external_handoff_only(world):
    plan = make_plan(world)
    started = launch.start(world.owner, plan["id"], plan["version"])
    approve(world, started["state"]["approval_id"])
    result = launch.resume(world.owner, plan["id"], started["version"])
    assert result["stage"] == "STORE_ONBOARDING_REQUIRED"
    assert result["branch_status"] == "WAITING_FOR_OWNER"
    assert result["payment_status"] == "unverified" and not result["store_live"]
    # Approval covers planning only and is single-use. Resume never manufactures
    # Shopify signup, supplier outreach, KYC, checkout or shipment confirmations.
    result = launch.resume(world.owner, plan["id"], result["version"])
    assert result["stage"] == "STORE_ONBOARDING_REQUIRED"
    with tenant_tx(world.tenant_id) as session:
        approval = session.get(Approval, started["state"]["approval_id"])
        assert approval.consumed_at is not None
        assert not session.scalars(select(ActionReceipt)).all()
        assert audit.verify_chain(session, world.tenant_id)["valid"]


def test_pause_cancel_stale_versions_and_approval_cleanup(world):
    plan = make_plan(world)
    started = launch.start(world.owner, plan["id"], plan["version"])
    paused = launch.pause(world.owner, plan["id"], started["version"])
    assert paused["branch_status"] == "PAUSED"
    with pytest.raises(GluonsError) as error:
        launch.resume(world.owner, plan["id"], started["version"])
    assert error.value.code == "CONFLICT_VERSION"
    cancelled = launch.cancel(world.owner, plan["id"], paused["version"])
    assert cancelled["branch_status"] == "CANCELLED"
    with pytest.raises(GluonsError, match="Start an uncancelled"):
        launch.resume(world.owner, plan["id"], cancelled["version"])
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Approval, started["state"]["approval_id"]).status == "cancelled"
    assert len(launch.timeline(world.owner, plan["id"])["events"]) == 4


def test_other_tenant_cannot_read_control_or_bind_launch(world):
    other = World.create("Other business")
    plan = make_plan(world)
    for operation in (launch.get_plan, launch.timeline, launch.evidence):
        with pytest.raises(GluonsError) as error:
            operation(other.owner, plan["id"])
        assert error.value.code == "NOT_FOUND"
    with pytest.raises(GluonsError) as error:
        launch.start(other.owner, plan["id"], plan["version"])
    assert error.value.code == "NOT_FOUND"
    assert not launch.list_plans(other.owner)


def test_viewer_cannot_create_launch_plan(world):
    viewer = Principal(kind="user", tenant_id=world.tenant_id, user_id=world.user_id, role=Role.VIEWER)
    with pytest.raises(GluonsError) as error:
        launch.create_plan(viewer, goal="Open a shop")
    assert error.value.code == "FORBIDDEN"


@pytest.mark.parametrize("budget", [-1, float("nan"), float("inf"), "bad"])
def test_invalid_budget_rejected_before_persisting(world, budget):
    with pytest.raises(GluonsError) as error:
        launch.create_plan(world.owner, goal="Open a shop", budget_usd=budget)
    assert error.value.code == "VALIDATION_FAILED"
    assert not launch.list_plans(world.owner)


def test_expired_approval_can_be_renewed_without_provider_side_effects(world):
    plan = make_plan(world)
    started = launch.start(world.owner, plan["id"], plan["version"])
    with tenant_tx(world.tenant_id) as session:
        approval = session.get(Approval, started["state"]["approval_id"])
        approval.expires_at = utcnow() - timedelta(seconds=1)
    resumed = launch.resume(world.owner, plan["id"], started["version"])
    assert resumed["branch_status"] == "NEEDS_REVIEW"
    renewed = launch.start(world.owner, plan["id"], resumed["version"])
    assert renewed["state"]["approval_id"] != started["state"]["approval_id"]
    assert renewed["branch_status"] == "WAITING_FOR_OWNER"


def test_approval_is_bound_to_plan_payload(world):
    plan = make_plan(world)
    started = launch.start(world.owner, plan["id"], plan["version"])
    approve(world, started["state"]["approval_id"])
    with tenant_tx(world.tenant_id) as session:
        row = session.get(LaunchPlan, plan["id"])
        row.budget_usd = 9999
    with pytest.raises(GluonsError) as error:
        launch.resume(world.owner, plan["id"], started["version"])
    assert error.value.code == "APPROVAL_PAYLOAD_MISMATCH"


def test_unverified_store_cannot_satisfy_kyc_or_store_handoff(world):
    plan = make_plan(world)
    started = launch.start(world.owner, plan["id"], plan["version"])
    approve(world, started["state"]["approval_id"])
    ready = launch.resume(world.owner, plan["id"], started["version"])
    with tenant_tx(world.tenant_id) as session:
        session.add(Connection(id="con_unverified_shop", tenant_id=world.tenant_id, provider="shopify", environment="sandbox", external_account_id="fixture.myshopify.com", account_display="Test store double", created_by=world.user_id))
    with pytest.raises(GluonsError, match="provider-checked"):
        launch.bind_store(world.owner, plan["id"], "con_unverified_shop", ready["version"])
    evidence = launch.evidence(world.owner, plan["id"])
    assert evidence["payment_verified"] is False and evidence["live_store_verified"] is False


def test_assistant_approval_scope_cannot_approve_business_launch(world):
    plan = make_plan(world)
    started = launch.start(world.owner, plan["id"], plan["version"])
    assistant = Principal(kind="assistant", tenant_id=world.tenant_id, user_id=world.user_id, role=Role.OWNER,
                          scopes=frozenset({"gluons.read", "gluons.plan", "gluons.approvals"}), approval_tier_limit="R3")
    with pytest.raises(GluonsError) as error, tenant_tx(world.tenant_id, world.user_id) as session:
        approvals.decide(session, assistant, started["state"]["approval_id"], "approve")
    assert error.value.code == "FORBIDDEN"
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Approval, started["state"]["approval_id"]).status == "pending"
