"""Internal verified-subject linking; no provider or public route is enabled here."""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from gluons import external_identity_intents, google_identity, identity
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import ExternalIdentityLink, User
from gluons.db.session import public_tx


def _invalid() -> GluonsError:
    return GluonsError("IDENTITY_LINK_INVALID", "The identity could not be linked or used. Review your sign-in options.", status=409)


def link(intent: external_identity_intents.ConsumedIntent, verified: google_identity.VerifiedIdentity, *, session_token: str) -> None:
    """Link only after a consumed linking intent and fresh provider verification."""
    if intent.purpose != "link" or not intent.user_id or not intent.session_id or verified.issuer != "https://accounts.google.com" or not verified.subject:
        raise _invalid()
    try:
        with public_tx() as session:
            current = external_identity_intents._link_session(session, session_token)
            if current.id != intent.session_id or current.user_id != intent.user_id:
                raise _invalid()
            session.execute(text("SELECT set_config('app.user_id', :user_id, true), set_config('app.identity_issuer', :issuer, true), "
                                 "set_config('app.identity_subject', :subject, true)"),
                            {"user_id": current.user_id, "issuer": verified.issuer, "subject": verified.subject})
            existing = session.scalar(select(ExternalIdentityLink).where(ExternalIdentityLink.issuer == verified.issuer,
                ExternalIdentityLink.subject == verified.subject).with_for_update())
            if existing is not None:
                if existing.user_id != current.user_id:
                    raise _invalid()
                existing.revoked_at = None
            else:
                session.add(ExternalIdentityLink(id=new_id("eil"), user_id=current.user_id,
                                               issuer=verified.issuer, subject=verified.subject))
            session.flush()
    except IntegrityError as exc:
        raise _invalid() from exc


def sign_in(verified: google_identity.VerifiedIdentity, *, ip: str | None = None, user_agent: str | None = None) -> identity.IssuedSession:
    """Sign in only to an explicitly linked active account; never match by email."""
    if verified.issuer != "https://accounts.google.com" or not verified.subject:
        raise _invalid()
    with public_tx() as session:
        session.execute(text("SELECT set_config('app.identity_issuer', :issuer, true), set_config('app.identity_subject', :subject, true)"),
                        {"issuer": verified.issuer, "subject": verified.subject})
        linked = session.scalar(select(ExternalIdentityLink).where(ExternalIdentityLink.issuer == verified.issuer,
            ExternalIdentityLink.subject == verified.subject))
        if linked is None or linked.revoked_at is not None:
            raise _invalid()
        user = session.get(User, linked.user_id, with_for_update=True)
        if user is None or user.disabled_at is not None or user.email_verified_at is None or (user.locked_until and user.locked_until > utcnow()):
            raise _invalid()
        session.execute(text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": user.id})
        linked = session.scalar(select(ExternalIdentityLink).where(ExternalIdentityLink.id == linked.id).with_for_update())
        if linked is None or linked.revoked_at is not None:
            raise _invalid()
        linked.last_authenticated_at = utcnow()
        return identity._create_session(session, user, ip, user_agent)


def unlink(*, session_token: str) -> None:
    """Do not remove the only implemented sign-in method for an account."""
    with public_tx() as session:
        current = external_identity_intents._link_session(session, session_token)
        user = session.get(User, current.user_id, with_for_update=True)
        if user is None or not user.password_hash or user.email_verified_at is None:
            raise _invalid()
        session.execute(text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": user.id})
        row = session.scalar(select(ExternalIdentityLink).where(ExternalIdentityLink.user_id == user.id,
            ExternalIdentityLink.issuer == "https://accounts.google.com").with_for_update())
        if row is None or row.revoked_at is not None:
            raise _invalid()
        row.revoked_at = utcnow()
