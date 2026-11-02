"""Real PostgreSQL assistant authorization; no external host is simulated as verified."""
from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from gluons.api.app import create_app
from gluons.core import GluonsError, utcnow
from gluons.db.models import Membership, OAuthAuthorizationRequest, OAuthToken, User
from gluons.db.session import public_tx, tenant_tx
from gluons.oauth import service
from helpers import PASSWORD, World

VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
REDIRECT = "https://assistant.example/oauth/callback?source=gluons"


def authorization(world, *, scopes="gluons.read gluons.plan gluons.tasks"):
    client = service.register_client({"client_name": "Test assistant", "redirect_uris": [REDIRECT]})
    pending = service.begin_authorization(client_id=client["client_id"], redirect_uri=REDIRECT,
                                          code_challenge=CHALLENGE, resource_uri=service.resource(),
                                          state="owner-generated-csrf-state", scope=scopes)
    return client, pending


def issue(world, *, scopes="gluons.read gluons.plan gluons.tasks"):
    client, pending = authorization(world, scopes=scopes)
    decision = service.consent(world.owner, pending["request_id"], approve=True)
    query = parse_qs(urlsplit(decision["redirect_url"]).query)
    pair = service.exchange_code(client_id=client["client_id"], code=query["code"][0], redirect_uri=REDIRECT,
                                 code_verifier=VERIFIER, resource_uri=service.resource())
    return client, pending, pair


def test_consent_preserves_state_and_does_not_grant_provider_or_policy_rights(world):
    client, pending = authorization(world)
    preview = service.consent_preview(world.owner, pending["request_id"])
    assert preview["tenant_id"] == world.tenant_id and preview["client_id"] == client["client_id"]
    result = service.consent(world.owner, pending["request_id"], approve=True)
    query = parse_qs(urlsplit(result["redirect_url"]).query)
    assert query["state"] == ["owner-generated-csrf-state"] and query["iss"] == [service.issuer()]
    assert query["source"] == ["gluons"]
    pair = service.exchange_code(client_id=client["client_id"], code=query["code"][0], redirect_uri=REDIRECT,
                                 code_verifier=VERIFIER, resource_uri=service.resource())
    principal = service.resolve_access_token(pair["access_token"])
    assert principal.tenant_id == world.tenant_id
    assert principal.can("automations:write")
    assert not principal.can("policies:sign") and not principal.can("billing:manage")
    assert not principal.can("connections:manage")
    with tenant_tx(world.tenant_id) as session:
        hashes = [row.token_hash for row in session.execute(select(OAuthToken)).scalars()]
    assert pair["access_token"] not in hashes and pair["refresh_token"] not in hashes


@pytest.mark.parametrize("changes,code", [
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"code_challenge": "not-a-sha256-challenge"}, "invalid_request"),
    ({"redirect_uri": "https://attacker.example/callback"}, "invalid_request"),
    ({"resource_uri": "https://other.example/mcp"}, "invalid_target"),
    ({"scope": "gluons.root"}, "invalid_scope"),
])
def test_authorization_rejects_pkce_redirect_scope_and_audience_tampering(world, changes, code):
    client = service.register_client({"redirect_uris": [REDIRECT]})
    args = {"client_id": client["client_id"], "redirect_uri": REDIRECT,
            "code_challenge": CHALLENGE, "resource_uri": service.resource(), **changes}
    with pytest.raises(service.OAuthError) as error:
        service.begin_authorization(**args)
    assert error.value.code == code


def test_code_is_single_use_and_bad_verifier_cannot_redeem_it(world):
    client, pending = authorization(world)
    redirect = service.consent(world.owner, pending["request_id"], approve=True)["redirect_url"]
    code = parse_qs(urlsplit(redirect).query)["code"][0]
    args = {"client_id": client["client_id"], "code": code, "redirect_uri": REDIRECT, "resource_uri": service.resource()}
    with pytest.raises(service.OAuthError, match="verifier"):
        service.exchange_code(**args, code_verifier="x" * 64)
    service.exchange_code(**args, code_verifier=VERIFIER)
    with pytest.raises(service.OAuthError):
        service.exchange_code(**args, code_verifier=VERIFIER)
    with pytest.raises(service.OAuthError):
        service.consent(world.owner, pending["request_id"], approve=True)


