"""Isolated proof graph: provider doubles only, no real Gmail traffic."""
from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gluons import approvals, policy, vault
from gluons.api.app import create_app
from gluons.config import get_settings
from gluons.connectors.gmail import GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE
from gluons.connectors.gmail_self_proof import ACTION
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import ActionReceipt, Approval, Connection, Membership, Organization
from gluons.db.session import tenant_tx
from gluons.runtime import engine, lifecycle
from gluons.skills.registry import get_registry
from helpers import PASSWORD, add_member, make_user


@pytest.fixture
def isolated_registry(database, monkeypatch):
    assets = Path(__file__).parent / "provider_proof_assets"
    settings = get_settings()
    monkeypatch.setattr(settings, "skills_dir", str(assets / "skills"))
    monkeypatch.setattr(settings, "blueprint_dir", str(assets / "blueprint"))
    monkeypatch.setattr(settings, "gmail_proof_enabled", True)
    from gluons.connectors import gmail_self_proof

    monkeypatch.setattr(gmail_self_proof.google, "identity", lambda token: {"sub": "synthetic-sub", "email": "owner@example.com"})
    get_registry.cache_clear()
    yield
    get_registry.cache_clear()


@pytest.fixture
def proof_world(world, isolated_registry):
    with tenant_tx(world.tenant_id, world.user_id) as session:
        secret = vault.store(session, world.tenant_id, "gmail", {"access_token": "synthetic-token"})
        connection = Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="gmail", environment="production",
                                external_account_id="synthetic-sub", account_display="owner@example.com", account_confirmed_at=utcnow(),
                                granted_scopes=[GMAIL_SEND_SCOPE, GMAIL_READ_SCOPE], credential_ref=secret,
                                created_by=world.user_id, metadata_json={"account_email": "owner@example.com"})
        session.add(connection)
        connection_id = connection.id
    automation = lifecycle.create(world.owner, skill_id="gmail_self_proof", config={"connections": {"email": connection_id}})
    return world, automation["id"], connection_id


def grant(world, automation_id, connection_id, **overrides):
    spec = {"mode": "supervised", "allowed_actions": [ACTION], "connection_ids": [connection_id],
            "resource_filters": {ACTION: {"allowed_recipients": ["owner@example.com"]}},
            "expires_at": utcnow() + timedelta(hours=1), **overrides}
    with tenant_tx(world.tenant_id, world.user_id) as session:
        result = policy.propose(session, world.owner, automation_id, spec)
        policy.sign(session, world.owner, result.id)
        return result.id


def activate_and_run(world, automation_id, source="synthetic-1"):
    current = lifecycle.get(world.owner, automation_id)
    if current["status"] != "active":
        lifecycle.activate(world.owner, automation_id, "supervised", current["version"])
    return lifecycle.submit_event(world.tenant_id, automation_id, source="manual", source_event_id=source,
                                  payload={}, actor=world.owner)["run_id"]


def test_one_self_send_requires_signed_bounded_grant_and_exact_owner_approval(proof_world):
    world, automation_id, connection_id = proof_world
    grant(world, automation_id, connection_id)
    run_id = activate_and_run(world, automation_id)
    messages = []

    def provider(request):
        if request.method == "POST":
            messages.append(json.loads(request.content)["raw"])
            return httpx.Response(200, json={"id": "synthetic-id"})
        return httpx.Response(200, json={"id": "synthetic-id", "raw": messages[0], "labelIds": ["SENT"]})

    transport = httpx.MockTransport(provider)
    first = engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport)
    assert first.status == "waiting_approval" and messages == []
    with tenant_tx(world.tenant_id, world.user_id) as session:
        approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
        assert approval.required_role == "owner" and approval.risk_tier == "R3"
        approval_id = approval.id
    admin_id, _ = make_user()
    admin = add_member(world.tenant_id, admin_id, "admin")
    with pytest.raises(GluonsError):
        with tenant_tx(world.tenant_id, admin_id) as session:
            approvals.decide(session, admin, approval_id, "approve")
    with tenant_tx(world.tenant_id, world.user_id) as session:
        approvals.decide(session, world.owner, approval_id, "approve")
    completed = engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport)
    assert completed.status == "completed" and len(messages) == 1
    with tenant_tx(world.tenant_id) as session:
        receipt = session.scalar(select(ActionReceipt).where(ActionReceipt.run_id == run_id))
        assert receipt.action == ACTION and receipt.verified is True and receipt.environment == "production"
    second_run = activate_and_run(world, automation_id, "synthetic-2")
    engine.advance(world.tenant_id, second_run, "proof-worker", transport=transport)
    assert len(messages) == 1


