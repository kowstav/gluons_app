"""An owner can set a signed, timezone-aware deadline through the policy API."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from gluons import policy
from gluons.api.app import create_app
from gluons.core import utcnow
from gluons.db.models import AutomationPolicy
from gluons.db.session import tenant_tx
from helpers import PASSWORD


def test_api_expiry_is_persisted_and_bound_to_signature(world):
    task = world.order_automation()
    expiry = (utcnow() + timedelta(hours=1)).replace(microsecond=0)
    with TestClient(create_app()) as client:
        login = client.post("/v1/auth/login", json={"email": world.email, "password": PASSWORD})
        assert login.status_code == 200
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers).status_code == 200
        body = {"mode": "supervised", "allowed_actions": ["email.send"], "expires_at": expiry.isoformat()}
        path = f"/v1/automation-policies?automation_id={task['id']}"
        proposal = client.post(path, json=body, headers=headers)
        assert proposal.status_code == 201 and datetime.fromisoformat(proposal.json()["expires_at"]) == expiry
        policy_id = proposal.json()["id"]
        signed = client.post(f"/v1/automation-policies/{policy_id}/sign", headers=headers)
        assert signed.status_code == 200 and datetime.fromisoformat(signed.json()["expires_at"]) == expiry
        body["expires_at"] = "2026-11-09T12:00:00"
        assert client.post(path, json=body, headers=headers).status_code == 422

    with tenant_tx(world.tenant_id, world.user_id) as session:
        row = session.get(AutomationPolicy, policy_id)
        assert policy.signature_valid(row)
        row.expires_at += timedelta(hours=1)
        assert not policy.signature_valid(row)
