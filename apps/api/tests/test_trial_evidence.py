"""Trial evidence through real runs, the worker and PostgreSQL. No provider is involved."""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from gluons import billing, trials
from gluons.api.app import create_app
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import ActionReceipt, Trial
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle, worker
from helpers import World, drain, order_event
from test_api_foundation import signed_in_org

CRITERIA = {"min_cases": 2, "min_accuracy": 1.0}
DRAFT = {"outcome": "draft_prepared_only", "simulated": True}


def code_of(fn, *args, **kwargs) -> str:
    with pytest.raises(GluonsError) as error:
        fn(*args, **kwargs)
    return error.value.code


def draft_trial(world, *, comparator=None, baseline=None, skill="property_maintenance"):
    automation = lifecycle.create(world.owner, skill_id=skill)
    started = lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"], comparator=comparator, baseline=baseline)
    return automation["id"], started["trial_id"]


def event(world, automation_id, event_id):
    return lifecycle.submit_event(world.tenant_id, automation_id, source="fixture", source_event_id=event_id,
                                  payload={"facts": {}, "source_evidence": ["fixture:case"]})


def passing(world, **extra):
    comparator = {"pass_criteria": CRITERIA, "expected_outcomes": {"a": DRAFT, "b": DRAFT}}
    automation_id, trial_id = draft_trial(world, comparator=comparator, **extra)
    event(world, automation_id, "a")
    event(world, automation_id, "b")
    worker.drain()
    return automation_id, trial_id


def test_explicit_criteria_with_full_labels_pass_and_report_denominators(world):
    _, trial_id = passing(world)
    report = trials.complete(world.owner, trial_id)
    assert report["passed"] is True and report["missing_evidence"] == [] and report["status"] == "completed"
    assert report["pass_criteria"]["min_accuracy"] == 1.0
    assert report["denominators"] == {"runs": 2, "completed_runs": 2, "labelled_expected": 2, "labelled_evaluated": 2,
                                      "accuracy_denominator": 2, "failure_rate_denominator": 2}
    assert report["ground_truth"]["available"] is True and report["ground_truth"]["coverage_of_completed_runs"] == 1.0
    assert report["errors"]["failed_runs"] == 0 and report["errors"]["mismatched_cases"] == []


@pytest.mark.parametrize("comparator", [{}, {"expected_outcomes": {"a": DRAFT, "b": DRAFT}}, {"minimum_cases": 1, "expected_outcomes": {"a": DRAFT, "b": DRAFT}},
                                        {"pass_criteria": {"min_cases": 2}, "expected_outcomes": {"a": DRAFT, "b": DRAFT}},
                                        {"pass_criteria": {"min_accuracy": 0.9}, "expected_outcomes": {"a": DRAFT, "b": DRAFT}},
                                        {"pass_criteria": {"min_cases": 0, "min_accuracy": 0.9}, "expected_outcomes": {"a": DRAFT, "b": DRAFT}},
                                        {"pass_criteria": "looks good", "expected_outcomes": {"a": DRAFT, "b": DRAFT}}])
def test_no_explicit_valid_criteria_means_no_pass(world, comparator):
    automation_id, trial_id = draft_trial(world, comparator=comparator)
    event(world, automation_id, "a")
    event(world, automation_id, "b")
    worker.drain()
    report = trials.complete(world.owner, trial_id)
    assert report["passed"] is False and report["pass_criteria"] is None
    assert any("pass_criteria" in line for line in report["missing_evidence"])


def test_zero_runs_can_never_pass_even_with_criteria(world):
    _, trial_id = draft_trial(world, comparator={"pass_criteria": {"min_cases": 1, "min_accuracy": 0.5}, "expected_outcomes": {"a": DRAFT}})
    report = trials.complete(world.owner, trial_id)
    assert report["passed"] is False and report["measured"]["runs"] == 0 and report["measured"]["accuracy"] is None
    assert report["external_write_check"]["verified_zero"] is False


