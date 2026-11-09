"""Gmail proof readback uses synthetic provider responses and never sends mail."""
from __future__ import annotations

import base64
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from gluons import vault
from gluons.api.app import create_app
from gluons.authz import Role
from gluons.connections import gmail_proof
from gluons.connectors.gmail import GMAIL_READ_SCOPE, GmailSendConnector, SendEmail, _content, _message_id
from gluons.core import GluonsError, fingerprint, new_id, utcnow
from gluons.db.models import ActionReceipt, Connection
from gluons.db.session import tenant_tx
from gluons.net import GuardedClient
from helpers import PASSWORD, World


@pytest.fixture
def proof_case(world):
    key = new_id("key")
    payload = SendEmail(to="test-recipient@example.com", subject="Synthetic test", body="No customer data.")
    raw = base64.urlsafe_b64encode(_content(payload, key, "owner@example.com")).decode()
    with tenant_tx(world.tenant_id, world.user_id) as session:
        credential = vault.store(session, world.tenant_id, "gmail", {"access_token": "synthetic-token"})
        connection = Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="gmail", environment="production",
                                external_account_id="synthetic-subject", account_display="owner@example.com",
                                account_confirmed_at=utcnow(), granted_scopes=[GMAIL_READ_SCOPE], credential_ref=credential,
                                status="connected", created_by=world.user_id, metadata_json={"account_email": "owner@example.com"})
        session.add(connection)
        receipt = ActionReceipt(id=new_id("rcp"), tenant_id=world.tenant_id, connection_id=connection.id,
                                action="email.send", mode="supervised", environment="production", provider="gmail",
                                status="succeeded", verified=True, verified_at=utcnow(), provider_object_id="msg123",
                                receipt={"message_id": _message_id(key)}, expected=payload.model_dump(mode="json"),
                                idempotency_key=key,
                                payload_hash=fingerprint({"action": "email.send", "connection_id": connection.id,
                                                          "input": payload.model_dump(mode="json")}))
        session.add(receipt)
        receipt_id, connection_id = receipt.id, connection.id
    return replace(world.owner, session_id="synthetic-session"), receipt_id, connection_id, raw


def test_owner_reads_again_without_writes_or_private_data(proof_case):
    owner, receipt_id, _, raw = proof_case
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "msg123", "raw": raw, "labelIds": ["SENT"]})

    result = gmail_proof.readback(owner, receipt_id, transport=httpx.MockTransport(provider))
    assert len(calls) == 1 and calls[0].method == "GET"
    assert result["found"] is True and result["matches"] is True and result["receipt_modified"] is False
    assert result["scope"] == "sent_mail_readback_not_delivery"
    assert "msg123" not in str(result) and "test-recipient" not in str(result) and raw not in str(result)
    with tenant_tx(owner.tenant_id, owner.user_id) as session:
        assert session.get(ActionReceipt, receipt_id).verified is True


def test_mismatch_stays_observation_not_receipt_mutation(proof_case):
    owner, receipt_id, _, raw = proof_case
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"id": "msg123", "raw": raw, "labelIds": []}))
    result = gmail_proof.readback(owner, receipt_id, transport=transport)
    assert result["matches"] is False and result["receipt_modified"] is False
    with tenant_tx(owner.tenant_id, owner.user_id) as session:
        assert session.get(ActionReceipt, receipt_id).verified is True


def test_wrong_provider_message_id_cannot_match(proof_case):
    owner, receipt_id, _, raw = proof_case
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"id": "other", "raw": raw, "labelIds": ["SENT"]}))
    assert gmail_proof.readback(owner, receipt_id, transport=transport)["matches"] is False


def test_http_route_requires_owner_session_and_returns_sanitized_result(world, proof_case, monkeypatch):
    _, receipt_id, _, raw = proof_case
    provider = httpx.MockTransport(lambda request: httpx.Response(200, json={"id": "msg123", "raw": raw, "labelIds": ["SENT"]}))
    monkeypatch.setattr(GmailSendConnector, "client", lambda self, ctx: GuardedClient(("gmail.googleapis.com",), transport=provider))
    with TestClient(create_app()) as client:
        path = f"/v1/proofs/gmail/receipts/{receipt_id}/readback"
        assert client.get(path).status_code == 401
        login = client.post("/v1/auth/login", json={"email": world.email, "password": PASSWORD})
        assert login.status_code == 200
        assert client.post(f"/v1/organizations/{world.tenant_id}/select",
                           headers={"X-CSRF-Token": login.json()["csrf_token"]}).status_code == 200
        response = client.get(path)
        assert response.status_code == 200 and response.json()["matches"] is True
        assert "msg123" not in response.text and "test-recipient" not in response.text and raw not in response.text


def test_other_tenant_non_owner_and_revoked_connection_are_denied(world, proof_case):
    owner, receipt_id, connection_id, _ = proof_case
    other = World.create("Other")
    transport = httpx.MockTransport(lambda request: pytest.fail("provider must not be called"))
    with pytest.raises(GluonsError) as missing:
        gmail_proof.readback(replace(other.owner, session_id="other-session"), receipt_id, transport=transport)
    assert missing.value.status == 404
    with pytest.raises(GluonsError) as forbidden:
        gmail_proof.readback(replace(owner, role=Role.ADMIN), receipt_id, transport=transport)
    assert forbidden.value.status == 403
    with tenant_tx(world.tenant_id, world.user_id) as session:
        session.get(Connection, connection_id).status = "disconnected"
    with pytest.raises(GluonsError) as disconnected:
        gmail_proof.readback(owner, receipt_id, transport=transport)
    assert disconnected.value.status == 409


def test_environment_mismatch_never_queries_provider(world, proof_case):
    owner, receipt_id, connection_id, _ = proof_case
    with tenant_tx(world.tenant_id, world.user_id) as session:
        session.get(Connection, connection_id).environment = "simulator"
    with pytest.raises(GluonsError) as error:
        gmail_proof.readback(owner, receipt_id, transport=httpx.MockTransport(lambda request: pytest.fail("provider must not be called")))
    assert error.value.status == 409


def test_tampered_payload_never_queries_provider(world, proof_case):
    owner, receipt_id, _, _ = proof_case
    transport = httpx.MockTransport(lambda request: pytest.fail("provider must not be called"))
    with tenant_tx(world.tenant_id, world.user_id) as session:
        session.get(ActionReceipt, receipt_id).expected = {"to": "test-recipient@example.com", "subject": "Changed", "body": "No customer data."}
    with pytest.raises(GluonsError) as error:
        gmail_proof.readback(owner, receipt_id, transport=transport)
    assert error.value.status == 409


def test_tampered_message_id_never_queries_provider(world, proof_case):
    owner, receipt_id, _, _ = proof_case
    with tenant_tx(world.tenant_id, world.user_id) as session:
        session.get(ActionReceipt, receipt_id).receipt = {"message_id": "wrong"}
    with pytest.raises(GluonsError) as error:
        gmail_proof.readback(owner, receipt_id, transport=httpx.MockTransport(lambda request: pytest.fail("provider must not be called")))
    assert error.value.status == 409
