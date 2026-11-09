"""Owner-reviewed profile previews; no provider share or write occurs here."""
from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from gluons import audit, passport
from gluons.authz import Principal
from gluons.config import get_settings
from gluons.core import (
    canonical_json,
    conflict,
    constant_time_equals,
    fingerprint,
    hmac_sha256_hex,
    new_id,
    not_found,
    utcnow,
    validation_failed,
)
from gluons.db.models import Connection, Organization
from gluons.db.session import tenant_tx

PREVIEW_TTL = timedelta(minutes=15)
CONSENT_TTL = timedelta(hours=24)
MAX_CONSENTS = 200


def _seal(principal: Principal, kind: str, detail: dict[str, Any]) -> str:
    body = {"kind": kind, "tenant": principal.tenant_id, "user": principal.user_id, "session_hash": fingerprint(principal.session_id),
            "expires": (utcnow() + PREVIEW_TTL).isoformat(), **detail}
    encoded = base64.urlsafe_b64encode(canonical_json(body).encode()).decode().rstrip("=")
    key = get_settings().key_bytes(get_settings().signing_key_b64)
    return encoded + "." + hmac_sha256_hex(key, "passport-preview-v1:" + encoded)


def _open(principal: Principal, token: str, kind: str) -> dict[str, Any]:
    try:
        if not isinstance(token, str) or len(token) > 8192:
            raise ValueError
        encoded, signature = token.split(".")
        key = get_settings().key_bytes(get_settings().signing_key_b64)
        if not constant_time_equals(signature, hmac_sha256_hex(key, "passport-preview-v1:" + encoded)):
            raise ValueError
        body = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        expires = datetime.fromisoformat(body["expires"])
        if (body["kind"] != kind or body["tenant"] != principal.tenant_id or body["user"] != principal.user_id
                or body["session_hash"] != fingerprint(principal.session_id) or expires.tzinfo is None or expires <= utcnow()):
            raise ValueError
        return body
    except (ValueError, TypeError, KeyError, AttributeError, binascii.Error):
        raise conflict("This preview is invalid, expired, or belongs to another session.") from None


def _shopify_currency(session, connection_id: str, *, lock: bool = False) -> tuple[Connection, str, str]:
    row = session.get(Connection, connection_id, with_for_update=lock)
    if (row is None or row.provider != "shopify" or row.status != "connected" or row.disconnected_at is not None or not row.credential_ref
            or row.account_confirmed_at is None or row.last_checked_at is None
            or (row.token_expires_at is not None and row.token_expires_at <= utcnow())
            or row.last_checked_at < utcnow() - timedelta(hours=24)):
        raise not_found("Confirmed, recently checked Shopify connection")
    currency = row.metadata_json.get("currency")
    try:
        parsed = passport.PassportFields.model_validate({"currency": currency})
        if parsed.currency is None:
            raise ValueError
    except (ValidationError, ValueError):
        raise validation_failed("The connected store has no valid currency to import.") from None
    state = fingerprint({"id": row.id, "account": row.external_account_id, "display": row.account_display,
                         "credential_ref": row.credential_ref, "confirmed": row.account_confirmed_at.isoformat(),
                         "checked": row.last_checked_at.isoformat(), "currency": parsed.currency, "status": row.status})
    return row, parsed.currency, state


def preview_import(principal: Principal, connection_id: str) -> dict[str, Any]:
    passport._owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id)
        if org is None:
            raise not_found("Organization")
        row, currency, state = _shopify_currency(session, connection_id)
        token = _seal(principal, "import", {"connection_id": row.id, "state": state, "version": org.passport_version})
        return {"fields": {"currency": currency}, "source": "shopify", "connection_id": row.id,
                "profile_version": org.passport_version, "preview_token": token,
                "notice": "This proposed value is not confirmed until you approve it."}


