"""Provider doubles test Gluons' OAuth handling; no real Google consent is claimed."""
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from gluons import vault
from gluons.api.app import create_app
from gluons.authz import Principal, Role
from gluons.config import get_settings
from gluons.connections import google, service
from gluons.core import GluonsError, utcnow
from gluons.db.models import Automation, Connection, ConnectionSession
from gluons.db.session import tenant_tx
from helpers import World


@pytest.fixture
def browser(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_client_id", "configured-test-client")
    monkeypatch.setattr(get_settings(), "google_client_secret", "provider-double-secret")
    return replace(world.owner, session_id="signed-in-browser")


def pending(browser):
    created = service.begin_session(browser, "gmail", capabilities=["read_order_messages", "send_order_confirmations"])
    result = service.authorize(browser, created["session_id"])
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    return created["session_id"], query["state"][0], query


def provider_double(monkeypatch, scopes=None):
    calls = []

    def exchange(code, verifier):
        calls.append((code, verifier))
        return {"access_token": "never-return-this-access-token", "refresh_token": "never-return-this-refresh-token",
                "expires_in": 3600, "scope": " ".join(scopes or [google.READ_SCOPE, google.SEND_SCOPE]), "token_type": "Bearer"}

    monkeypatch.setattr(google, "exchange", exchange)
    monkeypatch.setattr(google, "identity", lambda token: {"sub": "verified-google-subject", "email": "test@example.com", "email_verified": True})
    return calls


def test_missing_credentials_is_configuration_requirement(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_client_id", None)
    result = service.begin_session(world.owner, "gmail", capabilities=["read_order_messages"])
    assert result["status"] == "needs_configuration"
    assert "Google" in result["next_step"]


def test_setup_link_is_owner_bound_and_does_not_authorize(world, browser):
    created = service.begin_session(browser, "gmail", capabilities=["read_order_messages"])
    assert service.list_connections(browser) == []
    with pytest.raises(GluonsError) as error:
        service.authorize(World.create().owner, created["session_id"])
    assert error.value.status in (403, 404)
    assistant = Principal(kind="assistant", tenant_id=world.tenant_id, user_id=world.user_id, role=Role.OWNER,
                          scopes=frozenset({"gluons.connections", "gluons.read"}))
    with pytest.raises(GluonsError, match="Gluons Web"):
        service.authorize(assistant, created["session_id"])


def test_pkce_scope_and_browser_session_binding(world, browser, monkeypatch):
    calls = provider_double(monkeypatch)
    session_id, state, query = pending(browser)
    assert query["code_challenge_method"] == ["S256"]
    assert "gmail.modify" not in query["scope"][0]
    assert len(query["code_challenge"][0]) == 43
    with pytest.raises(GluonsError, match="another session"):
        service.callback(replace(browser, session_id="another-browser"), state=state, code="code")
    result = service.callback(browser, state=state, code="code")
    assert result["status"] == "authorized" and len(calls) == 1
    assert calls[0][0] == "code" and len(calls[0][1]) >= 43
    assert "never-return" not in str(result)
    connection = service.list_connections(browser)[0]
    assert connection["account_display"] == "test@example.com"
    assert connection["account_confirmed"] is False
    assert "credential_ref" not in connection
    with tenant_tx(world.tenant_id) as session:
        oauth_session = session.get(ConnectionSession, session_id)
        with pytest.raises(GluonsError):
            vault.read(session, world.tenant_id, oauth_session.pkce_verifier_ref)
    with pytest.raises(GluonsError):
        service.callback(browser, state=state, code="replay")
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["expired", "state", "declined", "scope"])
def test_callback_rejects_expiry_tampering_decline_and_missing_scopes(world, browser, monkeypatch, fault):
    provider_double(monkeypatch, scopes=[google.READ_SCOPE] if fault == "scope" else None)
    session_id, state, _ = pending(browser)
    if fault == "expired":
        with tenant_tx(world.tenant_id) as session:
            session.get(ConnectionSession, session_id).expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(GluonsError):
        service.callback(browser, state=state + "tampered" if fault == "state" else state,
                         code=None if fault == "declined" else "code", error="access_denied" if fault == "declined" else None)
    assert service.list_connections(browser) == []
    if fault in ("declined", "scope"):
        assert service.session_status(browser, session_id)["status"] == "failed"
        with pytest.raises(GluonsError):
            service.callback(browser, state=state, code="retry")


def test_confirm_then_disconnect_fences_automation_even_if_google_revoke_fails(world, browser, monkeypatch):
    provider_double(monkeypatch)
    _, state, _ = pending(browser)
    result = service.callback(browser, state=state, code="code")
    connection_id = result["connection_id"]
    assert service.confirm_account(browser, connection_id)["account_confirmed"] is True
    automation = world.order_automation()
    with tenant_tx(world.tenant_id) as session:
        session.get(Automation, automation["id"]).config = {"connections": {"email": connection_id}}
    def refused(credential):
        raise GluonsError("EXTERNAL_UNAVAILABLE", "provider double unavailable")
    monkeypatch.setattr(google, "revoke", refused)
    response = service.disconnect(browser, connection_id)
    assert response["status"] == "disconnected"
    assert response["provider_revocation"] == "manual_provider_revocation_required"
    assert response["affected_automations"] == [automation["id"]]
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Automation, automation["id"]).status == "waiting_external"
        row = session.get(Connection, connection_id)
        assert row.credential_ref is None and row.status == "disconnected"
    assert service.disconnect(browser, connection_id)["status"] == "disconnected"


def test_personal_whatsapp_cannot_be_connected(world):
    with pytest.raises(GluonsError, match="official WhatsApp Business"):
        service.begin_session(world.owner, "whatsapp_personal", capabilities=[])


def test_http_session_requires_csrf_and_returns_no_provider_token(world):
    from helpers import PASSWORD
    with TestClient(create_app()) as client:
        login = client.post("/v1/auth/login", json={"email": world.email, "password": PASSWORD})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers)
        assert client.post("/v1/connections/sessions", json={"provider": "gmail", "capabilities": ["read_order_messages"]}).status_code == 403
        response = client.post("/v1/connections/sessions", headers=headers, json={"provider": "gmail", "capabilities": ["read_order_messages"]})
        assert response.status_code == 201
        assert "access_token" not in response.text and "client_secret" not in response.text
