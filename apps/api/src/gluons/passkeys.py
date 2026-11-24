"""WebAuthn enrollment and single-use approval of an exact owner policy."""
from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from gluons.config import get_settings
from gluons.core import GluonsError, auth_required, conflict, new_id, utcnow
from gluons.db.models import IdentitySession, OwnerPasskey, PasskeyChallenge
from gluons.db.session import public_tx, user_tx
from gluons.identity import SessionView

CHALLENGE_TTL = timedelta(minutes=5)
ENROLLMENT_WINDOW = timedelta(minutes=10)


class Proof(BaseModel):
    challenge_id: str = Field(pattern=r"^wac_[0-9a-f]{24}$", max_length=28)
    credential: dict[str, Any]


def _origin() -> tuple[str, str] | None:
    settings = get_settings()
    public_origin = settings.public_web_url.rstrip("/")
    configured = settings.passkey_origin or (public_origin if not settings.hosted else "")
    if configured != public_origin:
        return None
    parts = urlsplit(configured)
    if not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        return None
    if parts.scheme == "https" or (not settings.hosted and parts.scheme == "http" and parts.hostname == "localhost"):
        return configured, parts.hostname
    return None


def _relying_party() -> tuple[str, str]:
    result = _origin()
    if result is None:
        raise GluonsError("PASSKEY_UNAVAILABLE", "Passkeys require a configured Gluons web origin.", status=503)
    return result


def _invalid() -> GluonsError:
    return GluonsError("PASSKEY_INVALID", "The passkey could not verify this request. Try again.", status=403)


def _session(session: Session, user_id: str, session_id: str, *, recent: bool = False) -> None:
    row = session.get(IdentitySession, session_id, with_for_update=True)
    now = utcnow()
    if row is None or row.user_id != user_id or row.revoked_at is not None or row.expires_at <= now:
        raise auth_required()
    if recent and now - (row.mfa_verified_at or row.created_at) > ENROLLMENT_WINDOW:
        raise GluonsError("SIGN_IN_AGAIN", "Sign in again before adding a passkey.", status=403)


def _start(session: Session, user_id: str, session_id: str, purpose: str, decision_hash: str | None = None) -> PasskeyChallenge:
    row = PasskeyChallenge(id=new_id("wac"), user_id=user_id, session_id=session_id, purpose=purpose,
                           challenge=secrets.token_bytes(32), decision_hash=decision_hash,
                           expires_at=utcnow() + CHALLENGE_TTL)
    session.add(row)
    return row


def _load(session: Session, proof: Proof, user_id: str, session_id: str, purpose: str,
          decision_hash: str | None = None) -> PasskeyChallenge:
    row = session.get(PasskeyChallenge, proof.challenge_id, with_for_update=True)
    if (row is None or row.user_id != user_id or row.session_id != session_id or row.purpose != purpose
            or row.decision_hash != decision_hash or row.used_at is not None or row.expires_at <= utcnow()):
        raise _invalid()
    return row


def _check_browser_data(credential: dict[str, Any]) -> None:
    if len(json.dumps(credential)) > 64_000:
        raise _invalid()
    client_data = json.loads(base64url_to_bytes(credential["response"]["clientDataJSON"]))
    if not isinstance(client_data, dict) or client_data.get("crossOrigin") is True:
        raise _invalid()


def status(view: SessionView) -> dict[str, bool]:
    with user_tx(view.user_id) as session:
        return {"available": _origin() is not None, "enrolled": session.get(OwnerPasskey, view.user_id) is not None}


def begin_enrollment(view: SessionView) -> dict[str, Any]:
    _, rp_id = _relying_party()
    if view.mfa_pending:
        raise auth_required("Complete two-step verification before adding a passkey.")
    with user_tx(view.user_id) as session:
        _session(session, view.user_id, view.session_id, recent=True)
        if session.get(OwnerPasskey, view.user_id) is not None:
            raise conflict("This account already has a passkey.")
        row = _start(session, view.user_id, view.session_id, "enroll")
        options = generate_registration_options(
            rp_id=rp_id, rp_name="Gluons", user_id=view.user_id.encode(), user_name=view.email,
            user_display_name=view.display_name or view.email, challenge=row.challenge,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
        )
        return {"challenge_id": row.id, "options": json.loads(options_to_json(options))}


def finish_enrollment(view: SessionView, proof: Proof) -> None:
    origin, rp_id = _relying_party()
    if view.mfa_pending:
        raise auth_required("Complete two-step verification before adding a passkey.")
    with user_tx(view.user_id) as session:
        _session(session, view.user_id, view.session_id, recent=True)
        row = _load(session, proof, view.user_id, view.session_id, "enroll")
        try:
            _check_browser_data(proof.credential)
            verified = verify_registration_response(credential=proof.credential, expected_challenge=row.challenge,
                expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True)
        except (WebAuthnException, KeyError, TypeError, ValueError):
            raise _invalid() from None
        stored = session.scalar(insert(OwnerPasskey).values(user_id=view.user_id, credential_id=verified.credential_id,
            public_key=verified.credential_public_key, sign_count=verified.sign_count).on_conflict_do_nothing()
            .returning(OwnerPasskey.user_id))
        if stored is None:
            raise conflict("This account or passkey is already enrolled.")
        row.used_at = utcnow()


def begin_policy(session: Session, user_id: str, session_id: str, decision_hash: str) -> dict[str, Any]:
    _, rp_id = _relying_party()
    _session(session, user_id, session_id)
    passkey = session.get(OwnerPasskey, user_id)
    if passkey is None:
        raise GluonsError("PASSKEY_REQUIRED", "Add a passkey in Account security before granting an unattended spending allowance.", status=403)
    row = _start(session, user_id, session_id, "policy", decision_hash)
    options = generate_authentication_options(rp_id=rp_id, challenge=row.challenge,
        allow_credentials=[PublicKeyCredentialDescriptor(id=passkey.credential_id)],
        user_verification=UserVerificationRequirement.REQUIRED)
    return {"challenge_id": row.id, "options": json.loads(options_to_json(options))}


def verify_policy(session: Session, user_id: str, session_id: str, decision_hash: str, proof: Proof) -> None:
    origin, rp_id = _relying_party()
    _session(session, user_id, session_id)
    row = _load(session, proof, user_id, session_id, "policy", decision_hash)
    passkey = session.get(OwnerPasskey, user_id, with_for_update=True)
    if passkey is None:
        raise _invalid()
    try:
        _check_browser_data(proof.credential)
        verified = verify_authentication_response(credential=proof.credential, expected_challenge=row.challenge,
            expected_rp_id=rp_id, expected_origin=origin, credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count, require_user_verification=True)
    except (WebAuthnException, KeyError, TypeError, ValueError):
        raise _invalid() from None
    if verified.credential_id != passkey.credential_id:
        raise _invalid()
    row.used_at = passkey.last_used_at = utcnow()
    passkey.sign_count = verified.new_sign_count


def purge_expired() -> int:
    with public_tx() as session:
        session.execute(text("SELECT set_config('app.passkey_cleanup', '1', true)"))
        return session.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires_at <= utcnow())).rowcount