def test_expired_request_or_code_never_links(world):
    client, pending = authorization(world)
    with public_tx() as session:
        session.execute(update(OAuthAuthorizationRequest).where(OAuthAuthorizationRequest.id == pending["request_id"])
                        .values(expires_at=utcnow() - timedelta(seconds=1)))
    with pytest.raises(service.OAuthError):
        service.consent(world.owner, pending["request_id"], approve=True)
    client, pending = authorization(world)
    redirect = service.consent(world.owner, pending["request_id"], approve=True)["redirect_url"]
    with public_tx() as session:
        session.execute(update(OAuthAuthorizationRequest).where(OAuthAuthorizationRequest.id == pending["request_id"])
                        .values(code_expires_at=utcnow() - timedelta(seconds=1)))
    with pytest.raises(service.OAuthError):
        service.exchange_code(client_id=client["client_id"], code=parse_qs(urlsplit(redirect).query)["code"][0],
                              redirect_uri=REDIRECT, code_verifier=VERIFIER, resource_uri=service.resource())


def test_one_assistant_can_be_revoked_without_revoking_another(world):
    _, _, first = issue(world)
    _, _, second = issue(world)
    binding = service.resolve_access_token(first["access_token"]).binding_id
    service.revoke_binding(world.owner, binding)
    with pytest.raises(GluonsError, match="invalid|revoked"):
        service.resolve_access_token(first["access_token"])
    assert service.resolve_access_token(second["access_token"]).tenant_id == world.tenant_id


def test_refresh_rotation_reuse_persists_revocation_of_the_whole_family(world):
    client, _, first = issue(world)
    second = service.refresh_tokens(client_id=client["client_id"], refresh_token=first["refresh_token"],
                                     resource_uri=service.resource(), scope="gluons.read")
    assert second["refresh_token"] != first["refresh_token"] and second["scope"] == "gluons.read"
    assert not service.resolve_access_token(second["access_token"]).can("automations:write")
    with pytest.raises(service.OAuthError, match="reuse"):
        service.refresh_tokens(client_id=client["client_id"], refresh_token=first["refresh_token"], resource_uri=service.resource())
    for pair in (first, second):
        with pytest.raises(GluonsError):
            service.resolve_access_token(pair["access_token"])
    with pytest.raises(service.OAuthError):
        service.refresh_tokens(client_id=client["client_id"], refresh_token=second["refresh_token"], resource_uri=service.resource())


def test_refresh_scope_cannot_escalate(world):
    client, _, pair = issue(world, scopes="gluons.read")
    with pytest.raises(service.OAuthError, match="increase"):
        service.refresh_tokens(client_id=client["client_id"], refresh_token=pair["refresh_token"],
                               resource_uri=service.resource(), scope="gluons.read gluons.activate")


def test_access_expiry_and_token_revocation_are_enforced(world):
    client, _, pair = issue(world)
    with tenant_tx(world.tenant_id) as session:
        session.execute(update(OAuthToken).where(OAuthToken.token_hash == service.token_hash(pair["access_token"]))
                        .values(expires_at=utcnow() - timedelta(seconds=1)))
    with pytest.raises(GluonsError):
        service.resolve_access_token(pair["access_token"])
    service.revoke_token(client_id=client["client_id"], token=pair["refresh_token"])
    with pytest.raises(service.OAuthError):
        service.refresh_tokens(client_id=client["client_id"], refresh_token=pair["refresh_token"], resource_uri=service.resource())


def test_live_membership_and_user_state_narrow_or_remove_authority(world):
    _, _, pair = issue(world)
    with tenant_tx(world.tenant_id) as session:
        session.execute(update(Membership).where(Membership.user_id == world.user_id).values(role="viewer"))
    assert not service.resolve_access_token(pair["access_token"]).can("automations:write")
    with public_tx() as session:
        session.execute(update(User).where(User.id == world.user_id).values(disabled_at=utcnow()))
    with pytest.raises(GluonsError):
        service.resolve_access_token(pair["access_token"])


