"""Sign-in hardening through real HTTP handling and real PostgreSQL. No provider is involved."""
from __future__ import annotations

import threading
from datetime import timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient

from gluons import identity, net
from gluons.api.app import MAX_REQUEST_BYTES, create_app
from gluons.config import Settings, validate_for_environment
from gluons.core import GluonsError, utcnow
from gluons.db.models import IdentitySession, Membership
from gluons.db.session import public_tx, tenant_tx
from helpers import PASSWORD, World, add_member, make_user

WEB = "http://localhost:3000"


@pytest.fixture
def client(database):
    return TestClient(create_app())


def next_code(secret: str) -> str:
    return pyotp.TOTP(secret).at(utcnow() + timedelta(seconds=30))


def test_session_endpoint_returns_the_csrf_token_so_no_cookie_has_to_be_read(client):
    """Deployed, the web app is another host and cannot read the API's cookies."""
    _, email = make_user()
    login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    token = login.json()["csrf_token"]
    # A browser on another host holds only the HttpOnly session cookie.
    client.cookies.delete("gluons_csrf")
    session = client.get("/v1/auth/session")
    assert session.status_code == 200 and session.json()["csrf_token"] == token
    assert session.headers["cache-control"] == "no-store"
    assert client.post("/v1/organizations", json={"name": "Reloaded"}).status_code == 403
    created = client.post("/v1/organizations", json={"name": "Reloaded"}, headers={"X-CSRF-Token": token})
    assert created.status_code in (200, 201), created.text


def test_csrf_token_is_bound_to_its_own_session(client):
    _, first = make_user()
    _, second = make_user()
    other = TestClient(create_app())
    stolen = other.post("/v1/auth/login", json={"email": second, "password": PASSWORD}).json()["csrf_token"]
    client.post("/v1/auth/login", json={"email": first, "password": PASSWORD})
    assert client.post("/v1/organizations", json={"name": "X"}, headers={"X-CSRF-Token": stolen}).status_code == 403


def test_a_session_issued_before_derived_tokens_keeps_working_with_its_cookie(client):
    _, email = make_user()
    issued = identity.login(email, PASSWORD)
    with public_tx() as session:
        session.get(IdentitySession, issued.session_id).csrf_token_hash = "0" * 64
    assert identity.resolve_session(issued.token).csrf_token is None


def test_second_step_requires_the_csrf_header(client):
    user_id, email = make_user()
    secret = identity.begin_mfa_enrollment(user_id)["secret"]
    identity.confirm_mfa_enrollment(user_id, pyotp.TOTP(secret).now())
    login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.json()["status"] == "mfa_required"
    code = next_code(secret)
    assert client.post("/v1/auth/mfa/verify", json={"code": code}).status_code == 403
    verified = client.post("/v1/auth/mfa/verify", json={"code": code}, headers={"X-CSRF-Token": login.json()["csrf_token"]})
    assert verified.status_code == 200, verified.text


def test_a_time_based_code_is_accepted_once(database):
    user_id, email = make_user()
    secret = identity.begin_mfa_enrollment(user_id)["secret"]
    identity.confirm_mfa_enrollment(user_id, pyotp.TOTP(secret).now())
    code = next_code(secret)
    identity.verify_mfa(identity.login(email, PASSWORD).token, code)
    with pytest.raises(GluonsError, match="not valid"):
        identity.verify_mfa(identity.login(email, PASSWORD).token, code)
    # An earlier step cannot be used after a later one either.
    with pytest.raises(GluonsError, match="not valid"):
        identity.verify_mfa(identity.login(email, PASSWORD).token, pyotp.TOTP(secret).now())


def test_enrolling_keeps_this_session_and_holds_the_others_at_the_second_step(database):
    user_id, email = make_user()
    enrolling, other = identity.login(email, PASSWORD), identity.login(email, PASSWORD)
    secret = identity.begin_mfa_enrollment(user_id)["secret"]
    identity.confirm_mfa_enrollment(user_id, pyotp.TOTP(secret).now(), enrolling.session_id)
    assert identity.resolve_session(enrolling.token).mfa_pending is False
    assert identity.resolve_session(other.token).mfa_pending is True
    with pytest.raises(GluonsError, match="already on"):
        identity.confirm_mfa_enrollment(user_id, next_code(secret), other.session_id)


