"""Single-use, browser-bound identity intents; no account or session is created here."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from gluons import google_identity, vault
from gluons.core import GluonsError, new_id, token_hash, utcnow
from gluons.db.models import ExternalIdentityIntent, IdentitySession, OAuthAuthorizationRequest, User
from gluons.db.session import public_tx

INTENT_TTL = timedelta(minutes=10)
LINK_SESSION_MAX_AGE = timedelta(minutes=10)
_OPAQUE = re.compile(r"^[A-Za-z0-9_-]{32,200}$")


@dataclass(frozen=True, repr=False)
class StartedIntent:
    authorization_url: str
    browser: str
    expires_at: datetime


@dataclass(frozen=True, repr=False)
class ConsumedIntent:
    purpose: str
    nonce_hash: str
    verifier: str
    user_id: str | None
    session_id: str | None
    outer_request_id: str | None


def _invalid() -> GluonsError:
    return GluonsError("IDENTITY_INTENT_INVALID", "This sign-in attempt is invalid or expired. Start again.", status=400)


def _bind(session: Session, state: str, browser: str) -> None:
    session.execute(text("SELECT set_config('app.identity_state_hash', :state, true), "
                         "set_config('app.identity_browser_hash', :browser, true)"),
                    {"state": token_hash(state), "browser": token_hash(browser)})


def _outer(session: Session, request_id: str | None) -> None:
    if request_id is None:
        return
    if not re.fullmatch(r"oar_[0-9a-f]{24}", request_id):
        raise _invalid()
    row = session.get(OAuthAuthorizationRequest, request_id)
    if row is None or row.status != "pending" or row.expires_at <= utcnow():
        raise _invalid()


def _link_session(session: Session, raw_token: str | None) -> IdentitySession:
    if not raw_token or len(raw_token) > 300:
        raise _invalid()
    row = session.scalar(select(IdentitySession).where(IdentitySession.token_hash == token_hash(raw_token)).with_for_update())
    user = session.get(User, row.user_id, with_for_update=True) if row else None
    now = utcnow()
    if (row is None or user is None or row.revoked_at is not None or row.expires_at <= now
            or row.created_at > now or row.created_at < now - LINK_SESSION_MAX_AGE
            or user.disabled_at is not None or user.email_verified_at is None
            or (user.mfa_enabled_at is not None and (row.mfa_verified_at is None or row.mfa_verified_at < user.mfa_enabled_at))):
        raise _invalid()
    return row


def begin(*, purpose: str, client_id: str, redirect_uri: str, allow_loopback: bool = False,
          session_token: str | None = None, outer_request_id: str | None = None) -> StartedIntent:
    if purpose not in ("login", "link") or (purpose == "login" and session_token is not None) or (purpose == "link" and outer_request_id is not None):
        raise _invalid()
    flow = google_identity.new_flow()
    url = google_identity.authorization_url(client_id=client_id, redirect_uri=redirect_uri, flow=flow, allow_loopback=allow_loopback)
    intent_id = new_id("eii")
    expires = utcnow() + INTENT_TTL
    sealed = vault.seal_global(f"external-identity-intent:{intent_id}", flow.verifier)
    with public_tx() as session:
        _outer(session, outer_request_id)
        current = _link_session(session, session_token) if purpose == "link" else None
        _bind(session, flow.state, flow.browser)
        session.add(ExternalIdentityIntent(id=intent_id, state_hash=token_hash(flow.state), browser_hash=token_hash(flow.browser),
            nonce_hash=token_hash(flow.nonce), verifier_sealed=sealed, purpose=purpose,
            user_id=current.user_id if current else None, session_id=current.id if current else None,
            outer_request_id=outer_request_id, expires_at=expires))
    return StartedIntent(url, flow.browser, expires)


def consume(*, state: str, browser: str, session_token: str | None = None) -> ConsumedIntent:
    if not isinstance(state, str) or not isinstance(browser, str) or not _OPAQUE.fullmatch(state) or not _OPAQUE.fullmatch(browser):
        raise _invalid()
    with public_tx() as session:
        _bind(session, state, browser)
        row = session.scalar(select(ExternalIdentityIntent).where(ExternalIdentityIntent.state_hash == token_hash(state)).with_for_update())
        if row is None or row.used_at is not None or row.expires_at <= utcnow():
            raise _invalid()
        _outer(session, row.outer_request_id)
        if row.purpose == "link":
            current = _link_session(session, session_token)
            if current.id != row.session_id or current.user_id != row.user_id:
                raise _invalid()
        elif row.purpose != "login" or session_token is not None:
            raise _invalid()
        verifier = vault.open_global(f"external-identity-intent:{row.id}", row.verifier_sealed)
        result = ConsumedIntent(row.purpose, row.nonce_hash, verifier, row.user_id, row.session_id, row.outer_request_id)
        row.used_at = utcnow()
        row.verifier_sealed = ""
        return result


def purge_expired() -> int:
    """Remove only expired intent material; a future caller must schedule this."""
    with public_tx() as session:
        session.execute(text("SELECT set_config('app.identity_cleanup', '1', true)"))
        return session.execute(delete(ExternalIdentityIntent).where(ExternalIdentityIntent.expires_at < utcnow())).rowcount
