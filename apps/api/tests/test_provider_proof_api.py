"""Exercise collector access against the real API and restricted PostgreSQL role."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from gluons.api.app import create_app
from gluons.core import new_id, utcnow
from gluons.db.models import ActionReceipt, Run
from gluons.db.session import tenant_tx
from helpers import PASSWORD, World

_spec = importlib.util.spec_from_file_location("provider_proof_api_test", Path(__file__).resolve().parents[3] / "scripts" / "provider_proof.py")
assert _spec and _spec.loader
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)


def test_collector_uses_authenticated_tenant_and_does_not_expose_receipt(world):
    other = World.create("Other")
    task = world.order_automation()
    run_id = new_id("run")
    with tenant_tx(world.tenant_id, world.user_id) as session:
        session.add(Run(id=run_id, tenant_id=world.tenant_id, automation_id=task["id"],
                        skill_id="multichannel_order_entry", skill_version="1.0.0", mode="supervised",
                        source_event_id=new_id("event"), status="completed"))
        session.add(ActionReceipt(id=new_id("receipt"), tenant_id=world.tenant_id, automation_id=task["id"],
                                  run_id=run_id, action="email.send", mode="supervised", environment="production",
                                  provider="gmail", status="succeeded", verified=True, verified_at=utcnow(),
                                  provider_object_id="private-test-id", readback={"id": "private-test-id"},
                                  idempotency_key=new_id("key"), payload_hash="a" * 64))

    def sign_in(client, owner):
        login = client.post("/v1/auth/login", json={"email": owner.email, "password": PASSWORD})
        assert login.status_code == 200
        selected = client.post(f"/v1/organizations/{owner.tenant_id}/select",
                               headers={"X-CSRF-Token": login.json()["csrf_token"]})
        assert selected.status_code == 200
        return client.cookies.get("gluons_session")

    with TestClient(create_app()) as client:
        own_cookie = sign_in(client, world)
        other_cookie = sign_in(client, other)

        def bridge(request):
            response = client.get(request.url.path, headers={"cookie": request.headers["cookie"]})
            return httpx.Response(response.status_code, content=response.content, headers={"content-type": "application/json"})

        transport = httpx.MockTransport(bridge)
        result = proof.collect("http://127.0.0.1", own_cookie, run_id, provider="gmail", action="email.send",
                               environment="production", transport=transport)
        assert result["gateway_verified"] is True and "private-test-id" not in str(result)
        with pytest.raises(httpx.HTTPStatusError) as denied:
            proof.collect("http://127.0.0.1", other_cookie, run_id, provider="gmail", action="email.send",
                          environment="production", transport=transport)
        assert denied.value.response.status_code == 404