def test_last_seen_is_not_rewritten_on_every_request(database):
    _, email = make_user()
    issued = identity.login(email, PASSWORD)
    identity.resolve_session(issued.token)
    with public_tx() as session:
        first = session.get(IdentitySession, issued.session_id).last_seen_at
    identity.resolve_session(issued.token)
    with public_tx() as session:
        row = session.get(IdentitySession, issued.session_id)
        assert row.last_seen_at == first
        row.last_seen_at = first - identity.LAST_SEEN_RESOLUTION
    identity.resolve_session(issued.token)
    with public_tx() as session:
        assert session.get(IdentitySession, issued.session_id).last_seen_at > first - identity.LAST_SEEN_RESOLUTION


def test_two_owners_cannot_demote_each_other_at_the_same_moment(database):
    world = World.create("Owners")
    second_id, _ = make_user()
    second = add_member(world.tenant_id, second_id, "owner")
    with tenant_tx(world.tenant_id, world.user_id) as session:
        members = {m.user_id: m.id for m in session.query(Membership).filter(Membership.tenant_id == world.tenant_id)}
    start, outcomes = threading.Barrier(2), []

    def demote(principal, member_id):
        start.wait()
        try:
            identity.change_member_role(principal, member_id, "viewer")
            outcomes.append("changed")
        except GluonsError as error:
            outcomes.append(error.code)

    threads = [threading.Thread(target=demote, args=(world.owner, members[second_id])),
               threading.Thread(target=demote, args=(second, members[world.user_id]))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with tenant_tx(world.tenant_id, world.user_id) as session:
        owners = session.query(Membership).filter(Membership.tenant_id == world.tenant_id, Membership.role == "owner", Membership.removed_at.is_(None)).count()
    assert owners >= 1 and outcomes.count("changed") <= 1, outcomes


def test_api_responses_carry_protective_headers(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "strict-transport-security" not in response.headers  # only when hosted behind TLS


def test_oversized_request_is_refused_before_it_is_buffered(client):
    declared = client.post("/v1/auth/login", content=b"x" * (MAX_REQUEST_BYTES + 1), headers={"Content-Type": "application/json"})
    assert declared.status_code == 413 and declared.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    def chunks():
        for _ in range(MAX_REQUEST_BYTES // 65536 + 2):
            yield b"x" * 65536

    streamed = client.post("/v1/auth/login", content=chunks(), headers={"Content-Type": "application/json"})
    assert streamed.status_code == 413


def test_unexpected_failure_returns_the_error_envelope_with_cors_headers(client, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("database password is hunter2")

    monkeypatch.setattr(identity, "request_password_reset", broken)
    response = client.post("/v1/auth/password-reset/request", json={"email": "a@example.com"}, headers={"Origin": WEB})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in response.text
    assert response.headers["access-control-allow-origin"] == WEB
    assert response.headers["x-request-id"]


HOSTED = {"env": "production", "kms_key_name": "k", "signing_key_b64": "c2lnbmluZy1rZXktMzItYnl0ZXMtbG9uZy14eHh4eHg=",
          "public_api_url": "https://api.example.com", "public_web_url": "https://app.example.com",
          "allowed_web_origins": "https://app.example.com", "mail_transport": "smtp",
          "database_url": "postgresql+psycopg://gluons_app:s3cret@db/gluons",
          "system_database_url": "postgresql+psycopg://gluons_system:s3cret@db/gluons"}


def test_hosted_configuration_accepts_a_complete_setup():
    validate_for_environment(Settings(_env_file=None, **HOSTED))


@pytest.mark.parametrize(("override", "message"), [
    ({"allowed_web_origins": "*"}, "exact https origin"),
    ({"allowed_web_origins": "http://app.example.com"}, "exact https origin"),
    ({"allowed_web_origins": "https://app.example.com/path"}, "exact https origin"),
    ({"allowed_web_origins": ""}, "must list the web app origin"),
    ({"database_url": "postgresql+psycopg://gluons_app:gluons_app_dev@db/gluons"}, "development password"),
    ({"mail_transport": "file"}, "must deliver mail"),
    ({"session_ttl_hours": 0}, "SESSION_TTL_HOURS"),
])
def test_hosted_configuration_refuses_unsafe_values(override, message):
    with pytest.raises(RuntimeError, match=message):
        validate_for_environment(Settings(_env_file=None, **{**HOSTED, **override}))


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "224.0.0.1", "fd00::1"])  # noqa: S104 - addresses under test, never bound
def test_internal_addresses_are_never_treated_as_reachable(address):
    assert net.is_private_address(address) is True


def test_a_public_address_is_reachable():
    assert net.is_private_address("8.8.8.8") is False
    assert net.is_private_address("2001:4860:4860::8888") is False
