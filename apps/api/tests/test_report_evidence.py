"""ROI, operational reports and anomaly alerts from real rows. Empty data stays empty."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gluons import analytics_jobs, billing, reports, trials
from gluons.api.app import create_app
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import Automation, Incident, Job, Notification, Run, Trial, UsageEvent
from gluons.db.session import system_tx, tenant_tx
from gluons.runtime import lifecycle, worker
from helpers import World
from test_api_foundation import signed_in_org
from test_trial_evidence import passing


def spend(world, amount: str, *, key: str | None = None, run_id=None, automation_id=None, category="model", at=None):
    with tenant_tx(world.tenant_id) as session:
        if at is None:
            billing.record_usage(session, tenant_id=world.tenant_id, dedupe_key=key or new_id("use"), category=category, provider="openai",
                                 meter="inference", quantity=100, cost_usd=Decimal(amount), cost_basis="token_rate_estimate",
                                 run_id=run_id, automation_id=automation_id)
        else:  # The ledger is append-only, so history is inserted with its time rather than moved.
            session.add(UsageEvent(id=new_id("use"), tenant_id=world.tenant_id, dedupe_key=new_id("old"), category=category, provider="openai",
                                   meter="inference", quantity=100, cost_usd=Decimal(amount), cost_basis="token_rate_estimate", occurred_at=at))


def failed_run(world, automation_id, index):
    with tenant_tx(world.tenant_id) as session:
        session.add(Run(id=new_id("run"), tenant_id=world.tenant_id, automation_id=automation_id, skill_id="property_maintenance", skill_version="1.0.0",
                        mode="shadow", status="failed", source="fixture", source_event_id=f"failed-{index}"))


def test_empty_business_returns_empty_results_not_placeholders(world):
    report = reports.roi(world.owner)
    assert report["measured"]["usage_costs_usd_by_basis"] == {} and report["measured"]["metrics"] == []
    assert report["measured"]["time_saved_minutes"] is None and report["measured"]["denominator"] == 0 and report["measured"]["method"] is None
    assert report["estimated"] == {"time_saved_minutes": None, "financial_savings_usd": None, "roi_percent": None, "method": None, "denominator": 0, "trials_used": []}
    assert report["unverified"]["opportunities"] == [] and report["recorded_estimates"] == []
    assert len(report["missing_evidence"]) == 2
    operational = reports.operational(world.owner)
    assert operational["denominators"] == {"runs": 0, "verified_actions": 0} and operational["failure_rate"]["rate"] is None
    assert reports.detect_anomalies(world.tenant_id) == []


def test_roi_keeps_measured_estimated_and_unverified_strictly_separate(world):
    baseline = {"method": "Timed 40 orders by hand", "cases": 40, "minutes_per_case": 6, "hourly_cost_usd": 30}
    automation_id, trial_id = passing(world, baseline=baseline)
    trials.record_measurement(world.owner, trial_id, metric="time_saved_minutes", value=9, unit="minutes", basis="measured", source="Stopwatch study")
    trials.record_measurement(world.owner, trial_id, metric="time_saved_minutes", value=500, unit="minutes", basis="estimated", source="Owner guess")
    trials.record_measurement(world.owner, trial_id, metric="missed_call_revenue", value=9000, unit="usd", basis="unverified", source="Sales idea")
    trials.complete(world.owner, trial_id)
    spend(world, "2.00", automation_id=automation_id)
    report = reports.roi(world.owner, automation_id)

    assert report["measured"]["time_saved_minutes"] == "9" and report["measured"]["denominator"] == 1
    assert report["measured"]["method"] and report["measured"]["metrics"][0]["sources"] == ["Stopwatch study"]
    assert report["measured"]["usage_costs_usd_by_basis"] == {"token_rate_estimate": "2.000000"}

    estimated = report["estimated"]
    assert estimated["time_saved_minutes"] == "12" and estimated["financial_savings_usd"] == "6.00"
    assert estimated["denominator"] == 2 and estimated["denominator_unit"] == "correct labelled trial cases"
    assert estimated["roi_percent"] == "200.00" and estimated["method"] and estimated["trials_used"] == [trial_id]

    assert report["recorded_estimates"][0]["value"] == "500"
    assert report["unverified"]["opportunities"][0]["metric"] == "missed_call_revenue"
    assert "9000" not in str(report["measured"]) + str(report["estimated"])
    assert report["missing_evidence"] == []


def test_roi_never_counts_an_unfinished_trial(world):
    automation_id, trial_id = passing(world, baseline={"method": "Timed", "cases": 3, "minutes_per_case": 5})
    trials.record_measurement(world.owner, trial_id, metric="time_saved_minutes", value=9, unit="minutes", basis="measured", source="Study")
    report = reports.roi(world.owner, automation_id)
    assert report["measured"]["time_saved_minutes"] is None and report["estimated"]["time_saved_minutes"] is None


def test_roi_without_usage_cost_gives_no_percentage(world):
    automation_id, trial_id = passing(world, baseline={"method": "Timed", "cases": 3, "minutes_per_case": 5, "hourly_cost_usd": 20})
    trials.complete(world.owner, trial_id)
    assert reports.roi(world.owner, automation_id)["estimated"]["roi_percent"] is None


def test_roi_is_tenant_scoped(world):
    automation_id, trial_id = passing(world)
    trials.complete(world.owner, trial_id)
    other = World.create("Other")
    with pytest.raises(GluonsError) as error:
        reports.roi(other.owner, automation_id)
    assert error.value.code == "NOT_FOUND"
    assert reports.roi(other.owner)["estimated"]["trials_used"] == []


def test_operational_report_carries_denominators(world):
    automation_id, _ = passing(world)
    failed_run(world, automation_id, 1)
    report = reports.operational(world.owner)
    assert report["denominators"]["runs"] == 3
    assert report["failure_rate"] == {"failed_runs": 1, "of_runs": 3, "rate": 0.333333}


def test_spend_spike_compares_with_history_and_alerts_once(world):
    for _ in range(7):
        spend(world, "1.00", at=utcnow() - timedelta(days=3))
    assert reports.detect_anomalies(world.tenant_id) == []
    spend(world, "2.00")
    assert reports.detect_anomalies(world.tenant_id) == []  # twice the daily average is not a spike
    spend(world, "7.00")
    assert reports.detect_anomalies(world.tenant_id)[0]["kind"] == "anomaly_spend_spike"
    assert reports.detect_anomalies(world.tenant_id) == []
    with tenant_tx(world.tenant_id) as session:
        incident = session.execute(select(Incident).where(Incident.kind == "anomaly_spend_spike")).scalar_one()
        assert incident.diagnosis["basis"] == "measured_database_records" and incident.diagnosis["last_24h_usd"] == "9.000000"
        assert session.execute(select(Notification).where(Notification.kind == "anomaly_spend_spike")).first() is not None


def test_ordinary_spend_raises_no_alert(world):
    spend(world, "0.40")
    assert reports.detect_anomalies(world.tenant_id) == []


def test_first_day_spend_needs_a_higher_floor(world):
    spend(world, "24.00")
    assert reports.detect_anomalies(world.tenant_id) == []
    spend(world, "1.00")
    assert [a["kind"] for a in reports.detect_anomalies(world.tenant_id)] == ["anomaly_spend_spike"]


def test_token_loop_flags_the_exact_run(world):
    automation = lifecycle.create(world.owner, skill_id="property_maintenance")
    run_id = new_id("run")
    with tenant_tx(world.tenant_id) as session:
        session.add(Run(id=run_id, tenant_id=world.tenant_id, automation_id=automation["id"], skill_id="property_maintenance",
                        skill_version="1.0.0", mode="shadow", status="running", source="fixture", source_event_id="loop"))
    for _ in range(reports.TOKEN_LOOP_CALLS_PER_HOUR - 1):
        spend(world, "0.0001", run_id=run_id, automation_id=automation["id"])
    assert reports.detect_anomalies(world.tenant_id) == []
    spend(world, "0.0001", run_id=run_id, automation_id=automation["id"])
    found = reports.detect_anomalies(world.tenant_id)
    assert [a["kind"] for a in found] == ["anomaly_token_loop"] and found[0]["model_calls_last_hour"] == 25
    assert reports.list_anomalies(world.owner)[0]["run_id"] == run_id


def test_failed_events_need_both_a_count_and_a_share(world):
    automation = lifecycle.create(world.owner, skill_id="property_maintenance")
    for index in range(4):
        failed_run(world, automation["id"], index)
    assert reports.detect_anomalies(world.tenant_id) == []
    failed_run(world, automation["id"], 4)
    found = reports.detect_anomalies(world.tenant_id)
    assert found[0]["kind"] == "anomaly_failed_events" and found[0]["failed_runs"] == 5 and found[0]["of_runs"] == 5


def test_anomalies_never_cross_tenants(world):
    spend(world, "50.00")
    other = World.create("Other")
    assert reports.detect_anomalies(other.tenant_id) == []
    assert reports.detect_anomalies(world.tenant_id)
    assert reports.list_anomalies(other.owner) == []


def test_viewer_can_read_but_anonymous_cannot(world):
    client = TestClient(create_app())
    assert client.get("/v1/reports/anomalies").status_code == 401
    headers = signed_in_org(client, world)
    assert client.get("/v1/reports/anomalies").json() == {"anomalies": []}
    assert client.post("/v1/reports/anomalies/scan", headers=headers).json() == {"opened": [], "open": []}


def test_scheduler_runs_both_sweeps_through_the_real_queue(world):
    automation_id, trial_id = passing(world)
    spend(world, "80.00")
    with tenant_tx(world.tenant_id) as session:
        session.get(Trial, trial_id).expires_at = utcnow() - timedelta(minutes=1)
    assert {analytics_jobs.EXPIRE_TRIALS, analytics_jobs.SCAN_ANOMALIES} <= set(worker.PERIODIC)
    worker.scheduler_tick()
    worker.drain()
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Trial, trial_id).status == "expired"
        assert session.execute(select(Incident).where(Incident.kind == "anomaly_spend_spike")).first() is not None
        assert session.get(Automation, automation_id).status == "awaiting_activation"
    with system_tx() as session:
        assert session.execute(select(Job).where(Job.kind == analytics_jobs.SCAN_ANOMALIES)).first() is not None


def test_usage_ledger_stays_append_only_for_reports(world):
    spend(world, "1.00")
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), tenant_tx(world.tenant_id) as session:
        session.execute(UsageEvent.__table__.update().values(cost_usd=0))
