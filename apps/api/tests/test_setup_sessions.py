"""Resumable setup stays tenant-scoped and never grants provider authority."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from gluons import data_export, deletion, identity, setup_sessions
from gluons.api.app import create_app
from gluons.connections import service
from gluons.core import GluonsError, utcnow
from gluons.db.models import Organization
from gluons.db.session import tenant_tx
from helpers import PASSWORD


@pytest.fixture
def owner(world):
    return replace(world.owner, session_id="owner-browser")


def create(owner, key="setup-key-123"):
    return setup_sessions.start(owner, intent="business_launch", launch_plan_id=None, idempotency_key=key)


def test_idempotent_create_and_versioned_existing_connection(owner):
    initial = create(owner)
    assert create(owner) == initial
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
