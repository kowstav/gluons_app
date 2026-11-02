"""Account lifecycle with real token handling."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pyotp
import pytest
from sqlalchemy import select, text

from gluons import identity, vault
from gluons.config import Settings, get_settings, validate_for_environment
from gluons.core import GluonsError, new_id
from gluons.db.models import IdentitySession, User
from gluons.db.session import public_tx, tenant_tx
from helpers import PASSWORD, add_member, make_user


def last_mail(address: str) -> dict:
    folder = Path(get_settings().mail_dir)
    messages = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
    return sorted((m for m in messages if m["to"] == address), key=lambda m: m["at"])[-1]


def token_in(message: dict) -> str:
    return re.search(r"token=([\w\-]+)", message["body"]).group(1)


def test_register_verify_and_sign_in(database):
    address = f"{new_id('u')}@example.com"
    identity.register(address, PASSWORD, "New Owner")
    identity.verify_email(token_in(last_mail(address)))
    issued = identity.login(address, PASSWORD)
    assert identity.resolve_session(issued.token).email == address


def test_verification_link_works_once(database):
    address = f"{new_id('u')}@example.com"
    identity.register(address, PASSWORD, "New Owner")
    token = token_in(last_mail(address))
    identity.verify_email(token)
    with pytest.raises(GluonsError, match="invalid or has expired"):
        identity.verify_email(token)


def test_password_is_hashed_and_short_passwords_refused(database):
    with pytest.raises(GluonsError, match="at least"):
        identity.register(f"{new_id('u')}@example.com", "short", "x")
    _, address = make_user()
    with public_tx() as session:
        stored = session.execute(select(User.password_hash).where(User.email == address)).scalar_one()
    assert stored.startswith("$argon2") and PASSWORD not in stored


def test_session_token_is_stored_only_as_a_hash(database):
    _, address = make_user()
    issued = identity.login(address, PASSWORD)
    with public_tx() as session:
        hashes = session.execute(select(IdentitySession.token_hash)).scalars().all()
    assert issued.token not in hashes


def test_password_reset_revokes_every_session(database):
    _, address = make_user()
    issued = identity.login(address, PASSWORD)
    identity.request_password_reset(address)
    token = token_in(last_mail(address))
    identity.reset_password(token, "a-brand-new-passphrase")
    with pytest.raises(GluonsError):
        identity.resolve_session(issued.token)
    assert identity.login(address, "a-brand-new-passphrase").token
    with pytest.raises(GluonsError, match="invalid or has expired"):
        identity.reset_password(token, "another-new-passphrase")


def test_reset_for_unknown_address_reveals_nothing(database):
    identity.request_password_reset(f"{new_id('nobody')}@example.com")


def test_expired_session_is_rejected(database):
    _, address = make_user()
    issued = identity.login(address, PASSWORD)
    with public_tx() as session:
        session.execute(text("update identity_sessions set expires_at = now() - interval '1 minute'"))
    with pytest.raises(GluonsError, match="Sign in"):
        identity.resolve_session(issued.token)


def test_mfa_enrollment_verification_and_recovery_codes(database):
    user_id, address = make_user()
    enrollment = identity.begin_mfa_enrollment(user_id)
    secret = enrollment["secret"]
    codes = identity.confirm_mfa_enrollment(user_id, pyotp.TOTP(secret).now())
    assert len(codes) == 8

    issued = identity.login(address, PASSWORD)
    assert issued.mfa_required is True
    assert identity.resolve_session(issued.token).mfa_pending is True
    with pytest.raises(GluonsError, match="not valid"):
        identity.verify_mfa(issued.token, "000000")
    identity.verify_mfa(issued.token, pyotp.TOTP(secret).now())
    assert identity.resolve_session(issued.token).mfa_pending is False

    second = identity.login(address, PASSWORD)
    identity.verify_mfa(second.token, codes[0])
    third = identity.login(address, PASSWORD)
    with pytest.raises(GluonsError, match="not valid"):
        identity.verify_mfa(third.token, codes[0])


def test_mfa_secret_is_encrypted_at_rest(database):
    user_id, _ = make_user()
    secret = identity.begin_mfa_enrollment(user_id)["secret"]
    with public_tx() as session:
        stored = session.get(User, user_id).mfa_secret_ref
    assert secret not in stored
    assert vault.open_global(f"mfa:{user_id}", stored) == secret


def test_one_owner_runs_several_businesses_with_separate_data(database):
    user_id, _ = make_user()
    first = identity.create_organization(user_id, "Bakery")
    second = identity.create_organization(user_id, "Plumbing")
    assert {o["id"] for o in identity.list_organizations(user_id)} == {first, second}
    with tenant_tx(first, user_id) as session:
        visible = session.execute(text("select id from organizations where id = :o and deleted_at is null"), {"o": second})
        # The owner may see the organization row, but tenant data stays separate.
        assert visible.first() is not None
        assert session.execute(text("select count(*) from audit_events where tenant_id = :o"), {"o": second}).scalar_one() == 0


def test_invitation_must_match_the_invited_email(world):
    _, stranger_email = make_user()
    stranger_id, _ = make_user()
    invited = f"{new_id('u')}@example.com"
    identity.invite_member(world.owner, invited, "operator")
    token = token_in(last_mail(invited))
    with pytest.raises(GluonsError, match="invalid or has expired"):
        identity.accept_invitation(stranger_id, world.tenant_id, token)


def test_invitation_flow_and_single_use(world):
    invitee_id, invitee_email = make_user()
    identity.invite_member(world.owner, invitee_email, "operator")
    token = token_in(last_mail(invitee_email))
    identity.accept_invitation(invitee_id, world.tenant_id, token)
    assert identity.principal_for_user(invitee_id, world.tenant_id).role == "operator"
    with pytest.raises(GluonsError):
        identity.accept_invitation(invitee_id, world.tenant_id, token)


def test_admin_cannot_invite_or_promote_an_owner(world):
    user_id, _ = make_user()
    admin = add_member(world.tenant_id, user_id, "admin")
    with pytest.raises(GluonsError, match="owner"):
        identity.invite_member(admin, f"{new_id('u')}@example.com", "owner")
    target_id, _ = make_user()
    add_member(world.tenant_id, target_id, "viewer")
    member = next(m for m in identity.list_members(world.owner) if m["user_id"] == target_id)
    with pytest.raises(GluonsError, match="owner"):
        identity.change_member_role(admin, member["id"], "owner")


def test_non_member_cannot_select_an_organization(world):
    user_id, address = make_user()
    issued = identity.login(address, PASSWORD)
    with pytest.raises(GluonsError, match="not a member"):
        identity.select_organization(issued.session_id, user_id, world.tenant_id)


def test_hosted_environment_refuses_development_keys():
    with pytest.raises(RuntimeError, match="Unsafe configuration"):
        validate_for_environment(Settings(env="production", public_api_url="https://api.x", public_web_url="https://x"))
    with pytest.raises(RuntimeError, match="must be https"):
        validate_for_environment(
            Settings(env="staging", kms_key_name="k", signing_key_b64="c2lnbmluZy1rZXktMzItYnl0ZXMtbG9uZy14eHh4eHg=")
        )
