"""New MCP tools, called by the official SDK client over a real socket, against real PostgreSQL.

These prove the tool contract and Gluons authorization. No Claude or ChatGPT host is involved,
so nothing here shows that a particular assistant product works.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import replace
from decimal import Decimal

import anyio
import httpx2
import pytest
import uvicorn
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from gluons import approvals, data_export, passport
from gluons.connections import google
from gluons.core import new_id
from gluons.db.models import Automation, AutomationPolicy, ConnectionSession, Notification, Organization, PhoneRoute, Run
from gluons.db.session import tenant_tx
from gluons.mcp.server import create_mcp_app
from gluons.mcp.server import create_server as create_mcp_server
from gluons.runtime import lifecycle
from helpers import World, drain
from test_assistant_oauth import issue

ALL_SCOPES = "gluons.read gluons.plan gluons.tasks gluons.connections gluons.approvals"
REQUIRED_TOOLS = {
    # doc 02
    "discover_automations", "inspect_automation", "analyze_business", "create_plan", "estimate_cost", "start_trial",
    "activate_automation", "get_task_status", "list_running_tasks", "set_task_policy", "approve_action", "pause_task",
    "resume_task", "cancel_task", "get_trial_report", "get_spending", "list_connections", "connect_service", "get_alerts",
    # doc 13
    "connections_list", "connection_begin", "connection_status", "connection_disconnect", "phone_route_begin",
    "phone_route_status", "policy_preview", "policy_request_activation", "policy_status",
    # product additions
    "list_missing_connections", "set_budget", "deny_action",
    "start_workflow_setup", "list_workflow_setups", "get_workflow_setup", "answer_workflow_setup",
    "prepare_workflow_gmail", "link_workflow_draft", "submit_order_trial_case",
    "configure_inventory_reorder", "submit_inventory_trial_case", "get_task_run",
}


@pytest.fixture(scope="module")
def mcp_url(database):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_mcp_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=10)


def payload(result):
    assert not result.is_error, result
    return result.structured_content or json.loads(result.content[0].text)


def error_code(result) -> str:
    assert result.is_error, result
    text = result.content[0].text
    return json.loads(text[text.index("{"):])["code"]


def session(url, world, *calls, scopes=ALL_SCOPES):
    """Runs (tool, arguments) pairs through one real client connection and returns raw results."""
    _, _, pair = issue(world, scopes=scopes)

    async def main():
        headers = {"Authorization": f"Bearer {pair['access_token']}"}
        async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                tools = await client.list_tools()
                results = [await client.call_tool(name, arguments) for name, arguments in calls]
                return tools, results

    return anyio.run(main)


def pending_approval(world, *, tier: str, automation_id: str | None = None, action: str = "erp.order.create"):
    with tenant_tx(world.tenant_id) as db:
        row = approvals.request_approval(db, tenant_id=world.tenant_id, action=action, risk_tier=tier, title=f"{tier} action",
                                         payload_hash=new_id("hash"), automation_id=automation_id, run_id=None)
        return row.id, row.version


def test_union_of_doc_02_and_doc_13_tools_is_served_and_none_accepts_a_tenant(mcp_url, world):
    tools, _ = session(mcp_url, world)
    names = {tool.name for tool in tools.tools}
    assert REQUIRED_TOOLS <= names, REQUIRED_TOOLS - names
    for tool in tools.tools:
        assert not {"tenant_id", "tenant", "organization_id"} & set(tool.input_schema.get("properties", {})), tool.name


def test_every_mcp_tool_declares_its_side_effect_label(database):
    for tool in anyio.run(create_mcp_server().list_tools):
        assert tool.annotations is not None and tool.annotations.read_only_hint is not None, tool.name


def test_tool_argument_cannot_change_the_tenant(mcp_url, world):
    other = World.create("Another Business")
    _, [created, listed] = session(mcp_url, world,
                                   ("create_automation", {"skill_id": "property_maintenance", "idempotency_key": "tenant-probe",
                                                          "tenant_id": other.tenant_id,
                                                          "config": {"tenant_id": other.tenant_id}}),
                                   ("list_running_tasks", {"tenant_id": other.tenant_id}))
    if not created.is_error:
        row = payload(created)
        assert row["tenant_id"] == world.tenant_id
    assert lifecycle.list_all(other.owner) == []
    assert all(row["tenant_id"] == world.tenant_id for row in payload(listed)["automations"])


def test_cross_tenant_ids_stay_invisible_to_new_tools(mcp_url, world):
    other = World.create("Another Business")
    foreign = other.order_automation()
    _, results = session(mcp_url, world,
                         ("policy_preview", {"automation_id": foreign["id"]}),
                         ("policy_status", {"automation_id": foreign["id"]}),
                         ("list_missing_connections", {"automation_id": foreign["id"]}),
                         ("set_budget", {"monthly_budget_usd": 1, "automation_id": foreign["id"], "idempotency_key": "foreign-budget"}))
    assert [error_code(r) for r in results] == ["NOT_FOUND"] * 4


def _start_workflow(mcp_url, world, intent):
    _, [result] = session(mcp_url, world, ("start_workflow_setup", {"intent": intent, "idempotency_key": f"setup-{intent}"}))
    return payload(result)


def _answers(setup, answers):
    return [("answer_workflow_setup", {"setup_id": setup["id"], "question_id": question, "answer": value,
                                        "expected_version": setup["version"] + position})
            for position, (question, value) in enumerate(answers)]


def test_launch_intake_reuses_confirmed_passport_and_links_draft_without_authority(mcp_url, world):
    owner = replace(world.owner, session_id="passport-owner")
    passport.update(owner, 1, {"selling_regions": ["IN"]})
    setup = _start_workflow(mcp_url, world, "start_business")
    assert setup["guidance"]["next_question"]["id"] == "business_idea"
    assert _start_workflow(mcp_url, world, "start_business")["id"] == setup["id"]
    answers = _answers(setup, (
        ("business_idea", "Sell office supplies to small businesses"),
        ("market", "IN"),
        ("product_decision", "assistant_recommends"),
        ("supplier_decision", "already_chosen"),
        ("supplier_name", "Existing Supply Co"),
        ("storefront_decision", "assistant_decides_within_rules"),
    ))
    _, results = session(mcp_url, world, *answers,
                         ("create_business_launch_plan", {"goal": "Sell office supplies", "country": "IN",
                                                           "idempotency_key": "office-launch-draft"}))
    states = [payload(result) for result in results]
    assert states[0]["guidance"]["next_question"]["suggested_answer"] == {"value": "IN", "source": "business_passport"}
    assert "market" not in states[0]["guidance"]["answers"]
    assert states[-2]["guidance"]["status"] == "ready_to_draft"
    draft = states[-1]
    _, [linked, listed] = session(mcp_url, world,
                                  ("link_workflow_draft", {"setup_id": setup["id"], "draft_id": draft["id"],
                                                           "expected_version": states[-2]["version"]}),
                                  ("list_workflow_setups", {}))
    linked = payload(linked)
    assert linked["guidance"]["workflow_draft"] == {"kind": "launch_plan", "id": draft["id"]}
    assert linked["guidance"]["authorization"] == "none"
    assert payload(listed)["sessions"][0]["workflow_artifact"]["id"] == draft["id"]
    exported = data_export.export_page(owner, "setup_sessions")
    assert "assistant_recommends" in str(exported)
    assert "link_hash" not in str(exported) and "idempotency_hash" not in str(exported)


def test_workflow_answers_check_scope_version_tenant_and_relevant_questions(mcp_url, world):
    setup = _start_workflow(mcp_url, world, "delegate_workflow")
    _, [foreign] = session(mcp_url, World.create("Different business"),
                           ("get_workflow_setup", {"setup_id": setup["id"]}))
    assert error_code(foreign) == "NOT_FOUND"
    call = _answers(setup, (("process", "other"),))[0]
    _, [read_only] = session(mcp_url, world, call, scopes="gluons.read")
    assert error_code(read_only) == "FORBIDDEN"
    _, [invalid, saved, duplicate, stale, secret] = session(
        mcp_url, world,
        _answers(setup, (("intake_channel", "gmail"),))[0], call, call,
        _answers(setup, (("process", "order_intake"),))[0],
        ("answer_workflow_setup", {"setup_id": setup["id"], "question_id": "process_example",
                                   "answer": "password: sample", "expected_version": 2}),
    )
    assert payload(saved) == payload(duplicate)
    assert [error_code(r) for r in (invalid, stale, secret)] == ["VALIDATION_FAILED", "CONFLICT_VERSION", "VALIDATION_FAILED"]
    _, results = session(mcp_url, world, *_answers(payload(saved), (
        ("process_example", "Stock falls below ten units"),
        ("system_of_record", "The warehouse"),
        ("routine_decision", "assistant_recommends"),
        ("exceptions", "Ask about price changes. Done when the supplier accepts."),
    )))
    assert payload(results[-1])["guidance"]["status"] == "planning_only"


def test_chat_gmail_handoff_is_scoped_and_resumable_without_provider_access(mcp_url, world, monkeypatch):
    monkeypatch.setattr(google, "configured", lambda: True)
    setup = _start_workflow(mcp_url, world, "delegate_workflow")
    _, results = session(mcp_url, world, *_answers(setup, (
        ("process", "order_intake"), ("intake_channel", "gmail"),
        ("process_example", "A customer sends a purchase order."),
        ("system_of_record", "ERPNext"),
        ("routine_decision", "assistant_recommends"),
        ("exceptions", "Review unknown items. Done after order readback."),
    )))
    ready = payload(results[-1])
    assert ready["guidance"]["status"] == "needs_connection"
    call = ("prepare_workflow_gmail", {"setup_id": setup["id"], "expected_version": ready["version"],
                                      "idempotency_key": "request-gmail-intake"})
    _, [no_scope] = session(mcp_url, world, call, scopes="gluons.read gluons.plan")
    assert error_code(no_scope) == "FORBIDDEN"
    _, [handoff, replay] = session(mcp_url, world, call, call)
    handoff = payload(handoff)
    assert handoff == payload(replay)
    assert handoff["next_actions"][0]["owner_url"].startswith("http")
    assert handoff["next_actions"][0]["owner_url"].endswith(f"/setup/{handoff['connection']['id']}")
    assert not any(scope.endswith(".send") for scope in handoff["connection"]["requested_scopes"])
    _, [resumed] = session(mcp_url, world, ("get_workflow_setup", {"setup_id": setup["id"]}))
    assert payload(resumed) == handoff
    with tenant_tx(world.tenant_id) as db:
        saved = db.get(ConnectionSession, handoff["connection"]["id"])
        assert saved.connection_id is None and saved.state_hash is None


def test_chat_order_draft_runs_only_a_shadow_case_and_can_be_monitored_and_paused(mcp_url, world, erp, sandbox_erp):
    erp_id = world.connect_erp(sandbox_erp)
    setup = _start_workflow(mcp_url, world, "delegate_workflow")
    _, results = session(mcp_url, world, *_answers(setup, (
        ("process", "order_intake"), ("intake_channel", "manual"),
        ("process_example", "Enter routine synthetic orders."),
        ("system_of_record", "ERPNext simulator"),
        ("routine_decision", "assistant_decides_within_rules"),
        ("exceptions", "Review unknown items and report the simulated outcome."),
    )), ("create_automation", {"skill_id": "multichannel_order_entry", "idempotency_key": "create-order-draft",
                               "config": {"connections": {"erp": erp_id}, "currency": "USD"}}))
    setup, draft = map(payload, results[-2:])
    sample = ("submit_order_trial_case", {"automation_id": draft["id"], "text": "2 cases FISH-1",
                                         "sample_sender": "orders@harbor-bistro.com", "idempotency_key": "sample-fish-order"})
    _, [linked, rejected, trial, run, duplicate, changed] = session(
        mcp_url, world,
        ("link_workflow_draft", {"setup_id": setup["id"], "draft_id": draft["id"], "expected_version": setup["version"]}),
        ("submit_order_trial_case", sample[1] | {"idempotency_key": "before-trial"}),
        ("start_trial", {"automation_id": draft["id"], "expected_version": draft["version"], "idempotency_key": "start-order-shadow"}),
        sample, sample, ("submit_order_trial_case", sample[1] | {"text": "3 cases FISH-1"}),
    )
    assert payload(linked)["guidance"]["status"] == "workflow_linked"
    assert error_code(rejected) == "CONFLICT"
    assert payload(duplicate) == {"run_id": payload(run)["run_id"], "duplicate": True}
    assert error_code(changed) == "CONFLICT"
    assert drain() > 0
    with tenant_tx(world.tenant_id) as db:
        completed = db.get(Run, payload(run)["run_id"])
        assert completed.outcome["simulated"] and completed.outcome["outcome"] == "shadow_would_create_order"
    _, [status, evidence, paused] = session(
        mcp_url, world,
        ("get_task_status", {"task_id": draft["id"]}),
        ("get_task_evidence", {"task_id": draft["id"]}),
        ("pause_task", {"task_id": draft["id"], "expected_version": payload(trial)["version"], "idempotency_key": "pause-order-shadow"}),
    )
    assert payload(status)["mode"] == "shadow"
    assert all(not receipt["verified"] for receipt in payload(evidence)["receipts"])
    assert payload(paused)["status"] == "paused" and erp.orders() == []


def test_chat_reorder_uses_saved_rules_and_groups_only_reviewable_lines(mcp_url):
    world = World.create("Demo Workshop Supply")
    setup = _start_workflow(mcp_url, world, "delegate_workflow")
    _, results = session(mcp_url, world, *_answers(setup, (
        ("process", "inventory_reorder"),
        ("process_example", "Review low stock each morning."),
        ("system_of_record", "Warehouse counts"),
        ("routine_decision", "assistant_recommends"),
        ("exceptions", "Ask about missing costs and excessive quantities."),
    )), ("create_automation", {"skill_id": "inventory_reorder", "idempotency_key": "reorder-automation"}))
    setup, draft = map(payload, results[-2:])
    assert setup["guidance"]["automation_skill_id"] == "inventory_reorder"
    _, [linked, configured] = session(
        mcp_url, world,
        ("link_workflow_draft", {"setup_id": setup["id"], "draft_id": draft["id"], "expected_version": setup["version"]}),
        ("configure_inventory_reorder", {"automation_id": draft["id"], "expected_version": draft["version"],
                                         "idempotency_key": "reorder-rules",
                                         "rules": {"stock_location": "Main warehouse", "currency": "INR", "items": [
                                             {"sku": "BEANS", "supplier": "Supply Co", "reorder_below": 10, "target": 22,
                                              "max_order": 25, "pack_size": 6, "unit_cost_minor": 450},
                                             {"sku": "CUPS", "supplier": "Supply Co", "reorder_below": 5, "target": 10,
                                              "max_order": 10, "unit_cost_minor": 5},
                                             {"sku": "LIDS", "supplier": "Supply Co", "reorder_below": 5, "target": 10,
                                              "max_order": 5, "unit_cost_minor": 10},
                                             {"sku": "BAGS", "supplier": "Other Co", "reorder_below": 5, "target": 8,
                                              "max_order": 8},
                                         ]}}),
    )
    assert payload(linked)["guidance"]["status"] == "workflow_linked"
    assert "configure_inventory_reorder" in payload(linked)["guidance"]["next_tools"]
    case = ("submit_inventory_trial_case", {"automation_id": draft["id"], "idempotency_key": "warehouse-count",
                                            "snapshot": {"stock_location": "Main warehouse",
                                                         "observed_at": "2026-11-10T08:00:00Z", "basis": "owner_supplied",
                                                         "counts": [{"sku": "BEANS", "available": 3, "on_order": 2},
                                                                    {"sku": "CUPS", "available": 4, "on_order": 0},
                                                                    {"sku": "LIDS", "available": 0, "on_order": 0},
                                                                    {"sku": "BAGS", "available": 0, "on_order": 0}]}})
    _, [before_trial, trial, started, retry] = session(
        mcp_url, world, case,
        ("start_trial", {"automation_id": draft["id"], "expected_version": payload(configured)["version"],
                         "idempotency_key": "reorder-shadow"}), case, case,
    )
    assert error_code(before_trial) == "CONFLICT"
    run_id = payload(started)["run_id"]
    assert payload(retry) == {"run_id": run_id, "duplicate": True}
    assert drain() > 0
    _, [report, evidence, paused] = session(
        mcp_url, world,
        ("get_task_run", {"run_id": run_id}),
        ("get_task_evidence", {"task_id": draft["id"]}),
        ("pause_task", {"task_id": draft["id"], "expected_version": payload(trial)["version"],
                        "idempotency_key": "pause-inventory-trial"}),
    )
    outcome = payload(report)["outcome"]
    assert outcome["simulated"] and outcome["outcome"] == "reorder_review_prepared"
    review = outcome["reorder_review"]
    assert review["external_actions_performed"] == 0 and review["basis"] == "owner_supplied" and review["currency"] == "INR"
    assert len(review["drafts"]) == 1
    grouped = review["drafts"][0]
    assert grouped["supplier"] == "Supply Co" and grouped["estimated_total_minor"] == 8130
    assert [(row["sku"], row["quantity"]) for row in grouped["lines"]] == [("BEANS", 18), ("CUPS", 6)]
    assert [(row["sku"], row["reason"]) for row in review["rows"] if row["status"] == "needs_review"] == [
        ("LIDS", "quantity_limit_exceeded"), ("BAGS", "missing_price"),
    ]
    assert payload(evidence)["receipts"] == [] and payload(paused)["status"] == "paused"
    _, [foreign] = session(mcp_url, World.create("Another warehouse"), ("get_task_run", {"run_id": run_id}))
    assert error_code(foreign) == "NOT_FOUND"


def test_estimate_cost_never_invents_a_model_price(mcp_url, world):
    _, [draft, live] = session(mcp_url, world, ("estimate_cost", {"skill_id": "property_maintenance"}),
                               ("estimate_cost", {"skill_id": "multichannel_order_entry"}))
    assert payload(draft)["model_cost"]["status"] == "deterministic_no_model"
    unpriced = payload(live)["model_cost"]
    assert unpriced["status"] == "no_evaluated_model_configured" and unpriced["usd_per_task_high"] is None


def test_missing_connections_reflects_real_rows(mcp_url, world):
    _, [result] = session(mcp_url, world, ("list_missing_connections", {"skill_id": "multichannel_order_entry"}))
    missing = payload(result)["missing"]
    assert missing and {row["capability"] for row in missing} >= {"erp"}
    world.connect_erp("http://127.0.0.1:1/simulator")
    automation = world.order_automation()
    _, [bound] = session(mcp_url, world, ("list_missing_connections", {"automation_id": automation["id"]}))
    data = payload(bound)
    assert "erp" in {row["capability"] for row in data["ready"]} and "erp" not in {row["capability"] for row in data["missing"]}


def test_policy_tools_grant_nothing_and_activation_never_self_approves(mcp_url, world):
    world.connect_erp("http://127.0.0.1:1/simulator")
    automation = world.order_automation()
    target = automation["id"]
    _, [preview, status, requested, change] = session(
        mcp_url, world,
        ("policy_preview", {"automation_id": target}),
        ("policy_status", {"automation_id": target}),
        ("policy_request_activation", {"automation_id": target, "mode": "live", "idempotency_key": "ask-owner-activate"}),
        ("set_task_policy", {"automation_id": target, "mode": "supervised", "allowed_actions": ["erp.order.create"],
                             "idempotency_key": "ask-owner-policy", "per_action_limit_minor": 1000}),
    )
    assert payload(preview)["grants_nothing"] is True
    assert "erp.order.create" in {a["name"] for a in payload(preview)["proposed"]["actions"]}
    assert payload(status)["signed_policy"] is None and payload(status)["live"] is False
    asked = payload(requested)
    assert asked["status"] == "owner_confirmation_required" and asked["self_approved"] is False
    assert any("Sign the policy yourself" in step for step in asked["required_owner_steps"])
    assert {m["kind"] for m in asked["missing"]} >= {"policy"}
    assert payload(change)["applied"] is False and payload(change)["diff"]["actions_added"] == ["erp.order.create"]
    with tenant_tx(world.tenant_id) as db:
        assert db.execute(select(AutomationPolicy)).first() is None
        row = db.get(Automation, target)
        assert row.status == "ready" and row.current_policy_id is None
        kinds = set(db.execute(select(Notification.kind)).scalars())
    assert {"policy_activation_requested", "policy_change_requested"} <= kinds


def test_set_task_policy_rejects_an_action_the_skill_does_not_declare(mcp_url, world):
    automation = world.order_automation()
    _, [result] = session(mcp_url, world, ("set_task_policy", {"automation_id": automation["id"], "mode": "live",
                                                               "allowed_actions": ["wire.transfer"],
                                                               "idempotency_key": "undeclared-action"}))
    assert error_code(result) == "VALIDATION_FAILED"


def test_assistant_can_tighten_a_budget_but_never_raise_one(mcp_url, world):
    automation = lifecycle.create(world.owner, skill_id="property_maintenance", monthly_budget_usd=Decimal("50"))
    _, [lowered, raised] = session(mcp_url, world,
                                   ("set_budget", {"monthly_budget_usd": 20, "automation_id": automation["id"],
                                                   "expected_version": automation["version"], "idempotency_key": "lower-budget-1"}),
                                   ("set_budget", {"monthly_budget_usd": 90, "automation_id": automation["id"],
                                                   "idempotency_key": "raise-budget-1"}))
    assert payload(lowered)["monthly_budget_usd"] == "20.00"
    assert error_code(raised) == "OWNER_WEB_REQUIRED"
    with tenant_tx(world.tenant_id) as db:
        assert db.get(Automation, automation["id"]).monthly_budget_usd == Decimal("20.00")
    # The first ceiling on an unlimited budget is a tightening; the owner can raise it on the web.
    _, [first] = session(mcp_url, world, ("set_budget", {"monthly_budget_usd": 5, "idempotency_key": "org-budget-1"}))
    assert payload(first)["previous_budget_usd"] is None
    with tenant_tx(world.tenant_id) as db:
        assert db.get(Organization, world.tenant_id).monthly_budget_usd == Decimal("5.00")


def test_read_only_assistant_cannot_use_mutating_tools(mcp_url, world):
    automation = world.order_automation()
    _, results = session(mcp_url, world,
                         ("policy_request_activation", {"automation_id": automation["id"], "mode": "live", "idempotency_key": "readonly-ask"}),
                         ("set_budget", {"monthly_budget_usd": 1, "idempotency_key": "readonly-budget"}),
                         ("connection_disconnect", {"connection_id": "con_x", "idempotency_key": "readonly-disc"}),
                         ("phone_route_begin", {"mode": "new_number", "idempotency_key": "readonly-phone"}),
                         scopes="gluons.read")
    assert [error_code(r) for r in results] == ["FORBIDDEN"] * 4


@pytest.mark.parametrize("tier", ["R3", "R4"])
def test_no_assistant_can_approve_high_risk_but_can_deny_it(mcp_url, world, tier):
    first_id, first_version = pending_approval(world, tier=tier)
    second_id, second_version = pending_approval(world, tier=tier)
    _, [approved, denied, denied_again] = session(
        mcp_url, world,
        ("approve_action", {"approval_id": first_id, "decision": "approve", "expected_version": first_version,
                            "idempotency_key": f"approve-high-{tier}"}),
        ("deny_action", {"approval_id": second_id, "expected_version": second_version, "reason": "Not this one",
                         "idempotency_key": f"deny-high-{tier}"}),
        ("approve_action", {"approval_id": first_id, "decision": "deny", "expected_version": first_version,
                            "idempotency_key": f"deny-through-approve-{tier}"}),
    )
    assert error_code(approved) == "FORBIDDEN"
    assert payload(denied)["status"] == "denied"
    assert payload(denied_again)["status"] == "denied"


def test_assistant_binding_inside_its_tier_can_still_approve(mcp_url, world):
    approval_id, version = pending_approval(world, tier="R1")
    _, [result] = session(mcp_url, world, ("approve_action", {"approval_id": approval_id, "decision": "approve",
                                                              "expected_version": version, "idempotency_key": "approve-low-tier"}))
    assert payload(result)["status"] == "approved"


def test_deny_without_the_approvals_scope_is_refused(mcp_url, world):
    approval_id, version = pending_approval(world, tier="R3")
    _, [result] = session(mcp_url, world, ("deny_action", {"approval_id": approval_id, "expected_version": version,
                                                           "idempotency_key": "deny-no-scope"}),
                          scopes="gluons.read gluons.plan gluons.tasks")
    assert error_code(result) == "FORBIDDEN"


def test_business_launch_approval_stays_web_only_but_can_be_denied(mcp_url, world):
    first_id, first_version = pending_approval(world, tier="R1", action="business_launch.plan")
    second_id, second_version = pending_approval(world, tier="R1", action="business_launch.other")
    _, [approved, denied] = session(mcp_url, world,
                                    ("approve_action", {"approval_id": first_id, "decision": "approve", "expected_version": first_version,
                                                        "idempotency_key": "launch-approve"}),
                                    ("deny_action", {"approval_id": second_id, "expected_version": second_version,
                                                     "idempotency_key": "launch-deny"}))
    assert error_code(approved) == "FORBIDDEN" and payload(denied)["status"] == "denied"


def test_phone_tools_only_record_a_request(mcp_url, world):
    _, [begun] = session(mcp_url, world, ("phone_route_begin", {"mode": "carrier_forwarding", "number_hint": "+15550100123",
                                                                "country": "US", "idempotency_key": "phone-begin-1"}))
    route = payload(begun)
    assert route["state"] == "awaiting_provider_setup" and route["test_call_verified"] is False
    _, [status] = session(mcp_url, world, ("phone_route_status", {"route_id": route["route_id"]}))
    assert payload(status)["network_routing_verified"] is False
    with tenant_tx(world.tenant_id) as db:
        assert db.get(PhoneRoute, route["route_id"]).number == "unassigned"


def test_connection_session_tools_return_no_secret_and_disconnect_fences(mcp_url, world):
    connection_id = world.connect_erp("http://127.0.0.1:1/simulator")
    automation = world.order_automation()
    _, [begun] = session(mcp_url, world, ("connection_begin", {"provider": "gmail", "requested_capabilities": ["read_order_messages"],
                                                               "idempotency_key": "conn-begin-1"}))
    started = payload(begun)
    assert "authorization_url" not in started and "token" not in json.dumps(started).lower()
    _, [status, disconnected] = session(mcp_url, world,
                                        ("connection_status", {"session_id": started["session_id"]}),
                                        ("connection_disconnect", {"connection_id": connection_id, "idempotency_key": "conn-off-1"}))
    assert payload(status)["session_id"] == started["session_id"]
    assert payload(disconnected)["status"] == "disconnected"
    assert automation["id"] in payload(disconnected)["affected_automations"]
    assert lifecycle.get(world.owner, automation["id"])["status"] == "waiting_external"


def test_mutating_tools_replay_idempotently(mcp_url, world):
    args = {"monthly_budget_usd": 7, "idempotency_key": "budget-replay-key"}
    _, [first, second] = session(mcp_url, world, ("set_budget", args), ("set_budget", args))
    assert payload(first) == payload(second)
