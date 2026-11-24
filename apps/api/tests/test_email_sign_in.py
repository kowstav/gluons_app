"""Email code login through the real API and restricted PostgreSQL role."""
import re

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gluons import email_sign_in, identity, mail
from gluons.api.app import create_app
from gluons.config import get_settings
from gluons.core import GluonsError, new_id
from gluons.db.models import EmailSignInChallenge, User
from gluons.db.session import public_tx
from helpers import PASSWORD, make_user
from test_core_identity import last_mail

ROOT = "/v1/auth/email-code"


def new_challenge(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(f"{ROOT}/request", json={"email": email})
    assert response.status_code == 202
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    code = re.search(r"code is ([0-9]{8})", last_mail(email)["body"])[1]
    assert set(response.json()) == {"challenge_id"}
    return {"challenge_id": response.json()["challenge_id"], "email": email, "code": code}


def test_mailbox_proof_creates_one_account_and_cannot_be_moved_or_replayed():
    email = f"{new_id('otp')}@example.com"
    with TestClient(create_app()) as owner, TestClient(create_app()) as other:
        proof = new_challenge(owner, email)
        with public_tx() as session:
            assert session.scalar(select(User).where(User.email == email)) is None
            assert session.get(EmailSignInChallenge, proof["challenge_id"]) is None
        assert other.post(f"{ROOT}/verify", json=proof).status_code == 401
        browser = owner.cookies.get(email_sign_in.BROWSER_COOKIE)
        assert browser
        assert owner.post(f"{ROOT}/verify", json=proof).json()["status"] == "signed_in"
        session = owner.get("/v1/auth/session").json()
        assert session["user"]["email"] == email
        assert session["active_organization_id"] is None
        owner.cookies.set(email_sign_in.BROWSER_COOKIE, browser)
        assert owner.post(f"{ROOT}/verify", json=proof).status_code == 401


def test_attempts_persist_and_expired_challenges_are_removed(monkeypatch):
    email = f"{new_id('otp')}@example.com"
    with TestClient(create_app()) as client:
        proof = new_challenge(client, email)
        for _ in range(email_sign_in.MAX_CODE_ATTEMPTS):
            assert client.post(f"{ROOT}/verify", json={**proof, "email": "different@example.com"}).status_code == 401
        assert client.post(f"{ROOT}/verify", json=proof).status_code == 401

        monkeypatch.setattr(email_sign_in, "CODE_TTL_SECONDS", 0)
        old_id = email_sign_in.request_code(email, "synthetic-browser")
        assert email_sign_in.purge_expired() >= 1
        with public_tx() as session:
            email_sign_in._bind(session, old_id, "synthetic-browser")
            assert session.get(EmailSignInChallenge, old_id) is None


def test_existing_account_still_requires_enrolled_mfa():
    user_id, email = make_user()
    secret = identity.begin_mfa_enrollment(user_id)["secret"]
    identity.confirm_mfa_enrollment(user_id, pyotp.TOTP(secret).now())
    with TestClient(create_app()) as client:
        assert client.post(f"{ROOT}/verify", json=new_challenge(client, email)).json()["status"] == "mfa_required"
        view = client.get("/v1/auth/session").json()
        assert view["user"]["id"] == user_id and view["mfa_pending"] is True
        assert client.post("/v1/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
        assert client.get("/v1/auth/session").json()["mfa_pending"] is False


def test_mailbox_owner_does_not_activate_an_unverified_password():
    email = f"{new_id('otp')}@example.com"
    identity.register(email, PASSWORD, "Unverified registration")
    with TestClient(create_app()) as client:
        assert client.post(f"{ROOT}/verify", json=new_challenge(client, email)).status_code == 200
        assert client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401
        with public_tx() as session:
            user = session.execute(select(User).where(User.email == email)).scalar_one()
            assert user.email_verified_at is not None and user.password_hash is None


def test_pilot_mail_restricts_codes_and_transactional_transport(monkeypatch):
    allowed = f"{new_id('otp')}@example.com"
    blocked = f"{new_id('otp')}@example.com"
    monkeypatch.setattr(get_settings(), "mail_allowed_recipients", f" {allowed.upper()}, another@example.com")
    with TestClient(create_app()) as client:
        response = client.post(f"{ROOT}/request", json={"email": blocked})
        assert response.status_code == 403
        assert email_sign_in.BROWSER_COOKIE not in client.cookies
        assert client.post(f"{ROOT}/verify", json=new_challenge(client, allowed)).status_code == 200
    with pytest.raises(GluonsError, match="approved recipients"):
        mail.send(blocked, "Subject", "Message")