def confirm_import(principal: Principal, token: str) -> dict[str, Any]:
    passport._owner(principal)
    preview = _open(principal, token, "import")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id, with_for_update=True)
        if org is None:
            raise not_found("Organization")
        if org.passport_version != preview["version"]:
            raise conflict("The Passport changed. Review a fresh import preview.")
        row, currency, state = _shopify_currency(session, preview["connection_id"], lock=True)
        if not constant_time_equals(state, preview["state"]):
            raise conflict("The connected store changed. Review a fresh import preview.")
        fields = dict(org.business_passport)
        fields["currency"] = {"value": currency, "source": "shopify", "source_ref": row.id,
                              "confirmed_at": utcnow().isoformat(), "confirmed_by": principal.user_id,
                              **passport.field_metadata("currency")}
        org.business_passport = fields
        org.passport_version += 1
        audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="passport.import_confirmed",
                     actor=principal, resource_type="organization", resource_id=principal.tenant_id,
                     data={"fields": ["currency"], "provider": "shopify", "passport_version": org.passport_version})
        return passport._view(org)


def preview_share(principal: Principal, provider: str, field_names: list[str], purpose: str) -> dict[str, Any]:
    passport._owner(principal)
    allowed = set(passport.PassportFields.model_fields)
    if (provider not in {"shopify", "gmail"} or purpose != "account_setup" or not field_names
            or len(field_names) > len(allowed) or len(set(field_names)) != len(field_names) or not set(field_names) <= allowed):
        raise validation_failed("Choose a supported provider, purpose and confirmed Passport fields.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id)
        if org is None:
            raise not_found("Organization")
        if not set(field_names) <= set(org.business_passport):
            raise validation_failed("Confirm each selected field before sharing.")
        names = sorted(field_names)
        token = _seal(principal, "share", {"provider": provider, "purpose": purpose, "fields": names,
                                            "version": org.passport_version})
        return {"provider": provider, "purpose": purpose, "fields": {name: org.business_passport[name]["value"] for name in names},
                "profile_version": org.passport_version, "preview_token": token,
                "notice": "Confirming records consent only; no data is sent and no provider action is authorized."}


def confirm_share(principal: Principal, token: str) -> dict[str, Any]:
    passport._owner(principal)
    preview = _open(principal, token, "share")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id, with_for_update=True)
        if org is None:
            raise not_found("Organization")
        if org.passport_version != preview["version"]:
            raise conflict("The Passport changed. Review a fresh sharing preview.")
        token_digest = fingerprint(token)
        for consent in org.passport_consents:
            if constant_time_equals(consent["preview_hash"], token_digest):
                return {key: value for key, value in consent.items() if key != "preview_hash"}
        if len(org.passport_consents) >= MAX_CONSENTS:
            raise conflict("This business has reached its consent history limit.")
        now = utcnow()
        consent = {"id": new_id("psc"), "provider": preview["provider"], "purpose": preview["purpose"],
                   "fields": preview["fields"], "profile_version": org.passport_version,
                   "confirmed_by": principal.user_id, "confirmed_at": now.isoformat(),
                   "expires_at": (now + CONSENT_TTL).isoformat(), "preview_hash": token_digest}
        org.passport_consents = [*org.passport_consents, consent]
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="passport.share_consent",
                     actor=principal, resource_type="organization", resource_id=principal.tenant_id,
                     data={"provider": consent["provider"], "fields": consent["fields"], "profile_version": org.passport_version})
        return {key: value for key, value in consent.items() if key != "preview_hash"}


def list_consents(principal: Principal) -> list[dict[str, Any]]:
    passport._owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id)
        if org is None:
            raise not_found("Organization")
        result = []
        for consent in org.passport_consents:
            view = {key: value for key, value in consent.items() if key != "preview_hash"}
            view["active"] = (not consent.get("revoked_at") and consent["profile_version"] == org.passport_version
                              and datetime.fromisoformat(consent["expires_at"]) > utcnow())
            result.append(view)
        return result


def revoke_consent(principal: Principal, consent_id: str) -> dict[str, Any]:
    passport._owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id, with_for_update=True)
        if org is None:
            raise not_found("Organization")
        consents = [dict(item) for item in org.passport_consents]
        consent = next((item for item in consents if item["id"] == consent_id), None)
        if consent is None:
            raise not_found("Passport sharing consent")
        if not consent.get("revoked_at"):
            consent["revoked_at"] = utcnow().isoformat()
            org.passport_consents = consents
            audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="passport.share_revoked",
                         actor=principal, resource_type="organization", resource_id=principal.tenant_id,
                         data={"consent_id": consent_id})
        return {"id": consent_id, "revoked": True}
