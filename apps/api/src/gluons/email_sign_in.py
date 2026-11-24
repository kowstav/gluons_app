"""Prove control of a mailbox before using the normal browser session issuer."""
from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from gluons import identity, mail, ratelimit
from gluons.config import get_settings
from gluons.core import GluonsError, constant_time_equals, hmac_sha256_hex, new_id, token_hash, utcnow
from gluons.db.models import EmailSignInChallenge, User
from gluons.db.session import public_tx

CODE_TTL_SECONDS = 600
MAX_CODE_ATTEMPTS = 5
BROWSER_COOKIE = "gluons_email_sign_in"


def _proof(label: str, value: str) -> str:
    settings = get_settings()
    return hmac_sha256_hex(settings.key_bytes(settings.signing_key_b64), f"email-sign-in:{label}:{value}")


def _bind(session: Session, challenge_id: str, browser: str) -> str:
    browser_hash = token_hash(browser)
    session.execute(text("SELECT set_config('app.email_challenge_id', :challenge, true), "
                         "set_config('app.email_browser_hash', :browser, true)"),
                    {"challenge": challenge_id, "browser": browser_hash})
    return browser_hash


def request_code(email: str, browser: str) -> str:
    address = identity._normalize_email(email)
    email_hash = _proof("address", address)
    ratelimit.hit_key("email_code_destination", email_hash)
    challenge_id = new_id("emc")
    code = f"{secrets.randbelow(100_000_000):08d}"
    with public_tx() as session:
        browser_hash = _bind(session, challenge_id, browser)
        session.add(EmailSignInChallenge(
            id=challenge_id, browser_hash=browser_hash, email_hash=email_hash,
            code_hash=_proof("code", f"{challenge_id}:{code}"),
            expires_at=utcnow() + timedelta(seconds=CODE_TTL_SECONDS),
        ))
    try:
        mail.send(address, "Your Gluons sign-in code",
                  f"Your Gluons sign-in code is {code}. It expires in 10 minutes. "
                  "Enter it on the Gluons page where you requested it.")
    except (OSError, RuntimeError, ValueError):
        raise GluonsError("EMAIL_DELIVERY_UNAVAILABLE", "Email delivery is unavailable. Try again later.", status=503) from None
    return challenge_id


def _claim_verified_user(session: Session, address: str) -> User | None:
    now = utcnow()
    display_name = address.split("@", maxsplit=1)[0][:200]
    session.execute(insert(User).values(id=new_id("usr"), email=address, display_name=display_name, email_verified_at=now)
                    .on_conflict_do_nothing(index_elements=[User.email]))
    user = session.execute(select(User).where(User.email == address).with_for_update()).scalar_one()
    if user.disabled_at is not None or (user.locked_until is not None and user.locked_until > now):
        return None
    if user.email_verified_at is None:
        # Someone may have started password registration before the mailbox owner arrived.
        user.password_hash = None
        user.display_name = display_name
        user.email_verified_at = now
    return user


def verify_code(challenge_id: str, browser: str, email: str, code: str, *,
                ip: str | None = None, user_agent: str | None = None) -> identity.IssuedSession:
    address = identity._normalize_email(email)
    invalid = GluonsError("EMAIL_CODE_INVALID", "That code is invalid or expired. Try again or request a new code.", status=401)
    with public_tx() as session:
        _bind(session, challenge_id, browser)
        row = session.get(EmailSignInChallenge, challenge_id, with_for_update=True)
        if row is None or row.used_at is not None or row.expires_at <= utcnow() or row.failed_attempts >= MAX_CODE_ATTEMPTS:
            raise invalid
        matched_email = constant_time_equals(row.email_hash, _proof("address", address))
        matched_code = constant_time_equals(row.code_hash, _proof("code", f"{challenge_id}:{code}"))
        if matched_email and matched_code:
            row.used_at = utcnow()
            user = _claim_verified_user(session, address)
            if user is not None:
                return identity._create_session(session, user, ip, user_agent)
        else:
            row.failed_attempts += 1
    # Leaving the transaction normally preserves a failed attempt or spent code.
    raise invalid


def purge_expired() -> int:
    with public_tx() as session:
        session.execute(text("SELECT set_config('app.email_challenge_cleanup', '1', true)"))
        return session.execute(delete(EmailSignInChallenge).where(EmailSignInChallenge.expires_at <= utcnow())).rowcount
