"""Setup measurements are bounded, owner-scoped and contain no entered values."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gluons import identity, setup_sessions
from gluons.api.app import create_app
from gluons.authz import Role
from gluons.core import GluonsError, utcnow
from gluons.db.models import AuditEvent, Organization
from gluons.db.session import tenant_tx
from helpers import PASSWORD


@pytest.fixture
def owner(world):
    return replace(world.owner, session_id="owner-browser")


def create(owner):
    return setup_sessions.start(owner, intent="business_launch", launch_plan_id=None, idempotency_key="measurement-test-key")


def test_measurements_record_only_aggregate_estimates(world, owner):
    setup = create(owner)
    setup_sessions.record_ui_measurement(owner, setup["id"], kind="active_seconds", seconds=42)
    setup_sessions.record_ui_measurement(owner, setup["id"], kind="help_opened")
    with tenant_tx(world.tenant_id) as session:
        rows = session.scalars(select(AuditEvent).where(AuditEvent.category == "setup_measurement").order_by(AuditEvent.seq)).all()
        assert [(row.event_type, row.data) for row in rows] == [
            ("setup.active_seconds", {"basis": "client_estimated", "seconds": 42}),
            ("setup.help_opened", {"basis": "client_estimated"}),
        ]


@pytest.mark.parametrize("kind,seconds", [("active_seconds", 0), ("active_seconds", 301), ("active_seconds", True),
                                           ("active_seconds", None), ("help_opened", 10), ("completion", None)])
def test_invalid_or_unverified_measurements_rejected(owner, kind, seconds):
    setup = create(owner)
    with pytest.raises(GluonsError):
        setup_sessions.record_ui_measurement(owner, setup["id"], kind=kind, seconds=seconds)


def test_measurement_requires_correct_owner_and_business(world, owner):
    setup = create(owner)
    other = identity.create_organization(world.user_id, "Another business")
    other_owner = replace(identity.principal_for_user(world.user_id, other), session_id="owner-browser")
    for principal in (other_owner, replace(owner, role=Role.ADMIN), world.owner):
        with pytest.raises(GluonsError):
            setup_sessions.record_ui_measurement(principal, setup["id"], kind="help_opened")


def test_expired_setup_does_not_accept_measurements(world, owner):
    setup = create(owner)
    with tenant_tx(world.tenant_id) as session:
        org = session.get(Organization, world.tenant_id)
        records = deepcopy(org.setup_sessions)
        records[setup["id"]]["expires_at"] = (utcnow() - timedelta(seconds=1)).isoformat()
        org.setup_sessions = records
    with pytest.raises(GluonsError):
        setup_sessions.record_ui_measurement(owner, setup["id"], kind="help_opened")


def test_measurement_http_rejects_extra_content(world):
    with TestClient(create_app()) as client:
        login = client.post("/v1/auth/login", json={"email": world.email, "password": PASSWORD})
        headers = {"X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": "measurement-http-key"}
        assert client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers).status_code == 200
        setup = client.post("/v1/setup-sessions", json={"intent": "business_launch"}, headers=headers).json()
        path = f"/v1/setup-sessions/{setup['id']}/measurements"
        assert client.post(path, headers=headers, json={"kind": "help_opened", "field_value": "must-not-be-stored"}).status_code == 422
        assert client.post(path, headers=headers, json={"kind": "active_seconds", "seconds": True}).status_code == 422
        assert client.post(path, headers=headers, json={"kind": "active_seconds", "seconds": 5}).json()["basis"] == "client_estimated"
