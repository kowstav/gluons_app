"""Official SDK Streamable HTTP with real PostgreSQL authorization and services."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from gluons.mcp.server import create_mcp_app, create_server
from gluons.oauth import service
from helpers import World
from test_assistant_oauth import issue


@pytest.fixture
def remote(world):
    _, _, pair = issue(world)
    server = create_server()
    with TestClient(create_mcp_app(server)) as client:
        client.headers.update({"Authorization": f"Bearer {pair['access_token']}",
                               "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"})
        yield client


def rpc(client, method, params=None, *, version=None):
    headers = {"MCP-Protocol-Version": version} if version else {}
    params = dict(params or {})
    if version == "2026-07-28":
        headers["MCP-Method"] = method
        params["_meta"] = {"io.modelcontextprotocol/protocolVersion": version, "io.modelcontextprotocol/clientCapabilities": {}}
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def call(client, name, arguments):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments})["result"]


def data(result):
    assert not result.get("isError"), result
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def test_unauthenticated_and_wrong_resource_requests_are_rejected(database):
    with TestClient(create_mcp_app()) as client:
        result = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert result.status_code == 401 and "resource_metadata=" in result.headers["www-authenticate"]
        client.headers["Authorization"] = "Bearer arbitrary-upstream-token"
        assert client.post("/mcp", json={}).status_code == 401


def test_legacy_initialize_and_tool_discovery_are_official_sdk_protocol(remote):
    init = rpc(remote, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                      "clientInfo": {"name": "Gluons test", "version": "1"}})
    assert init["result"]["serverInfo"]["name"] == "Gluons"
    tools = rpc(remote, "tools/list")["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"discover_automations", "start_trial", "approve_action", "pause_task", "connect_service"} <= names
    assert all("tenant_id" not in tool["inputSchema"].get("properties", {}) for tool in tools)


def test_modern_protocol_can_read_tools_without_a_session(remote):
    result = rpc(remote, "tools/list", version="2026-07-28")
    assert result["result"]["tools"]


def test_mcp_mutations_persist_and_retry_idempotently(remote, world):
    args = {"skill_id": "multichannel_order_entry", "name": "From assistant", "idempotency_key": "same-mcp-create-key"}
    first = data(call(remote, "create_automation", args))
    second = data(call(remote, "create_automation", args))
    assert first["id"] == second["id"]
    status = data(call(remote, "get_task_status", {"task_id": first["id"]}))
    assert status["name"] == "From assistant" and status["created_via"] == "assistant"
    result = data(call(remote, "cancel_task", {"task_id": first["id"], "expected_version": status["version"],
                                                "idempotency_key": "cancel-mcp-task-key"}))
    assert result["status"] in ("cancel_requested", "cancelled")


def test_mcp_cross_tenant_id_returns_not_found(remote, world):
    other = World.create()
    automation = other.order_automation()
    result = call(remote, "get_task_status", {"task_id": automation["id"]})
    assert result["isError"] is True and "NOT_FOUND" in result["content"][0]["text"]


def test_readonly_token_cannot_create_and_revocation_takes_effect_per_request(world):
    _, _, pair = issue(world, scopes="gluons.read")
    binding = service.resolve_access_token(pair["access_token"]).binding_id
    with TestClient(create_mcp_app()) as client:
        client.headers.update({"Authorization": f"Bearer {pair['access_token']}",
                               "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"})
        result = call(client, "create_automation", {"skill_id": "multichannel_order_entry", "idempotency_key": "read-cannot-create"})
        assert result["isError"] is True and "FORBIDDEN" in result["content"][0]["text"]
        assert data(call(client, "list_running_tasks", {}))["automations"] == []
        service.revoke_binding(world.owner, binding)
        assert client.post("/mcp", json={}).status_code == 401


def test_mcp_rejects_wrong_origin_even_with_valid_bearer(remote):
    response = remote.post("/mcp", json={}, headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403


def test_commerce_mcp_plan_is_persistent_and_requires_owner_handoffs(remote):
    plan = data(call(remote, "create_business_launch_plan", {"goal": "Start a tea store", "country": "IN",
                                                            "idempotency_key": "mcp-commerce-plan-key"}))
    assert plan["stage"] == "INTENT_CAPTURED" and plan["payment_status"] == "unverified" and plan["store_live"] is False
    result = data(call(remote, "get_business_launch_plan", {"plan_id": plan["id"]}))
    assert result["plan"]["id"] == plan["id"]
    assert result["plan"]["readiness"] == result["evidence"]["readiness"]
    assert result["plan"]["readiness"]["status"] == "blocked"
    cancelled = data(call(remote, "cancel_business_launch_plan", {"plan_id": plan["id"], "expected_version": plan["version"],
                                                                 "idempotency_key": "mcp-cancel-commerce"}))
    assert cancelled["branch_status"] == "CANCELLED"
