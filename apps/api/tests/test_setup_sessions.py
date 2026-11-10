"""Resumable setup stays tenant-scoped and never grants provider authority."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gluons import data_export, deletion, identity, setup_sessions, vault
from gluons.api.app import create_app
from gluons.connections import service
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import Connection, ConnectionSession, Organization
from gluons.db.session import tenant_tx
from helpers import PASSWORD


@pytest.fixture
def owner(world):
    return replace(world.owner, session_id="owner-browser")


def create(owner, key="setup-key-123"):
    return setup_sessions.start(owner, intent="business_launch", launch_plan_id=None, idempotency_key=key)


def test_setup_view_matches_published_contract_example(owner):
    path = Path(__file__).resolve().parents[3] / "docs/execution/contracts/setup-view.example.json"
    example = json.loads(path.read_text(encoding="utf-8-sig"))
    actual = create(owner)
    assert set(actual) == set(example)
    assert set(actual["next_actions"][0]) == set(example["next_actions"][0])
    assert actual["notice"] == example["notice"]


def test_idempotent_create_and_versioned_existing_connection(owner):
    initial = create(owner)
    assert create(owner) == initial
    assert setup_sessions.list_sessions(replace(owner, session_id="after-sign-in"))[0]["id"] == initial["id"]
    with pytest.raises(GluonsError):
        setup_sessions.start(owner, intent="business_launch", launch_plan_id="different", idempotency_key="setup-key-123")
    connection = service.begin_session(owner, "gmail", capabilities=["read_order_messages"])
    attached = setup_sessions.attach_connection(owner, initial["id"], connection["session_id"], 1)
    assert attached["connection"]["status"] in {"needs_configuration", "requires_external_authorization"}
    assert attached["version"] == 2
    with pytest.raises(GluonsError) as exc:
        setup_sessions.attach_connection(owner, initial["id"], connection["session_id"], 1)
    assert exc.value.status == 409
    assert setup_sessions.get(replace(owner, session_id="new-login"), initial["id"])["connection"] == attached["connection"]


def test_connection_request_is_atomic_and_idempotent(world, owner, monkeypatch):
    setup = create(owner)
    original = service.begin_session_in_transaction

    def fail_after_create(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(service, "begin_session_in_transaction", fail_after_create)
    args = {"provider": "gmail", "capabilities": ["read_messages"], "expected_version": 1, "idempotency_key": "connection-key-123"}
    with pytest.raises(RuntimeError):
        setup_sessions.request_connection(owner, setup["id"], **args)
    with tenant_tx(world.tenant_id) as session:
        assert session.scalars(select(ConnectionSession)).all() == []
    monkeypatch.setattr(service, "begin_session_in_transaction", original)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: setup_sessions.request_connection(owner, setup["id"], **args), range(2)))
    assert responses[0] == responses[1] and responses[0]["version"] == 2
    with tenant_tx(world.tenant_id) as session:
        assert len(session.scalars(select(ConnectionSession)).all()) == 1


def test_next_action_follows_account_confirmation_and_health(world, owner):
    setup = create(owner)
    requested = setup_sessions.request_connection(owner, setup["id"], provider="gmail", capabilities=["read_messages"],
                                                   expected_version=1, idempotency_key="connection-health-key")
    session_id = requested["connection"]["id"]
    with tenant_tx(world.tenant_id, world.user_id) as session:
        handoff = session.get(ConnectionSession, session_id)
        ref = vault.store(session, world.tenant_id, "gmail", {"access_token": "synthetic"})
        account = Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="gmail", environment="production",
                             external_account_id="synthetic-account", account_display="test@example.com", created_by=world.user_id,
                             credential_ref=ref, status="connected", granted_scopes=handoff.requested_scopes, last_checked_at=utcnow(),
                             metadata_json={"account_email": "test@example.com"})
        session.add(account)
        session.flush()
        handoff.connection_id, handoff.status = account.id, "authorized"
        account_id = account.id
    assert setup_sessions.get(owner, setup["id"])["next_actions"][0]["code"] == "confirm_account"
    with tenant_tx(world.tenant_id) as session:
        account = session.get(Connection, account_id)
        account.account_confirmed_at = utcnow()
        # Match the existing callback's operational Gmail scope requirement.
        account.granted_scopes = [scope for scope in account.granted_scopes if "/auth/gmail." in scope]
    assert setup_sessions.get(owner, setup["id"])["next_actions"] == []
    with tenant_tx(world.tenant_id) as session:
        account = session.get(Connection, account_id)
        required = list(account.granted_scopes)
        account.granted_scopes = []
    assert setup_sessions.get(owner, setup["id"])["next_actions"][0]["code"] == "verify_account"
    with tenant_tx(world.tenant_id) as session:
        session.get(Connection, account_id).granted_scopes = required
    with tenant_tx(world.tenant_id) as session:
        session.get(Connection, account_id).metadata_json = {"account_email": "different@example.com"}
    assert setup_sessions.get(owner, setup["id"])["next_actions"][0]["code"] == "reconnect_account"
    with tenant_tx(world.tenant_id) as session:
        session.get(Connection, account_id).status = "disconnected"
    assert setup_sessions.get(owner, setup["id"])["next_actions"][0]["code"] == "reconnect_account"
    restarted = setup_sessions.request_connection(owner, setup["id"], provider="gmail", capabilities=["read_messages"],
                                                   expected_version=2, idempotency_key="connection-restart-key")
    assert restarted["connection"]["id"] != session_id and restarted["version"] == 3


def test_resume_link_is_owner_authenticated_one_use_and_not_exported(world, owner):
    setup = create(owner)
    issued = setup_sessions.issue_resume_token(owner, setup["id"], 1)
    token = issued["resume_token"]
    other_org = identity.create_organization(world.user_id, "Other business")
    other = replace(identity.principal_for_user(world.user_id, other_org), session_id="different-web")
    with pytest.raises(GluonsError):
        setup_sessions.redeem(other, setup["id"], token)
    exported = data_export.export_page(owner, "setup_sessions")
    assert token not in str(exported) and "link_hash" not in str(exported) and "idempotency_hash" not in str(exported)
    resumed = setup_sessions.redeem(replace(owner, session_id="new-login"), setup["id"], token)
    assert resumed["id"] == setup["id"] and resumed["version"] == 3
    with pytest.raises(GluonsError):
        setup_sessions.redeem(owner, setup["id"], token)


def test_concurrent_redeem_allows_only_one(owner):
    setup = create(owner)
    token = setup_sessions.issue_resume_token(owner, setup["id"], 1)["resume_token"]
    def redeem(_):
        try:
            return setup_sessions.redeem(owner, setup["id"], token)["version"]
        except GluonsError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(redeem, range(2)))
    assert sorted(results) == [3, 409]


def test_setup_http_requires_auth_csrf_and_hides_resume_secret(world):
    with TestClient(create_app()) as client:
        assert client.post("/v1/setup-sessions", json={"intent": "business_launch"},
                           headers={"Idempotency-Key": "test-setup-key"}).status_code == 401
        login = client.post("/v1/auth/login", json={"email": world.email, "password": PASSWORD})
        headers = {"X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": "test-setup-key"}
        assert client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers).status_code == 200
        setup = client.post("/v1/setup-sessions", json={"intent": "business_launch"}, headers=headers).json()
        assert client.post(f"/v1/setup-sessions/{setup['id']}/resume-token", json={"expected_version": 1}).status_code == 403
        issue = client.post(f"/v1/setup-sessions/{setup['id']}/resume-token", json={"expected_version": 1}, headers=headers)
        assert issue.status_code == 200 and issue.headers["cache-control"] == "no-store"
        token = issue.json()["resume_token"]
        assert token not in client.get(f"/v1/setup-sessions/{setup['id']}").text
        redeem = client.post(f"/v1/setup-sessions/{setup['id']}/redeem", json={"resume_token": token}, headers=headers)
        assert redeem.status_code == 200 and redeem.headers["cache-control"] == "no-store"
        assert client.post(f"/v1/setup-sessions/{setup['id']}/redeem", json={"resume_token": token}, headers=headers).status_code == 409


def test_expired_link_can_be_replaced_and_purge_erases_setup(world, owner):
    setup = create(owner)
    token = setup_sessions.issue_resume_token(owner, setup["id"], 1)["resume_token"]
    with tenant_tx(world.tenant_id) as session:
        org = session.get(Organization, world.tenant_id)
        records = deepcopy(org.setup_sessions)
        records[setup["id"]]["link_expires_at"] = (utcnow() - timedelta(seconds=1)).isoformat()
        org.setup_sessions = records
    with pytest.raises(GluonsError):
        setup_sessions.redeem(owner, setup["id"], token)
    replacement = setup_sessions.issue_resume_token(owner, setup["id"], 2)
    assert setup_sessions.redeem(owner, setup["id"], replacement["resume_token"])["version"] == 4
    with tenant_tx(world.tenant_id) as session:
        session.get(Organization, world.tenant_id).deleted_at = utcnow()
    deletion.purge(world.tenant_id)
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Organization, world.tenant_id).setup_sessions == {}


def test_expired_setup_preserves_record_but_cannot_mint_authority(world, owner):
    setup = create(owner)
    with tenant_tx(world.tenant_id) as session:
        org = session.get(Organization, world.tenant_id)
        records = deepcopy(org.setup_sessions)
        records[setup["id"]]["expires_at"] = (utcnow() - timedelta(seconds=1)).isoformat()
        org.setup_sessions = records
    expired = setup_sessions.get(owner, setup["id"])
    assert expired["status"] == "expired" and expired["next_actions"][0]["code"] == "restart_setup"
    with pytest.raises(GluonsError):
        setup_sessions.issue_resume_token(owner, setup["id"], 1)
    replacement = create(owner, "new-setup-key-123")
    assert replacement["id"] != setup["id"]
    assert setup_sessions.remove_expired(owner, setup["id"], 1)["removed"] is True
    with pytest.raises(GluonsError):
        setup_sessions.get(owner, setup["id"])


def test_expired_setup_with_active_handoff_cannot_be_removed(world, owner):
    setup = create(owner)
    requested = setup_sessions.request_connection(owner, setup["id"], provider="gmail", capabilities=["read_messages"],
                                                   expected_version=1, idempotency_key="active-handoff-key")
    with tenant_tx(world.tenant_id) as session:
        org = session.get(Organization, world.tenant_id)
        records = deepcopy(org.setup_sessions)
        records[setup["id"]]["expires_at"] = (utcnow() - timedelta(seconds=1)).isoformat()
        org.setup_sessions = records
    with pytest.raises(GluonsError):
        setup_sessions.remove_expired(owner, setup["id"], requested["version"])