def test_no_policy_no_effect_and_no_activation(proof_world):
    world, automation_id, _ = proof_world
    current = lifecycle.get(world.owner, automation_id)
    with pytest.raises(GluonsError):
        lifecycle.activate(world.owner, automation_id, "supervised", current["version"])


@pytest.mark.parametrize("overrides", [
    {"allowed_actions": [ACTION, "email.send"]},
    {"connection_ids": []},
    {"resource_filters": {}},
    {"expires_at": None},
    {"expires_at": utcnow() + timedelta(days=2)},
])
def test_unbounded_policy_cannot_reach_provider(proof_world, overrides):
    world, automation_id, connection_id = proof_world
    grant(world, automation_id, connection_id, **overrides)
    run_id = activate_and_run(world, automation_id)
    result = engine.advance(world.tenant_id, run_id, "proof-worker",
                            transport=httpx.MockTransport(lambda request: pytest.fail("provider must not be called")))
    assert result.status == "failed"


def test_disabled_proof_cannot_reach_provider(proof_world, monkeypatch):
    world, automation_id, connection_id = proof_world
    grant(world, automation_id, connection_id)
    run_id = activate_and_run(world, automation_id)
    monkeypatch.setattr(get_settings(), "gmail_proof_enabled", False)
    result = engine.advance(world.tenant_id, run_id, "proof-worker",
                            transport=httpx.MockTransport(lambda request: pytest.fail("provider must not be called")))
    assert result.status == "failed"


@pytest.mark.parametrize("setting,value", [
    ("database_url", "postgresql://app:secret@remote.example/database"),
    ("skills_dir", "not-the-proof-fixture"),
    ("env", "production"),
])
def test_nonisolated_configuration_cannot_reach_provider(proof_world, monkeypatch, setting, value):
    world, automation_id, connection_id = proof_world
    grant(world, automation_id, connection_id)
    run_id = activate_and_run(world, automation_id)
    monkeypatch.setattr(get_settings(), setting, value)
    result = engine.advance(world.tenant_id, run_id, "proof-worker",
                            transport=httpx.MockTransport(lambda request: pytest.fail("provider must not be called")))
    assert result.status == "failed"


@pytest.mark.parametrize("url,allowed", [
    ("postgresql://user@/db?host=/tmp/local-postgres", True),
    ("postgresql://user@127.0.0.1/db", True),
    ("postgresql://user@127.0.0.1/db?host=remote.example", False),
    ("postgresql://user@127.0.0.1/db?hostaddr=192.0.2.1", False),
    ("postgresql://user@/db?host=/tmp/local,remote.example", False),
    ("postgresql://user@/db", False),
])
def test_isolation_guard_handles_local_sockets_and_rejects_remote_overrides(isolated_registry, monkeypatch, url, allowed):
    from gluons.connectors.gmail_self_proof import isolated_proof_enabled

    for name in ("database_url", "system_database_url", "migration_database_url"):
        monkeypatch.setattr(get_settings(), name, url)
    assert isolated_proof_enabled() is allowed


def test_account_identity_mismatch_never_sends(proof_world, monkeypatch):
    from gluons.connectors import gmail_self_proof

    world, automation_id, connection_id = proof_world
    grant(world, automation_id, connection_id)
    run_id = activate_and_run(world, automation_id)
    transport = httpx.MockTransport(lambda request: pytest.fail("Gmail must not be called"))
    assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "waiting_approval"
    with tenant_tx(world.tenant_id, world.user_id) as session:
        approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
        approvals.decide(session, world.owner, approval.id, "approve")
    monkeypatch.setattr(gmail_self_proof.google, "identity", lambda token: {"sub": "wrong-account", "email": "owner@example.com"})
    engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport)
    with tenant_tx(world.tenant_id) as session:
        result = session.scalar(select(ActionReceipt).where(ActionReceipt.run_id == run_id))
        assert result.verified is False and result.status == "not_applied"


