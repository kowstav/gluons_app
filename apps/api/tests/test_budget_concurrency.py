"""Budget holds and the usage ledger under real PostgreSQL concurrency.

The model provider is a labelled HTTP double: these tests prove Gluons' accounting and locking
and say nothing about any real provider's behaviour or prices.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import ROUND_UP, Decimal

import httpx
import pytest
from sqlalchemy import func, select, update

from gluons import billing, model_gateway
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import Automation, BudgetHold, ModelDescriptor, Organization, UsageEvent
from gluons.db.session import public_tx, tenant_tx
from gluons.models import providers
from gluons.runtime import lifecycle
from helpers import World
from test_model_gateway import Answer, configured, register, response  # noqa: F401 - pytest fixture and helpers

SYSTEM, USER = "Extract.", "Input"


def set_org_budget(world, amount) -> None:
    with tenant_tx(world.tenant_id) as session:
        session.execute(update(Organization).where(Organization.id == world.tenant_id).values(monthly_budget_usd=amount))


def spent(world) -> tuple[Decimal, int]:
    with tenant_tx(world.tenant_id) as session:
        total, count = session.execute(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0), func.count()).select_from(UsageEvent)).one()
    return Decimal(total), count


def holds(world) -> Decimal:
    with tenant_tx(world.tenant_id) as session:
        return Decimal(session.execute(select(func.coalesce(func.sum(BudgetHold.amount_usd), 0))).scalar_one())


def one_call_reservation(model_id: str) -> Decimal:
    with public_tx() as session:
        model = session.execute(select(ModelDescriptor).where(ModelDescriptor.model_id == model_id)).scalar_one()
    size = providers.encoded_request_size(SYSTEM, USER, Answer.model_json_schema())
    return model_gateway.reservation(model, size, 2048)


def place(world, automation_id=None, amount="1.00") -> str:
    """Returns the outcome so a thread can report it instead of raising."""
    try:
        with tenant_tx(world.tenant_id) as session:
            billing.place_hold(session, world.tenant_id, automation_id, Decimal(amount))
        return "held"
    except GluonsError as error:
        return error.code


def test_twenty_threads_through_the_gateway_never_overshoot_a_five_call_budget(world, configured):  # noqa: F811
    purpose = new_id("purpose")
    model_id = register(purpose, price="2000")
    per_call = one_call_reservation(model_id)
    budget = (per_call * 5).quantize(Decimal("0.01"), rounding=ROUND_UP)
    assert per_call * 6 > budget, "the fixture must fit exactly five reservations"
    set_org_budget(world, budget)

    start = threading.Barrier(20)
    outcomes: list[str] = []
    lock = threading.Lock()
    refusals_done = threading.Event()
    provider_calls: list[float] = []
    held_during_calls: list[Decimal] = []

    def provider_double(request):
        with lock:
            provider_calls.append(1.0)
        # Keep admitted calls in flight until every refused thread has been turned away.
        assert refusals_done.wait(timeout=60)
        return httpx.Response(200, json=response("openai"))

    def call(_):
        start.wait(timeout=30)
        try:
            model_gateway.complete_structured(tenant_id=world.tenant_id, purpose=purpose, schema=Answer, system=SYSTEM, user=USER,
                                              transport=httpx.MockTransport(provider_double))
            outcome = "completed"
        except GluonsError as error:
            outcome = error.code
        with lock:
            outcomes.append(outcome)
            if outcomes.count("BUDGET_EXCEEDED") == 15:
                held_during_calls.append(holds(world))
                refusals_done.set()
        return outcome

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(call, range(20)))

    assert results.count("completed") == 5 and results.count("BUDGET_EXCEEDED") == 15
    assert len(provider_calls) == 5, "a refused call must never reach the provider"
    assert held_during_calls[0] <= budget
    total, count = spent(world)
    assert count == 5 and total <= budget
    assert holds(world) == 0


@pytest.mark.parametrize("scope", ["organization", "automation"])
def test_twenty_concurrent_holds_admit_exactly_what_fits(world, scope):
    automation_id = None
    if scope == "organization":
        set_org_budget(world, Decimal("5.00"))
    else:
        automation_id = lifecycle.create(world.owner, skill_id="property_maintenance", monthly_budget_usd=Decimal("5.00"))["id"]
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: place(world, automation_id), range(20)))
    assert results.count("held") == 5 and results.count("BUDGET_EXCEEDED") == 15
    assert holds(world) == Decimal("5")


def test_automation_budget_and_organization_budget_both_apply(world):
    set_org_budget(world, Decimal("3.00"))
    automation_id = lifecycle.create(world.owner, skill_id="property_maintenance", monthly_budget_usd=Decimal("10.00"))["id"]
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: place(world, automation_id), range(10)))
    assert results.count("held") == 3


def test_budget_held_for_one_business_is_not_visible_to_another(world):
    other = World.create("Other")
    set_org_budget(world, Decimal("1.00"))
    set_org_budget(other, Decimal("1.00"))
    assert place(world) == "held" and place(other) == "held"
    assert place(world) == "BUDGET_EXCEEDED"


def test_one_usage_event_replayed_ten_thousand_times_bills_once(world):
    def deliver(batch: int) -> int:
        added = 0
        with tenant_tx(world.tenant_id) as session:
            for _ in range(2500):
                added += billing.record_usage(session, tenant_id=world.tenant_id, dedupe_key="provider-event-1", category="model", provider="openai",
                                              meter="inference", quantity=10, cost_usd=Decimal("0.25"), cost_basis="token_rate_estimate")
        return added

    with ThreadPoolExecutor(max_workers=4) as pool:
        inserted = sum(pool.map(deliver, range(4)))
    assert inserted == 1 and spent(world) == (Decimal("0.25"), 1)


def test_ten_thousand_distinct_events_replayed_as_a_batch_bill_once_each(world):
    keys = [f"evt-{index}" for index in range(10_000)]

    def write(batch: list[str]) -> int:
        added = 0
        with tenant_tx(world.tenant_id) as session:
            for key in batch:
                added += billing.record_usage(session, tenant_id=world.tenant_id, dedupe_key=key, category="model", provider="openai",
                                              meter="inference", quantity=1, cost_usd=Decimal("0.001"), cost_basis="token_rate_estimate")
        return added

    batches = [keys[start:start + 500] for start in range(0, len(keys), 500)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(write, batches)) == 10_000
    assert spent(world) == (Decimal("10.000000"), 10_000)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(write, batches)) == 0
    assert spent(world) == (Decimal("10.000000"), 10_000)


def test_expired_holds_free_the_budget_but_live_ones_do_not(world):
    set_org_budget(world, Decimal("2.00"))
    assert place(world) == "held" and place(world) == "held"
    assert place(world) == "BUDGET_EXCEEDED"
    with tenant_tx(world.tenant_id) as session:
        oldest = session.execute(select(BudgetHold).order_by(BudgetHold.created_at).limit(1)).scalar_one()
        oldest.expires_at = utcnow() - timedelta(seconds=1)
    assert place(world) == "held"
    assert place(world) == "BUDGET_EXCEEDED"


def test_crashed_call_loses_only_its_hold_window(world, configured):  # noqa: F811
    """A worker dying mid-call leaves a hold behind, which expires instead of leaking forever."""
    set_org_budget(world, Decimal("1.00"))
    with tenant_tx(world.tenant_id) as session:
        hold_id = billing.place_hold(session, world.tenant_id, None, Decimal("1.00"))
    assert place(world, amount="0.50") == "BUDGET_EXCEEDED"
    with tenant_tx(world.tenant_id) as session:
        session.execute(update(BudgetHold).where(BudgetHold.id == hold_id).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert place(world, amount="0.50") == "held"


def test_spent_usage_counts_against_the_budget_alongside_holds(world):
    set_org_budget(world, Decimal("1.00"))
    with tenant_tx(world.tenant_id) as session:
        billing.record_usage(session, tenant_id=world.tenant_id, dedupe_key="spent", category="model", provider="openai", meter="inference",
                             quantity=1, cost_usd=Decimal("0.75"), cost_basis="token_rate_estimate")
    assert place(world, amount="0.50") == "BUDGET_EXCEEDED"
    assert place(world, amount="0.25") == "held"


def test_lowered_budget_applies_to_the_next_hold_only(world):
    automation = lifecycle.create(world.owner, skill_id="property_maintenance", monthly_budget_usd=Decimal("5.00"))
    assert place(world, automation["id"], "3.00") == "held"
    with tenant_tx(world.tenant_id) as session:
        session.get(Automation, automation["id"]).monthly_budget_usd = Decimal("3.00")
    assert place(world, automation["id"], "0.01") == "BUDGET_EXCEEDED"
    assert holds(world) == Decimal("3")


def test_provider_double_failure_releases_the_hold_and_keeps_a_conservative_charge(world, configured):  # noqa: F811
    purpose = new_id("purpose")
    register(purpose)
    def timeout(request):
        raise httpx.ReadTimeout("provider-double timeout", request=request)
    with pytest.raises(GluonsError):
        model_gateway.complete_structured(tenant_id=world.tenant_id, purpose=purpose, schema=Answer, system=SYSTEM, user=USER,
                                          transport=httpx.MockTransport(timeout))
    assert holds(world) == 0
    with tenant_tx(world.tenant_id) as session:
        row = session.execute(select(UsageEvent)).scalar_one()
    assert row.cost_basis == "estimated" and row.detail["status"] == "requires_provider_reconciliation"
    assert json.dumps(row.detail).count("provider-double timeout") == 0
