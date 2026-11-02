"""Audit findings: second-factor guessing, unauthenticated OAuth growth, urgent mail delivery."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from gluons import events, identity
from gluons.api.app import create_app
from gluons.config import get_settings
from gluons.core import GluonsError, utcnow
from gluons.db.models import IdentitySession, Notification, OAuthAuthorizationRequest, OAuthClient, User
from gluons.db.session import public_tx, system_tx, tenant_tx
from gluons.oauth import service as oauth
from helpers import PASSWORD, World, add_member, make_user
from test_api_foundation import signed_in_org
from test_assistant_oauth import REDIRECT, authorization


def enrolled_user() -> tuple[str, str, str]:
    user_id, email = make_user()
    secret = identity.begin_mfa_enrollment(user_id)["secret"]
    identity.confirm_mfa_enrollment(user_id, pyotp.TOTP(secret).now())
    return user_id, email, secret


def sign_in(email: str):
    return identity.login(email, PASSWORD)


def wrong_code(secret: str) -> str:
    return "000000" if pyotp.TOTP(secret).now() != "000000" else "111111"


def test_second_factor_guessing_locks_the_account_and_ends_the_session():
    user_id, email, secret = enrolled_user()
    issued = sign_in(email)
    for _ in range(identity.MAX_FAILED_LOGINS - 1):
        with pytest.raises(GluonsError, match="not valid"):
            identity.verify_mfa(issued.token, wrong_code(secret))
    with pytest.raises(GluonsError, match="not valid"):
        identity.verify_mfa(issued.token, wrong_code(secret))
    with pytest.raises(GluonsError) as locked:
        sign_in(email)
    assert locked.value.code == "ACCOUNT_LOCKED"
    with public_tx() as session:
        assert session.execute(select(IdentitySession.revoked_at).where(IdentitySession.user_id == user_id)).scalar() is not None


def test_signing_in_again_does_not_reset_the_second_factor_count():
    user_id, email, secret = enrolled_user()
    for _ in range(identity.MAX_FAILED_LOGINS - 1):
        issued = sign_in(email)
        with pytest.raises(GluonsError, match="not valid"):
            identity.verify_mfa(issued.token, wrong_code(secret))
    with public_tx() as session:
        assert session.get(User, user_id).failed_logins == identity.MAX_FAILED_LOGINS - 1


def test_a_correct_code_clears_the_count_and_a_recovery_code_works_once():
    user_id, email, secret = enrolled_user()
    issued = sign_in(email)
    with pytest.raises(GluonsError):
        identity.verify_mfa(issued.token, wrong_code(secret))
    identity.verify_mfa(issued.token, pyotp.TOTP(secret).now())
    with public_tx() as session:
        assert session.get(User, user_id).failed_logins == 0


def test_abandoned_consent_and_registrations_are_purged_but_a_live_client_stays(world):
    client, pending = authorization(world)
    stale = oauth.register_client({"client_name": "Abandoned", "redirect_uris": [REDIRECT]})
    long_ago = utcnow() - timedelta(days=3)
    with public_tx() as session:
        session.execute(update(OAuthClient).where(OAuthClient.id == stale["client_id"]).values(created_at=long_ago))
        session.execute(update(OAuthAuthorizationRequest).where(OAuthAuthorizationRequest.id == pending["request_id"]).values(expires_at=long_ago))
    oauth.purge_stale()
    with public_tx() as session:
        assert session.get(OAuthClient, stale["client_id"]) is None
        assert session.get(OAuthAuthorizationRequest, pending["request_id"]) is None
        assert session.get(OAuthClient, client["client_id"]) is not None


def test_a_client_cannot_flood_pending_consent(world, monkeypatch):
    monkeypatch.setattr(oauth, "MAX_PENDING_PER_CLIENT", 2)
    client, _ = authorization(world)
    from test_assistant_oauth import CHALLENGE
    start = dict(client_id=client["client_id"], redirect_uri=REDIRECT, code_challenge=CHALLENGE, resource_uri=oauth.resource())
    oauth.begin_authorization(**start)
    with pytest.raises(oauth.OAuthError) as refused:
        oauth.begin_authorization(**start)
    assert refused.value.code == "temporarily_unavailable"


def mail_for(address: str) -> list[dict]:
    folder = Path(get_settings().mail_dir)
    messages = [json.loads(path.read_text(encoding="utf-8")) for path in folder.glob("*.json")]
    return [message for message in messages if message.get("to") == address]


def notify(world, severity: str = "action") -> str:
    with tenant_tx(world.tenant_id) as session:
        events.notify(session, world.tenant_id, kind="safety_escalation", title="Urgent: a person must respond now", severity=severity, link="/runs/run_1")
        session.flush()
    with tenant_tx(world.tenant_id) as session:
        return session.execute(select(Notification.id).order_by(Notification.created_at.desc()).limit(1)).scalar_one()


def owner_email(world) -> str:
    with public_tx() as session:
        return session.get(User, world.user_id).email


def test_urgent_notifications_reach_the_owner_once_and_info_stays_in_the_app(world):
    user_id, _ = make_user()
    add_member(world.tenant_id, user_id, "viewer")
    address = owner_email(world)
    notify(world)
    events.deliver_urgent()
    events.deliver_urgent()
    delivered = [m for m in mail_for(address) if "Urgent" in m["subject"]]
    assert len(delivered) == 1 and "/runs/run_1" in delivered[0]["body"]
    with public_tx() as session:
        viewer = session.get(User, user_id).email
    assert [m for m in mail_for(viewer) if "Urgent" in m["subject"]] == []
    other = World.create("Quiet Business")
    notify(other, "info")
    events.deliver_urgent()
    assert [m for m in mail_for(owner_email(other)) if "Urgent" in m["subject"]] == []


def test_a_failed_send_stays_pending_and_later_rows_are_not_lost(world, monkeypatch):
    notification_id = notify(world)
    monkeypatch.setattr(events.mail, "send", lambda *a, **k: (_ for _ in ()).throw(OSError("smtp down")))
    with pytest.raises(OSError):
        events.deliver_urgent()
    with system_tx() as session:
        assert session.get(Notification, notification_id).delivered_at is None
    monkeypatch.undo()
    assert events.deliver_urgent() >= 1


def test_message_and_order_lists_are_tenant_scoped_and_label_untrusted_text(world, erp):
    client = TestClient(create_app())
    assert client.get("/v1/messages").status_code == 401
    headers = signed_in_org(client, world)
    response = client.get("/v1/messages", headers=headers)
    assert response.status_code == 200 and response.json()["trust"] == "untrusted_customer_input"
    assert client.get("/v1/orders", headers=headers).json() == {"orders": []}