def test_lost_send_response_looks_up_before_retry(proof_world):
    world, automation_id, connection_id = proof_world
    grant(world, automation_id, connection_id)
    run_id = activate_and_run(world, automation_id)
    writes = []

    def provider(request):
        if request.method == "POST":
            writes.append(json.loads(request.content)["raw"])
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "synthetic-id"}]})
        return httpx.Response(200, json={"id": "synthetic-id", "raw": writes[0], "labelIds": ["SENT"]})

    transport = httpx.MockTransport(provider)
    assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "waiting_approval"
    with tenant_tx(world.tenant_id, world.user_id) as session:
        approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
        approvals.decide(session, world.owner, approval.id, "approve")
    assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "retrying"
    assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "completed"
    assert len(writes) == 1


def test_invalid_send_response_is_ambiguous_and_never_blindly_resent(proof_world):
    world, automation_id, connection_id = proof_world
    grant(world, automation_id, connection_id)
    run_id = activate_and_run(world, automation_id)
    writes = []

    def provider(request):
        if request.method == "POST":
            writes.append(request.content)
            return httpx.Response(200, content=b"invalid response")
        return httpx.Response(200, json={"messages": []})

    transport = httpx.MockTransport(provider)
    assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "waiting_approval"
    with tenant_tx(world.tenant_id, world.user_id) as session:
        approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
        approvals.decide(session, world.owner, approval.id, "approve")
    assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "retrying"
    engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport)
    assert len(writes) == 1


@pytest.mark.parametrize("revocation", ["policy", "connection", "owner", "emergency_stop"])
def test_revocation_after_approval_prevents_dispatch(proof_world, revocation):
    world, automation_id, connection_id = proof_world
    policy_id = grant(world, automation_id, connection_id)
    run_id = activate_and_run(world, automation_id)
    transport = httpx.MockTransport(lambda request: pytest.fail("provider must not be called"))
    assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "waiting_approval"
    with tenant_tx(world.tenant_id, world.user_id) as session:
        approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
        approvals.decide(session, world.owner, approval.id, "approve")
    with tenant_tx(world.tenant_id, world.user_id) as session:
        if revocation == "policy":
            policy.revoke(session, world.owner, policy_id)
        elif revocation == "connection":
            session.get(Connection, connection_id).status = "disconnected"
        elif revocation == "owner":
            session.scalar(select(Membership).where(Membership.user_id == world.user_id)).role = "admin"
        else:
            session.get(Organization, world.tenant_id).emergency_stop_at = utcnow()
    engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport)
    with tenant_tx(world.tenant_id) as session:
        receipt = session.scalar(select(ActionReceipt).where(ActionReceipt.run_id == run_id))
        assert receipt is None or receipt.verified is False


def test_concurrent_approved_runs_share_one_effect(proof_world):
    world, automation_id, connection_id = proof_world
    grant(world, automation_id, connection_id)
    runs = [activate_and_run(world, automation_id, f"synthetic-{i}") for i in range(2)]
    writes = []

    def provider(request):
        if request.method == "POST":
            writes.append(json.loads(request.content)["raw"])
            return httpx.Response(200, json={"id": "synthetic-id"})
        return httpx.Response(200, json={"id": "synthetic-id", "raw": writes[0], "labelIds": ["SENT"]})

    transport = httpx.MockTransport(provider)
    for run_id in runs:
        assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "waiting_approval"
        with tenant_tx(world.tenant_id, world.user_id) as session:
            approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
            approvals.decide(session, world.owner, approval.id, "approve")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda run_id: engine.advance(world.tenant_id, run_id, f"worker-{run_id}", transport=transport), runs))
    for run_id in runs:
        engine.advance(world.tenant_id, run_id, f"retry-{run_id}", transport=transport)
    assert len(writes) == 1


