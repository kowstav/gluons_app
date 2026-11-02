"""The full path: event -> extraction -> approval -> real HTTP write -> independent readback."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from gluons import approvals
from gluons.db.models import ActionReceipt, Approval, Order, Run
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle
from helpers import drain, order_event


def run_row(world, run_id: str) -> Run:
    with tenant_tx(world.tenant_id) as session:
        return session.get(Run, run_id)


def pending_approval(world, run_id: str) -> Approval:
    with tenant_tx(world.tenant_id) as session:
        return session.execute(select(Approval).where(Approval.run_id == run_id, Approval.status == "pending")).scalar_one()


def receipts(world) -> list[ActionReceipt]:
    with tenant_tx(world.tenant_id) as session:
        return list(session.execute(select(ActionReceipt)).scalars())


def approve(world, approval_id: str) -> None:
    with tenant_tx(world.tenant_id, world.user_id) as session:
        approvals.decide(session, world.owner, approval_id, "approve")


def test_supervised_order_writes_once_and_is_verified_by_readback(world, erp, sandbox_erp):
    world.connect_erp(sandbox_erp)
    automation = world.supervised_automation()

    run = order_event(world, automation["id"])
    drain()
    assert run_row(world, run["run_id"]).status == "waiting_approval"
    assert erp.orders() == [], "nothing may be written before a person approves"

    approval = pending_approval(world, run["run_id"])
    assert approval.action == "erp.order.create"
    assert approval.records_after_preview["items"][0]["sku"] == "FISH-1"
    approve(world, approval.id)
    drain()

    finished = run_row(world, run["run_id"])
    assert finished.status == "completed", finished.error
    written = erp.orders()
    assert len(written) == 1
    assert {i["item_code"] for i in written[0]["items"]} == {"FISH-1", "RICE-2"}

    receipt = next(r for r in receipts(world) if r.action == "erp.order.create")
    assert receipt.verified is True
    assert receipt.environment == "simulator"
    assert receipt.provider_object_id == written[0]["name"]
    assert receipt.readback["name"] == written[0]["name"]
    with tenant_tx(world.tenant_id) as session:
        order = session.execute(select(Order)).scalar_one()
    assert order.status == "confirmed" and order.external_order_id == written[0]["name"]


def test_shadow_makes_no_external_write_and_needs_no_grant(world, erp, sandbox_erp):
    world.connect_erp(sandbox_erp)
    automation = world.order_automation()
    lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"])

    run = order_event(world, automation["id"])
    drain()

    finished = run_row(world, run["run_id"])
    assert finished.status == "completed", finished.error
    assert finished.outcome["simulated"] is True
    assert finished.outcome["outcome"] == "shadow_would_create_order"
    assert erp.orders() == []
    assert all(r.status == "preview" and not r.verified and r.provider_object_id is None for r in receipts(world))


def test_duplicate_delivery_creates_one_run_and_one_write(world, erp, sandbox_erp):
    world.connect_erp(sandbox_erp)
    automation = world.supervised_automation()
    results = [order_event(world, automation["id"], "mail-dup") for _ in range(10)]
    assert sum(1 for r in results if not r["duplicate"]) == 1
    assert len({r["run_id"] for r in results}) == 1
    drain()
    approve(world, pending_approval(world, results[0]["run_id"]).id)
    drain()
    assert len(erp.orders()) == 1


def test_ambiguous_item_goes_to_review_and_never_guesses(world, erp, sandbox_erp):
    world.connect_erp(sandbox_erp)
    automation = world.supervised_automation()
    run = order_event(world, automation["id"], "mail-amb", "3 oil")
    drain()
    approval = pending_approval(world, run["run_id"])
    assert approval.kind == "review"
    assert "oil" in approval.summary
    assert erp.orders() == []


def test_lost_response_is_recovered_by_lookup_not_a_second_write(world, erp, sandbox_erp):
    world.connect_erp(sandbox_erp)
    automation = world.supervised_automation()
    run = order_event(world, automation["id"], "mail-lost")
    drain()
    erp.fault(drop_response_next=True)
    approve(world, pending_approval(world, run["run_id"]).id)
    drain()
    # The step is waiting out its backoff; force the retry to run now.
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Run, run["run_id"]).status == "retrying"
    from gluons.runtime import engine

    engine.advance(world.tenant_id, run["run_id"], "retry-worker")
    assert run_row(world, run["run_id"]).status == "completed"
    assert len(erp.orders()) == 1, "the retry must find the first write"
    receipt = next(r for r in receipts(world) if r.action == "erp.order.create")
    assert receipt.verified is True and receipt.attempts == 2


def test_altered_write_is_caught_by_readback(world, erp, sandbox_erp):
    world.connect_erp(sandbox_erp)
    automation = world.supervised_automation()
    run = order_event(world, automation["id"], "mail-alter")
    drain()
    erp.fault(alter_next=True)
    approve(world, pending_approval(world, run["run_id"]).id)
    drain()
    assert run_row(world, run["run_id"]).status == "reconciliation_required"
    receipt = next(r for r in receipts(world) if r.action == "erp.order.create")
    assert receipt.status == "mismatch" and receipt.verified is False


def test_denied_approval_writes_nothing(world, erp, sandbox_erp):
    world.connect_erp(sandbox_erp)
    automation = world.supervised_automation()
    run = order_event(world, automation["id"], "mail-deny")
    drain()
    with tenant_tx(world.tenant_id, world.user_id) as session:
        approvals.decide(session, world.owner, pending_approval(world, run["run_id"]).id, "deny", reason="wrong price")
    drain()
    assert erp.orders() == []
    assert run_row(world, run["run_id"]).status == "completed"
    assert run_row(world, run["run_id"]).outcome["outcome"] == "escalated_to_human"


@pytest.mark.parametrize("control", ["pause", "cancel"])
def test_pause_and_cancel_stop_a_waiting_run(world, erp, sandbox_erp, control):
    world.connect_erp(sandbox_erp)
    automation = world.supervised_automation()
    run = order_event(world, automation["id"], f"mail-{control}")
    drain()
    approval = pending_approval(world, run["run_id"])
    current = lifecycle.get(world.owner, automation["id"])
    getattr(lifecycle, control)(world.owner, automation["id"], current["version"])
    if control == "pause":
        approve(world, approval.id)
    drain()
    assert erp.orders() == []
    assert run_row(world, run["run_id"]).status == ("paused" if control == "pause" else "cancelled")