def test_missing_ground_truth_can_never_pass(world):
    automation_id, trial_id = draft_trial(world, comparator={"pass_criteria": {"min_cases": 1, "min_accuracy": 0.5}})
    event(world, automation_id, "a")
    worker.drain()
    report = trials.complete(world.owner, trial_id)
    assert report["passed"] is False and report["ground_truth"]["available"] is False and report["measured"]["accuracy"] is None
    assert report["ground_truth"]["unlabelled_completed_runs"] == 1


def test_label_without_a_run_keeps_the_denominator_incomplete(world):
    comparator = {"pass_criteria": {"min_cases": 1, "min_accuracy": 0.5}, "expected_outcomes": {"a": DRAFT, "never-ran": DRAFT}}
    automation_id, trial_id = draft_trial(world, comparator=comparator)
    event(world, automation_id, "a")
    worker.drain()
    report = trials.complete(world.owner, trial_id)
    assert report["passed"] is False and report["ground_truth"]["labelled_without_completed_run"] == 1


def test_wrong_outcome_lowers_accuracy_and_is_listed_as_an_error(world):
    comparator = {"pass_criteria": {"min_cases": 2, "min_accuracy": 0.9}, "expected_outcomes": {"a": DRAFT, "b": {"outcome": "something_else"}}}
    automation_id, trial_id = draft_trial(world, comparator=comparator)
    event(world, automation_id, "a")
    event(world, automation_id, "b")
    worker.drain()
    report = trials.complete(world.owner, trial_id)
    assert report["measured"]["accuracy"] == 0.5 and report["passed"] is False
    assert report["errors"]["mismatched_cases"][0]["source_event_id"] == "b"


def test_empty_label_can_never_count_as_correct(world):
    comparator = {"pass_criteria": {"min_cases": 1, "min_accuracy": 0.5}, "expected_outcomes": {"a": {}}}
    automation_id, trial_id = draft_trial(world, comparator=comparator)
    event(world, automation_id, "a")
    worker.drain()
    assert trials.complete(world.owner, trial_id)["measured"]["correct_cases"] == 0


def test_shadow_zero_writes_is_checked_from_real_receipts(world, erp):
    world.connect_erp(erp.base)
    automation = world.order_automation()
    started = lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"])
    order_event(world, automation["id"])
    drain()
    report = trials.get_report(world.owner, started["trial_id"])
    check = report["external_write_check"]
    assert check["source"] == "action_receipts" and check["checked"] is True and check["receipts_examined"] >= 1
    assert check["external_writes"] == 0 and check["previews"] == check["receipts_examined"] and check["verified_zero"] is True
    assert erp.orders() == []


def test_a_write_in_shadow_is_reported_and_blocks_the_pass(world):
    automation_id, trial_id = passing(world)
    with tenant_tx(world.tenant_id) as session:
        session.add(ActionReceipt(id=new_id("rcp"), tenant_id=world.tenant_id, automation_id=automation_id, action="email.send", mode="shadow",
                                  idempotency_key="stray", payload_hash="0" * 64, status="succeeded", environment="production"))
    report = trials.complete(world.owner, trial_id)
    assert report["passed"] is False and report["measured"]["external_writes"] == 1
    assert report["external_write_check"]["verified_zero"] is False and report["external_write_check"]["write_receipt_ids"]


