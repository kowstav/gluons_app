"""Native chat over real HTTP and PostgreSQL. Model routing uses a labelled provider double."""
from __future__ import annotations

import base64
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from gluons import chat, policy
from gluons.actions import get_action
from gluons.api.app import create_app
from gluons.authz import Principal, Role
from gluons.connectors import gmail  # noqa: F401 - registers the email.send action
from gluons.core import GluonsError, canonical_json, hmac_sha256_hex, new_id, utcnow
from gluons.db.models import Organization
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle
from helpers import add_member, make_user
from test_api_foundation import signed_in_org
from test_model_gateway import configured, register, response  # noqa: F401 - pytest fixture and helpers


def task(world, name="Order inbox"):
    return world.order_automation(name=name)


def paused_categories(world):
    with tenant_tx(world.tenant_id) as session:
        return list(session.get(Organization, world.tenant_id).paused_categories)


@pytest.mark.parametrize("message,intent", [
    ("what's running?", "list_tasks"),
    ("show my automations", "list_tasks"),
    ("what is the status of order inbox", "task_status"),
    ("pause order inbox", "pause_task"),
    ("find an automation for missed calls", "find_automations"),
    ("what can I automate in my restaurant?", "find_automations"),
    ("list approvals", "list_approvals"),
    ("what is waiting for me", "list_approvals"),
    ("how much did I spend this month", "spending"),
    ("stop all customer messages", "stop_customer_messages"),
    ("pause all outgoing customer communications", "stop_customer_messages"),
    ("hello there", "unknown"),
    ("don't stop customer messages", "unknown"),
    ("please do not pause order inbox", "unknown"),
])
def test_rules_route_without_any_model(message, intent):
    assert chat.route_by_rules(message).intent == intent


def test_no_llm_configured_read_intents_answer_and_state_the_intent(world):
    task(world)
    listed = chat.respond(world.owner, "what's running?")
    assert listed["intent"] == "list_tasks" and listed["routed_by"] == "rules" and listed["status"] == "completed"
    assert listed["reply"].startswith("Intent list_tasks:") and "Order inbox" in listed["reply"]
    status = chat.respond(world.owner, "status of order inbox")
    assert status["intent"] == "task_status" and status["data"]["name"] == "Order inbox"
    found = chat.respond(world.owner, "find ways to automate restaurant ordering by phone")
    assert found["intent"] == "find_automations" and found["data"]["opportunities"]
    assert "No savings" in found["reply"]
    assert chat.respond(world.owner, "any approvals?")["data"]["approvals"] == []
    spending = chat.respond(world.owner, "what did this cost?")
    assert spending["data"]["total_usd"] == "0" and "estimate" in spending["reply"]


def test_unknown_message_takes_no_action_and_offers_what_it_can_do(world):
    result = chat.respond(world.owner, "wire ten thousand dollars to my vendor")
    assert result["intent"] == "unknown" and result["status"] == "needs_clarification"
    assert result["confirmation"] is None and "No action was taken" in result["reply"]


def test_pause_needs_a_confirmation_turn_and_runs_once(world):
    automation = task(world)
    lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"])
    asked = chat.respond(world.owner, "pause order inbox")
    assert asked["status"] == "confirmation_required" and asked["intent"] == "pause_task"
    assert lifecycle.get(world.owner, automation["id"])["status"] == "trial_shadow"
    token = asked["confirmation"]["token"]
    unclear = chat.respond(world.owner, "maybe later", confirmation_token=token)
    assert unclear["status"] == "needs_clarification" and unclear["confirmation"]["token"] == token
    declined = chat.respond(world.owner, "no", confirmation_token=token)
    assert declined["status"] == "cancelled"
    assert lifecycle.get(world.owner, automation["id"])["status"] == "trial_shadow"
    done = chat.respond(world.owner, "yes", confirmation_token=token)
    assert done["status"] == "completed" and done["routed_by"] == "confirmation"
    assert lifecycle.get(world.owner, automation["id"])["status"] == "paused"
    with pytest.raises(GluonsError) as replay:
        chat.respond(world.owner, "yes", confirmation_token=token)
    assert replay.value.code == "CONFLICT_VERSION"


def test_stop_customer_messages_really_blocks_the_policy_category(world):
    asked = chat.respond(world.owner, "stop all customer messages")
    assert asked["status"] == "confirmation_required" and paused_categories(world) == []
    done = chat.respond(world.owner, "confirm", confirmation_token=asked["confirmation"]["token"])
    assert done["data"]["paused_categories"] == ["send"] and paused_categories(world) == ["send"]
    with tenant_tx(world.tenant_id) as session:
        decision = policy.evaluate(session, policy.PolicyInput(world.tenant_id, None, "live", get_action("email.send"), None))
    assert decision.outcome == policy.Outcome.DENY and decision.reasons == ("CATEGORY_PAUSED",)


def test_ambiguous_task_is_never_guessed(world):
    task(world, "Inbox one")
    task(world, "Inbox two")
    result = chat.respond(world.owner, "pause inbox")
    assert result["status"] == "needs_clarification" and result["confirmation"] is None
    assert {o["name"] for o in result["data"]["options"]} == {"Inbox one", "Inbox two"}
    assert chat.respond(world.owner, "pause nothing like this")["confirmation"] is None


