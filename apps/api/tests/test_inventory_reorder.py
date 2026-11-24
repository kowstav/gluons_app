"""The reusable reorder calculation and its pinned, provider-free runtime inputs."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from gluons import data_export
from gluons.db.models import Automation, Run, RunEvent
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle, worker
from gluons.skills.domain.reorder import SKILL_ID, ReorderRules, StockSnapshot, evaluate

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[3] / "packages" / "skills" / SKILL_ID / "evals.json").read_text(encoding="utf-8")
)
RULES = ReorderRules.model_validate(FIXTURES["rules"])


def snapshot(case) -> StockSnapshot:
    return StockSnapshot.model_validate({"stock_location": case.get("stock_location", RULES.stock_location),
                                         "observed_at": "2026-11-21T08:00:00Z", "basis": "synthetic", "counts": case["counts"]})


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda case: case["id"])
def test_reorder_reference_case(case):
    [line] = evaluate(RULES, snapshot(case))["rows"]
    assert line == {"sku": "BEANS", "supplier": "Supply Co", **case["expected"]}


def test_reorder_run_keeps_the_rules_captured_at_submission(world):
    automation = lifecycle.create(world.owner, skill_id=SKILL_ID, config={"inventory_rules": RULES.model_dump(mode="json")})
    lifecycle.start_trial(world.owner, automation["id"], "historical_replay", automation["version"])
    submitted = lifecycle.submit_event(world.tenant_id, automation["id"], source="fixture", source_event_id="inventory-snapshot",
                                       payload=snapshot(FIXTURES["cases"][0]).model_dump(mode="json"))
    with tenant_tx(world.tenant_id) as db:
        # A later edit must not change how an already accepted snapshot is evaluated.
        changed = RULES.model_copy(update={"items": [RULES.items[0].model_copy(update={"unit_cost_minor": 999})]})
        db.get(Automation, automation["id"]).config = {"inventory_rules": changed.model_dump(mode="json")}
    worker.drain()
    with tenant_tx(world.tenant_id) as db:
        run = db.get(Run, submitted["run_id"])
        assert run.mode == "historical_replay" and run.status == "completed"
        assert run.outcome["reorder_review"]["drafts"][0]["estimated_total_minor"] == 8100
        completed = db.execute(select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.event_type == "run.completed")).scalar_one()
        assert "reorder_review" not in completed.data

    owner = replace(world.owner, session_id="inventory-export-owner")
    [record] = data_export.export_page(owner, "runs")["records"]
    assert record["inventory_rules"]["items"][0]["unit_cost_minor"] == 450
    assert record["inventory_snapshot"]["basis"] == "synthetic"
    assert record["reorder_review"]["drafts"][0]["estimated_total_minor"] == 8100
    [current] = data_export.export_page(owner, "automations")["records"]
    assert current["inventory_rules"]["items"][0]["unit_cost_minor"] == 999