@pytest.mark.parametrize("revocation", ["policy", "connection", "owner", "emergency_stop"])
def test_revocation_during_identity_check_prevents_dispatch(proof_world, monkeypatch, revocation):
    from gluons.connectors import gmail_self_proof

    world, automation_id, connection_id = proof_world
    policy_id = grant(world, automation_id, connection_id)
    run_id = activate_and_run(world, automation_id)
    transport = httpx.MockTransport(lambda request: pytest.fail("Gmail must not be called"))
    assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "waiting_approval"
    with tenant_tx(world.tenant_id, world.user_id) as session:
        approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
        approvals.decide(session, world.owner, approval.id, "approve")

    def identity(token):
        with tenant_tx(world.tenant_id, world.user_id) as session:
            if revocation == "policy":
                policy.revoke(session, world.owner, policy_id)
            elif revocation == "connection":
                session.get(Connection, connection_id).status = "disconnected"
            elif revocation == "owner":
                session.scalar(select(Membership).where(Membership.user_id == world.user_id)).role = "admin"
            else:
                session.get(Organization, world.tenant_id).emergency_stop_at = utcnow()
        return {"sub": "synthetic-sub", "email": "owner@example.com"}

    monkeypatch.setattr(gmail_self_proof.google, "identity", identity)
    engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport)
    with tenant_tx(world.tenant_id) as session:
        receipt = session.scalar(select(ActionReceipt).where(ActionReceipt.run_id == run_id))
        assert receipt is not None and receipt.verified is False


def test_authenticated_owner_api_grant_and_approval_for_self_test(proof_world):
    world, automation_id, connection_id = proof_world
    messages = []

    def provider(request):
        if request.method == "POST":
            messages.append(json.loads(request.content)["raw"])
            return httpx.Response(200, json={"id": "synthetic-id"})
        return httpx.Response(200, json={"id": "synthetic-id", "raw": messages[0], "labelIds": ["SENT"]})

    transport = httpx.MockTransport(provider)
    with TestClient(create_app()) as client:
        login = client.post("/v1/auth/login", json={"email": world.email, "password": PASSWORD})
        assert login.status_code == 200
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers).status_code == 200
        proposal = client.post(f"/v1/automation-policies?automation_id={automation_id}", headers=headers, json={
            "mode": "supervised", "allowed_actions": [ACTION], "connection_ids": [connection_id],
            "resource_filters": {ACTION: {"allowed_recipients": ["owner@example.com"]}},
            "expires_at": (utcnow() + timedelta(hours=1)).isoformat(),
        })
        assert proposal.status_code == 201
        assert client.post(f"/v1/automation-policies/{proposal.json()['id']}/sign", headers=headers).status_code == 200
        current = client.get(f"/v1/automations/{automation_id}").json()
        activated = client.post(f"/v1/automations/{automation_id}/activate", headers=headers,
                                json={"mode": "supervised", "expected_version": current["version"]})
        assert activated.status_code == 200
        event = client.post(f"/v1/automations/{automation_id}/events", headers=headers,
                            json={"source_event_id": "synthetic-http", "source": "manual",
                                  "payload": {"to": "attacker@example.com", "body": "untrusted"}})
        assert event.status_code == 202
        run_id = event.json()["run_id"]
        assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "waiting_approval"
        assert messages == []
        with tenant_tx(world.tenant_id, world.user_id) as session:
            approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
            approval_id = approval.id
        decision = client.post(f"/v1/approvals/{approval_id}/decide", headers=headers, json={"decision": "approve"})
        assert decision.status_code == 200
        assert engine.advance(world.tenant_id, run_id, "proof-worker", transport=transport).status == "completed"
    assert len(messages) == 1
    message = BytesParser(policy=email_policy.default).parsebytes(base64.urlsafe_b64decode(messages[0]))
    assert str(message["To"]) == "owner@example.com" and "attacker" not in str(message)