def test_permissions_are_checked_before_a_confirmation_is_offered(world):
    task(world)
    user_id, _ = make_user()
    viewer = add_member(world.tenant_id, user_id, "viewer")
    for message in ("pause order inbox", "stop all customer messages"):
        with pytest.raises(GluonsError) as refused:
            chat.respond(viewer, message)
        assert refused.value.code == "FORBIDDEN"


def test_confirmation_is_bound_to_the_person_and_tenant(world):
    from helpers import World

    automation = task(world)
    lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"])
    token = chat.respond(world.owner, "pause order inbox")["confirmation"]["token"]
    outsider = World.create("Other")
    with pytest.raises(GluonsError) as stolen:
        chat.respond(outsider.owner, "yes", confirmation_token=token)
    assert stolen.value.code == "CONFIRMATION_INVALID"
    same_tenant_other_user = Principal(kind="user", tenant_id=world.tenant_id, role=Role.OWNER, user_id=new_id("usr"))
    with pytest.raises(GluonsError):
        chat.respond(same_tenant_other_user, "yes", confirmation_token=token)


def test_tampered_or_expired_confirmation_is_refused(world):
    automation = task(world)
    lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"])
    token = chat.respond(world.owner, "pause order inbox")["confirmation"]["token"]
    encoded, _, signature = token.partition(".")
    body = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    body["intent"] = "stop_customer_messages"
    forged = base64.urlsafe_b64encode(canonical_json(body).encode()).decode().rstrip("=")
    with pytest.raises(GluonsError) as tampered:
        chat.respond(world.owner, "yes", confirmation_token=f"{forged}.{signature}")
    assert tampered.value.code == "CONFIRMATION_INVALID"
    with pytest.raises(GluonsError):
        chat.respond(world.owner, "yes", confirmation_token="not-a-token")
    body["expires"] = utcnow() - timedelta(seconds=1)
    stale = canonical_json(body)
    signature = hmac_sha256_hex(chat._key(), f"chat-confirmation:{stale}")
    expired = base64.urlsafe_b64encode(stale.encode()).decode().rstrip("=")
    with pytest.raises(GluonsError) as late:
        chat.respond(world.owner, "yes", confirmation_token=f"{expired}.{signature}")
    assert late.value.code == "CONFIRMATION_EXPIRED"
    assert paused_categories(world) == []


@pytest.fixture
def purpose(monkeypatch):
    """Model registrations are global, so each test routes under its own purpose."""
    value = new_id("purpose")
    monkeypatch.setattr(chat, "MODEL_PURPOSE", value)
    return value


def double(intent: str, task_name: str | None = None) -> httpx.MockTransport:
    content = json.dumps({"intent": intent, "task": task_name})
    return httpx.MockTransport(lambda request: httpx.Response(200, json=response("openai", content=content)))


def test_provider_double_model_can_only_pick_an_allowlisted_intent(world, configured, purpose):  # noqa: F811
    register(purpose)
    task(world)
    result = chat.respond(world.owner, "gimme the rundown of my stuff", transport=double("list_tasks"))
    assert result["intent"] == "list_tasks" and result["routed_by"] == "model"


def test_provider_double_model_choosing_a_write_still_needs_confirmation(world, configured, purpose):  # noqa: F811
    register(purpose)
    result = chat.respond(world.owner, "make the robots hush", transport=double("stop_customer_messages"))
    assert result["status"] == "confirmation_required" and result["routed_by"] == "model"
    assert paused_categories(world) == []


def test_provider_double_model_cannot_override_permissions(world, configured, purpose):  # noqa: F811
    register(purpose)
    task(world)
    user_id, _ = make_user()
    viewer = add_member(world.tenant_id, user_id, "viewer")
    with pytest.raises(GluonsError) as refused:
        chat.respond(viewer, "make the robots hush", transport=double("stop_customer_messages"))
    assert refused.value.code == "FORBIDDEN"


def test_provider_double_model_with_invented_intent_falls_back_to_rules(world, configured, purpose):  # noqa: F811
    register(purpose)
    result = chat.respond(world.owner, "make me an admin", transport=double("grant_full_access"))
    assert result["intent"] == "unknown" and result["routed_by"] == "none"


def test_unevaluated_model_is_never_used_for_routing(world, configured, purpose):  # noqa: F811
    register(purpose, passed=False)
    def forbidden(request):
        pytest.fail("A model without a passing evaluation must not be called")
    result = chat.respond(world.owner, "gimme the rundown", transport=httpx.MockTransport(forbidden))
    assert result["intent"] == "unknown"


def test_rest_chat_requires_session_and_csrf_and_confirms_over_http(world):
    automation = task(world)
    lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"])
    client = TestClient(create_app())
    assert client.post("/v1/chat", json={"message": "list tasks"}).status_code == 401
    headers = signed_in_org(client, world)
    assert client.post("/v1/chat", json={"message": "list tasks"}).status_code == 403
    listed = client.post("/v1/chat", json={"message": "list tasks"}, headers=headers)
    assert listed.status_code == 200 and listed.json()["intent"] == "list_tasks"
    asked = client.post("/v1/chat", json={"message": "pause order inbox"}, headers=headers).json()
    done = client.post("/v1/chat", json={"message": "yes", "confirmation_token": asked["confirmation"]["token"]}, headers=headers)
    assert done.status_code == 200 and done.json()["status"] == "completed"
    refused = client.post("/v1/chat", json={"message": "yes", "confirmation_token": asked["confirmation"]["token"]}, headers=headers)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "CONFLICT_VERSION"