def test_revocation_is_tenant_isolated_and_consent_cannot_expand_scope(world):
    other = World.create()
    _, _, pair = issue(world)
    binding = service.resolve_access_token(pair["access_token"]).binding_id
    with pytest.raises(GluonsError) as error:
        service.revoke_binding(other.owner, binding)
    assert error.value.code == "NOT_FOUND"
    _, pending = authorization(world, scopes="gluons.read")
    with pytest.raises(service.OAuthError):
        service.consent(world.owner, pending["request_id"], approve=True, scopes=["gluons.approvals"])
    with pytest.raises(service.OAuthError):
        service.consent(world.owner, pending["request_id"], approve=True, approval_max_tier="R4")


def test_denial_and_unverified_user_do_not_create_bindings(world):
    _, pending = authorization(world)
    result = service.consent(world.owner, pending["request_id"], approve=False)
    assert parse_qs(urlsplit(result["redirect_url"]).query)["error"] == ["access_denied"]
    assert service.list_bindings(world.owner) == []
    with public_tx() as session:
        session.execute(update(User).where(User.id == world.user_id).values(email_verified_at=None))
    _, pending = authorization(world)
    with pytest.raises(GluonsError, match="Verify"):
        service.consent(world.owner, pending["request_id"], approve=True)


def test_public_http_metadata_and_token_errors_use_oauth_contract(database):
    with TestClient(create_app()) as client:
        metadata = client.get("/.well-known/oauth-authorization-server")
        assert metadata.status_code == 200
        assert metadata.json()["code_challenge_methods_supported"] == ["S256"]
        assert "none" in metadata.json()["token_endpoint_auth_methods_supported"]
        response = client.post("/oauth/token", data={"grant_type": "password", "client_id": "unregistered"})
        assert response.status_code == 400 and response.json()["error"] == "unsupported_grant_type"
        assert response.headers["cache-control"] == "no-store"
        duplicate = client.post("/oauth/token", content="client_id=one&client_id=two", headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert duplicate.status_code == 400


def test_browser_consent_requires_session_csrf_and_explicit_click(world):
    _, pending = authorization(world)
    path = f"/v1/assistants/consent/{pending['request_id']}"
    with TestClient(create_app()) as client:
        assert client.get(path).status_code == 401
        login = client.post("/v1/auth/login", json={"email": world.email, "password": PASSWORD})
        csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
        client.post(f"/v1/organizations/{world.tenant_id}/select", headers=csrf)
        assert client.get(path).status_code == 200
        assert service.list_bindings(world.owner) == []
        assert client.post(path, json={"approve": True}).status_code == 403
        result = client.post(path, json={"approve": True}, headers=csrf)
        assert result.status_code == 200 and "code=" in result.json()["redirect_url"]


@pytest.mark.parametrize("uri", ["http://public.example/callback", "https://user:password@example.com/cb", "https://a.example/*", "https://a.example/cb#x"])
def test_unsafe_registration_redirects_are_refused(database, uri):
    with pytest.raises(service.OAuthError):
        service.register_client({"redirect_uris": [uri]})


def test_unapproved_cimd_origin_never_performs_network_request(database, monkeypatch):
    monkeypatch.setenv("GLUONS_OAUTH_CIMD_HOSTS", "approved.example")
    def forbidden_lookup(*args, **kwargs):
        pytest.fail("Unapproved metadata must not perform DNS resolution")
    monkeypatch.setattr("gluons.oauth.service.socket.getaddrinfo", forbidden_lookup)
    with pytest.raises(service.OAuthError, match="not approved"):
        service.begin_authorization(client_id="https://127.0.0.1/metadata.json", redirect_uri=REDIRECT,
                                     code_challenge=CHALLENGE, resource_uri=service.resource())