def test_actual_costs_are_split_by_basis_and_budget_is_enforced(world):
    automation = lifecycle.create(world.owner, skill_id="property_maintenance")
    comparator = {"pass_criteria": {"min_cases": 1, "min_accuracy": 1.0}, "expected_outcomes": {"a": DRAFT}}
    started = lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"], comparator=comparator, budget_usd=Decimal("1.00"))
    run = event(world, automation["id"], "a")
    worker.drain()
    with tenant_tx(world.tenant_id) as session:
        for key, cost, basis in (("x", "0.75", "provider_invoice"), ("y", "0.50", "token_rate_estimate")):
            billing.record_usage(session, tenant_id=world.tenant_id, dedupe_key=key, category="model", provider="openai", meter="inference",
                                 quantity=1, cost_usd=Decimal(cost), cost_basis=basis, automation_id=automation["id"], run_id=run["run_id"])
    report = trials.complete(world.owner, started["trial_id"])
    costs = report["external_costs"]
    assert costs["provider_reported_usd"] == "0.750000" and costs["estimated_usd"] == "0.500000" and costs["total_usd"] == "1.250000"
    assert costs["over_budget"] is True and report["passed"] is False


def test_corrections_and_manual_effort_are_reported_and_can_fail_a_trial(world):
    comparator = {"pass_criteria": {**CRITERIA, "max_corrections": 0}, "expected_outcomes": {"a": DRAFT, "b": DRAFT}}
    automation_id, trial_id = draft_trial(world, comparator=comparator)
    event(world, automation_id, "a")
    event(world, automation_id, "b")
    worker.drain()
    trials.record_measurement(world.owner, trial_id, metric="correction", value=1, unit="case", basis="measured", source="Reviewer log")
    trials.record_measurement(world.owner, trial_id, metric="manual_effort_minutes", value=12, unit="minutes", basis="measured", source="Stopwatch study")
    report = trials.complete(world.owner, trial_id)
    assert report["passed"] is False and report["corrections"]["count"] == "1"
    assert report["corrections"]["manual_effort_minutes"] == "12"
    assert any("correction" in line for line in report["missing_evidence"])


def test_measurement_bases_stay_apart(world):
    _, trial_id = passing(world)
    trials.record_measurement(world.owner, trial_id, metric="time_saved_minutes", value=30, unit="minutes", basis="measured", source="Stopwatch")
    trials.record_measurement(world.owner, trial_id, metric="time_saved_minutes", value=99, unit="minutes", basis="estimated", source="Owner guess")
    trials.record_measurement(world.owner, trial_id, metric="revenue_uplift", value=500, unit="usd", basis="unverified", source="Sales idea")
    assert code_of(trials.record_measurement, world.owner, trial_id, metric="x", value=1, unit="u", basis="vibes", source="s") == "VALIDATION_FAILED"
    report = trials.complete(world.owner, trial_id)
    assert [m["value"] for m in report["observed"]] == ["30"]
    assert [m["value"] for m in report["estimated"]["recorded"]] == ["99"]
    assert [m["metric"] for m in report["unverified"]] == ["revenue_uplift"]


def test_human_baseline_comparator_carries_method_denominators_and_label(world):
    baseline = {"method": "Timed 40 orders by hand in March", "cases": 40, "minutes_per_case": 6, "error_rate": 0.05,
                "cost_usd_per_case": 2.5, "hourly_cost_usd": 30}
    _, trial_id = passing(world, baseline=baseline)
    report = trials.complete(world.owner, trial_id)
    comparison = report["human_comparison"]
    assert comparison["available"] is True and comparison["basis"] == "owner_supplied_baseline_unverified"
    assert comparison["human"]["cases"] == 40 and comparison["automation"]["cases"] == 2
    assert comparison["difference"]["error_rate"] == -0.05 and comparison["difference"]["cost_usd_per_case"] == -2.5
    estimated = report["estimated"]
    assert estimated["time_saved_minutes"] == "12" and estimated["financial_savings_usd"] == "6.00" and estimated["denominator"] == 2
    assert estimated["method"]
    assert report["observed"] == []


def test_no_baseline_means_no_comparison_or_projection(world):
    _, trial_id = passing(world)
    report = trials.complete(world.owner, trial_id)
    assert report["human_comparison"]["available"] is False
    assert report["estimated"]["time_saved_minutes"] is None and report["estimated"]["financial_savings_usd"] is None


