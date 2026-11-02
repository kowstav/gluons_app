"""Accounts, browser sessions, organizations and membership.

The Gluons sign-in is the identity authority. A chat id, an unverified email address or
a URL parameter is never proof of who someone is.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from gluons import audit, mail, vault
from gluons.authz import Principal, Role
from gluons.config import get_settings
from gluons.core import (
    GluonsError,
    auth_required,
    conflict,
    constant_time_equals,
    forbidden,
    new_id,
    new_secret_token,
    not_found,
    token_hash,
    utcnow,
    validation_failed,
)
from gluons.db.models import (
    IdentitySession,
    Invitation,
    Membership,
    Organization,
    User,
    UserToken,
)
from gluons.db.session import public_tx, tenant_tx, user_tx

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("gluons-timing-equalizer")
MAX_FAILED_LOGINS = 8
LOCKOUT = timedelta(minutes=15)
MIN_PASSWORD_LENGTH = 12


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise validation_failed(f"Use at least {MIN_PASSWORD_LENGTH} characters.", field="password")


def _issue_user_token(session: Session, user_id: str, kind: str, ttl: timedelta) -> str:
    raw = new_secret_token()
    session.add(
        UserToken(
            id=new_id("utk"), user_id=user_id, kind=kind, token_hash=token_hash(raw), expires_at=utcnow() + ttl
        )
    )
    return raw


def _consume_user_token(session: Session, raw: str, kind: str) -> UserToken:
    row = session.execute(
        select(UserToken).where(UserToken.token_hash == token_hash(raw), UserToken.kind == kind).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        raise GluonsError("INVALID_TOKEN", "This link is invalid or has expired.", status=400)
    row.used_at = utcnow()
    return row


def register(email: str, password: str, display_name: str) -> None:
    """Same response whether or not the address exists, so accounts cannot be enumerated."""
    _check_password_strength(password)
    address = _normalize_email(email)
    web = get_settings().public_web_url
    with public_tx() as session:
        existing = session.execute(select(User).where(User.email == address)).scalar_one_or_none()
        if existing is not None:
            mail.send(address, "Your Gluons account", "Someone tried to sign up with this address. You already have an account.")
            return
        user = User(
            id=new_id("usr"), email=address, password_hash=_hasher.hash(password), display_name=display_name.strip()
        )
        session.add(user)
        session.flush()
        raw = _issue_user_token(session, user.id, "verify_email", timedelta(hours=24))
    mail.send(address, "Verify your Gluons email", f"Confirm your address: {web}/verify-email?token={raw}")


def verify_email(raw_token: str) -> None:
    with public_tx() as session:
        token = _consume_user_token(session, raw_token, "verify_email")
        session.execute(update(User).where(User.id == token.user_id).values(email_verified_at=utcnow()))


@dataclass(frozen=True)
class IssuedSession:
    session_id: str
    user_id: str
    token: str
    csrf_token: str
    mfa_required: bool


def _create_session(session: Session, user: User, ip: str | None, user_agent: str | None) -> IssuedSession:
    settings = get_settings()
    raw, csrf = new_secret_token("gs_"), new_secret_token()
    identity = IdentitySession(
        id=new_id("ses"),
        user_id=user.id,
        token_hash=token_hash(raw),
        csrf_token_hash=token_hash(csrf),
        ip=ip,
        user_agent=(user_agent or "")[:500],
        expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
    )
    session.add(identity)
    session.flush()
    return IssuedSession(identity.id, user.id, raw, csrf, user.mfa_enabled_at is not None)


def login(email: str, password: str, *, ip: str | None = None, user_agent: str | None = None) -> IssuedSession:
    address = _normalize_email(email)
    invalid = GluonsError("AUTH_FAILED", "Email or password is incorrect.", status=401)
    with public_tx() as session:
        user = session.execute(select(User).where(User.email == address).with_for_update()).scalar_one_or_none()
        if user is None or user.password_hash is None:
            try:
                _hasher.verify(_DUMMY_HASH, password)  # equalise timing for unknown accounts
            except VerificationError:
                pass
            raise invalid
        now = utcnow()
        if user.disabled_at is not None or (user.locked_until is not None and user.locked_until > now):
            raise GluonsError("ACCOUNT_LOCKED", "Too many attempts. Try again in a few minutes.", status=429)
        try:
            _hasher.verify(user.password_hash, password)
            matched = True
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            matched = False
        if matched:
            if user.email_verified_at is None:
                raise GluonsError("EMAIL_NOT_VERIFIED", "Verify your email address before signing in.", status=403)
            if user.mfa_enabled_at is None:
                # With two-step on, only a correct code clears the count, or anyone holding the
                # password could sign in again between guesses and never reach the limit.
                user.failed_logins = 0
            user.locked_until = None
            return _create_session(session, user, ip, user_agent)
        # Returning normally commits the failure count, which raising here would roll back.
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = now + LOCKOUT
            user.failed_logins = 0
    raise invalid


def verify_mfa(session_token: str, code: str) -> None:
    with public_tx() as session:
        identity = _load_session(session, session_token)
        user = session.get(User, identity.user_id, with_for_update=True)
        if user is None or user.mfa_secret_ref is None:
            raise conflict("Two-step verification is not enrolled.")
        now = utcnow()
        if user.locked_until is not None and user.locked_until > now:
            raise GluonsError("ACCOUNT_LOCKED", "Too many attempts. Try again in a few minutes.", status=429)
        secret = vault.open_global(f"mfa:{user.id}", user.mfa_secret_ref)
        hashed = token_hash(code.strip())
        if pyotp.TOTP(secret).verify(code, valid_window=1) or hashed in user.mfa_recovery_hashes:
            user.mfa_recovery_hashes = [h for h in user.mfa_recovery_hashes if h != hashed]
            user.failed_logins = 0
            identity.mfa_verified_at = now
            return
        # Returning normally commits the failure count, which raising here would roll back.
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = now + LOCKOUT
            user.failed_logins = 0
            identity.revoked_at = now
    raise GluonsError("AUTH_FAILED", "That code is not valid.", status=401)


def begin_mfa_enrollment(user_id: str) -> dict[str, Any]:
    secret = pyotp.random_base32()
    with user_tx(user_id) as session:
        user = session.get(User, user_id)
        if user is None:
            raise not_found("User")
        if user.mfa_enabled_at is not None:
            raise conflict("Two-step verification is already on.")
        user.mfa_secret_ref = vault.seal_global(f"mfa:{user_id}", secret)
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Gluons")
    return {"secret": secret, "otpauth_uri": uri}


def confirm_mfa_enrollment(user_id: str, code: str) -> list[str]:
    with user_tx(user_id) as session:
        user = session.get(User, user_id)
        if user is None or user.mfa_secret_ref is None:
            raise conflict("Start enrolling first.")
        secret = vault.open_global(f"mfa:{user_id}", user.mfa_secret_ref)
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            raise GluonsError("AUTH_FAILED", "That code is not valid.", status=401)
        codes = [base64.b32encode(new_secret_token(nbytes=8).encode()).decode()[:16] for _ in range(8)]
        user.mfa_recovery_hashes = [token_hash(c) for c in codes]
        user.mfa_enabled_at = utcnow()
    return codes


def _load_session(session: Session, raw: str) -> IdentitySession:
    identity = session.execute(
        select(IdentitySession).where(IdentitySession.token_hash == token_hash(raw))
    ).scalar_one_or_none()
    if identity is None or identity.revoked_at is not None or identity.expires_at <= utcnow():
        raise auth_required()
    return identity


@dataclass(frozen=True)
class SessionView:
    session_id: str
    user_id: str
    email: str
    display_name: str
    active_tenant_id: str | None
    csrf_token_hash: str
    mfa_pending: bool


def resolve_session(raw: str) -> SessionView:
    with public_tx() as session:
        identity = _load_session(session, raw)
        user = session.get(User, identity.user_id)
        if user is None or user.disabled_at is not None:
            raise auth_required()
        identity.last_seen_at = utcnow()
        return SessionView(
            session_id=identity.id,
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            active_tenant_id=identity.active_tenant_id,
            csrf_token_hash=identity.csrf_token_hash,
            mfa_pending=user.mfa_enabled_at is not None and identity.mfa_verified_at is None,
        )


def logout(raw: str) -> None:
    with public_tx() as session:
        identity = session.execute(
            select(IdentitySession).where(IdentitySession.token_hash == token_hash(raw))
        ).scalar_one_or_none()
        if identity is not None:
            identity.revoked_at = utcnow()


def list_sessions(user_id: str) -> list[dict[str, Any]]:
    with user_tx(user_id) as session:
        rows = session.execute(
            select(IdentitySession)
            .where(
                IdentitySession.user_id == user_id,
                IdentitySession.revoked_at.is_(None),
                IdentitySession.expires_at > utcnow(),
            )
            .order_by(IdentitySession.created_at.desc())
        ).scalars()
        return [
            {
                "id": r.id,
                "ip": r.ip,
                "user_agent": r.user_agent,
                "created_at": r.created_at.isoformat(),
                "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            }
            for r in rows
        ]


def revoke_session(user_id: str, session_id: str) -> None:
    with user_tx(user_id) as session:
        row = session.get(IdentitySession, session_id)
        if row is None or row.user_id != user_id:
            raise not_found("Session")
        row.revoked_at = utcnow()


def request_password_reset(email: str) -> None:
    address = _normalize_email(email)
    web = get_settings().public_web_url
    with public_tx() as session:
        user = session.execute(select(User).where(User.email == address)).scalar_one_or_none()
        if user is None:
            return
        raw = _issue_user_token(session, user.id, "password_reset", timedelta(hours=1))
    mail.send(address, "Reset your Gluons password", f"Reset it here within an hour: {web}/reset-password?token={raw}")


def reset_password(raw_token: str, new_password: str) -> None:
    _check_password_strength(new_password)
    with public_tx() as session:
        token = _consume_user_token(session, raw_token, "password_reset")
        user = session.get(User, token.user_id)
        if user is None:
            raise not_found("User")
        user.password_hash = _hasher.hash(new_password)
        user.failed_logins = 0
        user.locked_until = None
        session.execute(
            update(IdentitySession)
            .where(IdentitySession.user_id == user.id, IdentitySession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )


# --- Organizations ---------------------------------------------------------------------


def create_organization(user_id: str, name: str, *, industry: str | None = None, country: str | None = None) -> str:
    org_id = new_id("org")
    with tenant_tx(org_id, user_id) as session:
        session.add(Organization(id=org_id, name=name.strip(), industry=industry, country=country))
        session.flush()
        session.add(Membership(id=new_id("mem"), tenant_id=org_id, user_id=user_id, role=Role.OWNER.value))
        audit.record(
            session,
            tenant_id=org_id,
            category="organization",
            event_type="organization.created",
            actor=user_id,
            resource_type="organization",
            resource_id=org_id,
            data={"name": name},
        )
    return org_id


def list_organizations(user_id: str) -> list[dict[str, Any]]:
    with user_tx(user_id) as session:
        rows = session.execute(
            select(Organization, Membership.role)
            .join(Membership, Membership.tenant_id == Organization.id)
            .where(
                Membership.user_id == user_id,
                Membership.removed_at.is_(None),
                Organization.deleted_at.is_(None),
            )
            .order_by(Organization.created_at)
        ).all()
        return [{"id": o.id, "name": o.name, "industry": o.industry, "country": o.country, "role": r} for o, r in rows]


def principal_for_user(user_id: str, tenant_id: str, session_id: str | None = None) -> Principal:
    """Membership is re-read on every request, so removal takes effect immediately."""
    with tenant_tx(tenant_id, user_id) as session:
        membership = session.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user_id,
                Membership.removed_at.is_(None),
            )
        ).scalar_one_or_none()
        if membership is None:
            raise forbidden("You are not a member of this organization.")
        return Principal(
            kind="user",
            tenant_id=tenant_id,
            user_id=user_id,
            role=Role(membership.role),
            session_id=session_id,
            approval_tier_limit=membership.approval_tier_limit,
        )


def select_organization(session_id: str, user_id: str, tenant_id: str) -> None:
    principal_for_user(user_id, tenant_id)
    with user_tx(user_id) as session:
        row = session.get(IdentitySession, session_id)
        if row is None or row.user_id != user_id:
            raise not_found("Session")
        row.active_tenant_id = tenant_id


def invite_member(principal: Principal, email: str, role: str) -> dict[str, Any]:
    principal.require("members:manage")
    target_role = Role(role)
    if target_role == Role.OWNER and principal.role != Role.OWNER:
        raise forbidden("Only an owner can invite another owner.")
    address = _normalize_email(email)
    raw = new_secret_token()
    settings = get_settings()
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        invitation = Invitation(
            id=new_id("inv"),
            tenant_id=principal.tenant_id,
            email=address,
            role=target_role.value,
            token_hash=token_hash(raw),
            invited_by=principal.actor_id,
            expires_at=utcnow() + timedelta(days=7),
        )
        session.add(invitation)
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="membership",
            event_type="member.invited",
            actor=principal,
            resource_type="invitation",
            resource_id=invitation.id,
            data={"email": address, "role": target_role.value},
        )
        org = session.get(Organization, principal.tenant_id)
        org_name = org.name if org else "a business"
    mail.send(
        address,
        f"Join {org_name} on Gluons",
        f"You were invited as {target_role.value}: {settings.public_web_url}/invite?org={principal.tenant_id}&token={raw}",
    )
    return {"id": invitation.id, "email": address, "role": target_role.value, "expires_at": invitation.expires_at.isoformat()}


def accept_invitation(user_id: str, tenant_id: str, raw_token: str) -> None:
    with user_tx(user_id) as session:
        user = session.get(User, user_id)
        if user is None or user.email_verified_at is None:
            raise GluonsError("EMAIL_NOT_VERIFIED", "Verify your email address first.", status=403)
        email = user.email
    with tenant_tx(tenant_id, user_id) as session:
        invitation = session.execute(
            select(Invitation).where(Invitation.token_hash == token_hash(raw_token)).with_for_update()
        ).scalar_one_or_none()
        now = utcnow()
        if (
            invitation is None
            or invitation.accepted_at is not None
            or invitation.revoked_at is not None
            or invitation.expires_at <= now
            or not constant_time_equals(invitation.email, email)
        ):
            raise GluonsError("INVALID_TOKEN", "This invitation is invalid or has expired.", status=400)
        invitation.accepted_at = now
        existing = session.execute(
            select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Membership(
                    id=new_id("mem"),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role=invitation.role,
                    invited_by=invitation.invited_by,
                )
            )
        else:
            existing.role = invitation.role
            existing.removed_at = None
        audit.record(
            session,
            tenant_id=tenant_id,
            category="membership",
            event_type="member.joined",
            actor=user_id,
            resource_type="user",
            resource_id=user_id,
            data={"role": invitation.role},
        )


def list_members(principal: Principal) -> list[dict[str, Any]]:
    principal.require("org:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        rows = session.execute(
            select(Membership, User.email, User.display_name)
            .join(User, User.id == Membership.user_id)
            # The memberships policy also exposes the caller's rows in other tenants.
            .where(Membership.tenant_id == principal.tenant_id, Membership.removed_at.is_(None))
            .order_by(Membership.created_at)
        ).all()
        return [
            {
                "id": m.id,
                "user_id": m.user_id,
                "email": email,
                "display_name": name,
                "role": m.role,
                "approval_tier_limit": m.approval_tier_limit,
            }
            for m, email, name in rows
        ]


def _active_owners(session: Session, tenant_id: str) -> int:
    return session.execute(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.tenant_id == tenant_id,
            Membership.role == Role.OWNER.value,
            Membership.removed_at.is_(None),
        )
    ).scalar_one()


def change_member_role(principal: Principal, member_id: str, role: str) -> None:
    principal.require("members:manage")
    new_role = Role(role)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        member = session.get(Membership, member_id, with_for_update=True)
        if member is None or member.removed_at is not None:
            raise not_found("Member")
        if Role.OWNER in (new_role, Role(member.role)) and principal.role != Role.OWNER:
            raise forbidden("Only an owner can change owner access.")
        if member.role == Role.OWNER.value and new_role != Role.OWNER and _active_owners(session, principal.tenant_id) <= 1:
            raise conflict("A business needs at least one owner.")
        before = member.role
        member.role = new_role.value
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="membership",
            event_type="member.role_changed",
            actor=principal,
            resource_type="membership",
            resource_id=member_id,
            data={"before": before, "after": new_role.value},
        )


def remove_member(principal: Principal, member_id: str) -> None:
    principal.require("members:manage")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        member = session.get(Membership, member_id, with_for_update=True)
        if member is None or member.removed_at is not None:
            raise not_found("Member")
        if member.role == Role.OWNER.value:
            if principal.role != Role.OWNER:
                raise forbidden("Only an owner can remove an owner.")
            if _active_owners(session, principal.tenant_id) <= 1:
                raise conflict("A business needs at least one owner.")
        member.removed_at = utcnow()
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="membership",
            event_type="member.removed",
            actor=principal,
            resource_type="membership",
            resource_id=member_id,
        )