def test_criteria_and_baseline_lock_when_the_first_run_exists(world):
    automation_id, trial_id = draft_trial(world)
    configured = trials.configure(world.owner, trial_id, pass_criteria=CRITERIA, baseline={"method": "Timed", "cases": 5}, expires_in_days=7)
    assert configured["pass_criteria"]["min_cases"] == 2 and configured["expires_at"] is not None
    assert code_of(trials.configure, world.owner, trial_id, pass_criteria={"min_cases": 0, "min_accuracy": 2}) == "VALIDATION_FAILED"
    event(world, automation_id, "a")
    worker.drain()
    assert code_of(trials.configure, world.owner, trial_id, pass_criteria={"min_cases": 1, "min_accuracy": 0.1}) == "CONFLICT"
    assert code_of(trials.configure, world.owner, trial_id, baseline={"method": "Other", "cases": 1}) == "CONFLICT"
    labelled = trials.configure(world.owner, trial_id, expected_outcomes={"a": DRAFT})
    assert labelled["ground_truth"]["labelled_cases"] == 1


@pytest.mark.parametrize("baseline", [{}, {"method": ""}, {"method": "x"}, {"method": "x", "cases": 0}, {"method": "x", "cases": 3, "error_rate": 2},
                                      {"method": "x", "cases": 3, "minutes_per_case": -1}])
def test_unusable_baselines_are_refused(world, baseline):
    _, trial_id = draft_trial(world)
    assert code_of(trials.configure, world.owner, trial_id, baseline=baseline) == "VALIDATION_FAILED"


def test_recommendation_is_gradual_and_authorizes_nothing(world, erp):
    world.connect_erp(erp.base)
    _, draft_id = passing(world)
    assert trials.complete(world.owner, draft_id)["activation_recommendation"]["next_mode"] == "none"
    automation = world.order_automation()
    started = lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"])
    report = trials.get_report(world.owner, started["trial_id"])
    assert report["activation_recommendation"]["next_mode"] == "stay_in_trial"
    assert report["activation_recommendation"]["authorizes_nothing"] is True
    with tenant_tx(world.tenant_id) as session:
        session.get(Trial, started["trial_id"]).comparator = {"pass_criteria": {"min_cases": 1, "min_accuracy": 1.0}, "expected_outcomes": {"mail-1": {"simulated": True}}}
    order_event(world, automation["id"])
    drain()
    passed = trials.complete(world.owner, started["trial_id"])
    assert passed["passed"] is True, passed["missing_evidence"]
    assert passed["activation_recommendation"]["next_mode"] == "supervised"
    assert lifecycle.get(world.owner, automation["id"])["mode"] == "shadow"


def test_supervised_trial_requires_a_supervised_automation_and_checks_verified_writes(world, erp):
    world.connect_erp(erp.base)
    idle = world.order_automation()
    assert code_of(trials.start_supervised, world.owner, idle["id"], idle["version"]) == "CONFLICT"
    automation = world.supervised_automation()
    comparator = {"pass_criteria": {"min_cases": 1, "min_accuracy": 1.0}, "expected_outcomes": {"mail-1": {"mode": "supervised"}}}
    started = trials.start_supervised(world.owner, automation["id"], automation["version"], comparator=comparator)
    order_event(world, automation["id"])
    drain()
    from sqlalchemy import select

    from gluons import approvals
    from gluons.db.models import Approval

    with tenant_tx(world.tenant_id, world.user_id) as session:
        for approval in session.execute(select(Approval).where(Approval.status == "pending")).scalars().all():
            approvals.decide(session, world.owner, approval.id, "approve")
    drain()
    report = trials.complete(world.owner, started["trial_id"])
    check = report["external_write_check"]
    assert check["mode_allows_writes"] is True and check["external_writes"] == 1 and check["verified_writes"] == 1
    assert check["unverified_writes"] == 0 and report["passed"] is True, report["missing_evidence"]
    assert report["activation_recommendation"]["next_mode"] == "limited_live"
    assert len(erp.orders()) == 1


def test_expired_trial_cannot_pass_and_ignores_later_runs(world):
    automation_id, trial_id = passing(world)
    with tenant_tx(world.tenant_id) as session:
        session.execute(update(Trial).where(Trial.id == trial_id).values(expires_at=utcnow()))
    assert trials.get_report(world.owner, trial_id)["expired"] is True
    event(world, automation_id, "late")
    worker.drain()
    assert trials.expire_due(world.tenant_id) == [trial_id]
    report = trials.get_report(world.owner, trial_id)
    assert report["status"] == "expired" and report["passed"] is False and report["measured"]["runs"] == 2
    assert lifecycle.get(world.owner, automation_id)["status"] == "awaiting_activation"
    assert trials.expire_due(world.tenant_id) == []
    assert code_of(trials.record_measurement, world.owner, trial_id, metric="x", value=1, unit="u", basis="measured", source="s") == "CONFLICT"


def test_completing_an_expired_trial_yields_a_failed_report(world):
    _, trial_id = passing(world)
    with tenant_tx(world.tenant_id) as session:
        session.execute(update(Trial).where(Trial.id == trial_id).values(expires_at=utcnow()))
    report = trials.complete(world.owner, trial_id)
    assert report["passed"] is False and any("expired" in line for line in report["missing_evidence"])


def test_failing_trial_does_not_unlock_live_and_a_passing_one_can(world, erp):
    world.connect_erp(erp.base)
    automation = world.order_automation()
    world.grant(automation["id"], "live")
    current = lifecycle.get(world.owner, automation["id"])
    with tenant_tx(world.tenant_id, world.user_id) as session:
        from gluons.db.models import Automation

        session.get(Automation, automation["id"]).monthly_budget_usd = Decimal("10")
        missing = lifecycle.readiness(session, session.get(Automation, automation["id"]), "live")
    assert {m["kind"] for m in missing} == {"trial"}
    assert code_of(lifecycle.activate, world.owner, automation["id"], "live", current["version"]) == "ACTIVATION_BLOCKED"
    started = lifecycle.start_trial(world.owner, automation["id"], "shadow", current["version"],
                                    comparator={"pass_criteria": {"min_cases": 1, "min_accuracy": 1.0}, "expected_outcomes": {"mail-1": {"simulated": True}}})
    order_event(world, automation["id"])
    drain()
    assert trials.complete(world.owner, started["trial_id"])["passed"] is True
    live = lifecycle.get(world.owner, automation["id"])
    assert lifecycle.activate(world.owner, automation["id"], "live", live["version"])["status"] == "active"


def test_trial_results_are_tenant_isolated(world):
    _, trial_id = passing(world)
    other = World.create("Other")
    assert code_of(trials.get_report, other.owner, trial_id) == "NOT_FOUND"
    assert code_of(trials.configure, other.owner, trial_id, expected_outcomes={}) == "NOT_FOUND"
    assert code_of(trials.record_measurement, other.owner, trial_id, metric="x", value=1, unit="u", basis="measured", source="s") == "NOT_FOUND"


def test_trial_routes_over_http(world):
    automation_id, trial_id = draft_trial(world)
    client = TestClient(create_app())
    headers = signed_in_org(client, world)
    assert client.patch(f"/v1/trials/{trial_id}", json={"pass_criteria": CRITERIA, "expected_outcomes": {"a": DRAFT}}, headers=headers).status_code == 200
    measurement = client.post(f"/v1/trials/{trial_id}/measurements", headers=headers,
                              json={"metric": "correction", "value": 0, "unit": "case", "basis": "measured", "source": "Reviewer log"})
    assert measurement.status_code == 201
    assert client.get(f"/v1/trials/{trial_id}/results").json()["passed"] is False
    assert [t["id"] for t in client.get("/v1/trials").json()["trials"]] == [trial_id]
