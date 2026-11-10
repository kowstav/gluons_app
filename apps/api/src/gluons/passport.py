"""Confirmed, non-secret business facts, separate from provider authority."""
from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator, model_validator

from gluons import audit
from gluons.authz import Principal, Role
from gluons.core import conflict_version, fingerprint, forbidden, not_found, utcnow, validation_failed
from gluons.db.models import Organization
from gluons.db.session import tenant_tx

SENSITIVE_MARKERS = re.compile(r"(?i)(?:bearer\s+\S+|(?:password|api[_ -]?key|access[_ -]?token|refresh[_ -]?token)\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}|(?<!\d)\d{12,19}(?!\d))")


class PassportFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    brand_name: str | None = Field(default=None, min_length=1, max_length=200)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    business_type: str | None = Field(default=None, min_length=1, max_length=100)
    selling_regions: list[str] | None = Field(default=None, max_length=50)
    support_email: EmailStr | None = None
    support_hours: str | None = Field(default=None, min_length=1, max_length=300)
    operational_address: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("selling_regions")
    @classmethod
    def validate_regions(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and (any(len(v) != 2 or not v.isascii() or not v.isalpha() or not v.isupper() for v in values)
                                   or len(set(values)) != len(values)):
            raise ValueError("Use unique uppercase two-letter region codes.")
        return values

    @model_validator(mode="after")
    def reject_credential_markers(self) -> PassportFields:
        # Free-text fields are for ordinary business facts, not credential storage.
        for value in self.model_dump(exclude_unset=True).values():
            values = value if isinstance(value, list) else [value]
            if any(isinstance(item, str) and SENSITIVE_MARKERS.search(item) for item in values):
                raise ValueError("Credentials and sensitive identifiers do not belong in the Passport.")
        return self


def field_metadata(name: str) -> dict[str, Any]:
    return {"schema_version": 1, "sensitivity": "business_contact" if name in {"support_email", "operational_address"} else "business_profile"}


ORGANIZATION_FIELDS = {"business_name": "name", "country": "country", "timezone": "timezone"}


def resolved_fields(org: Organization) -> dict[str, Any]:
    """Resolve confirmed organization values without storing competing copies."""
    result = {}
    for name, raw in org.business_passport.items():
        field = dict(raw)
        if field.get("source") == "organization" and name in ORGANIZATION_FIELDS:
            value = getattr(org, ORGANIZATION_FIELDS[name])
            expected_hash = field.pop("value_hash", None)
            current = value is not None and fingerprint(value) == expected_hash
            field["status"] = "confirmed" if current else "stale"
            if current:
                field["value"] = value
        result[name] = field
    return result


def _owner(principal: Principal) -> None:
    principal.require("org:manage")
    if principal.kind != "user" or principal.role != Role.OWNER or not principal.session_id or not principal.user_id:
        raise forbidden("An authenticated business owner must manage the Passport in Gluons Web.")


def _view(org: Organization) -> dict[str, Any]:
    return {"organization_id": org.id, "schema_version": 1, "version": org.passport_version, "fields": resolved_fields(org),
            "organization": {"name": org.name, "country": org.country, "timezone": org.timezone,
                             "version": org.version, "source": "organization_record", "passport_confirmation": "review_individual_fields"},
            "notice": "Profile facts do not authorize sharing or provider actions."}


def get(principal: Principal) -> dict[str, Any]:
    _owner(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id)
        if org is None:
            raise not_found("Organization")
        return _view(org)


def update(principal: Principal, expected_version: int, changes: dict[str, Any]) -> dict[str, Any]:
    _owner(principal)
    try:
        changes = PassportFields.model_validate(changes).model_dump(exclude_unset=True, mode="json")
    except ValidationError:
        raise validation_failed("Use only valid, non-secret Passport fields.") from None
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id, with_for_update=True)
        if org is None:
            raise not_found("Organization")
        if org.passport_version != expected_version:
            raise conflict_version(org.passport_version)
        fields = dict(org.business_passport)
        now = utcnow().isoformat()
        for name, value in changes.items():
            if value is None:
                fields.pop(name, None)
            else:
                fields[name] = {"value": value, "source": "owner", "confirmed_at": now,
                                "confirmed_by": principal.user_id, **field_metadata(name)}
        if changes:
            org.business_passport = fields
            org.passport_version += 1
            audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="passport.updated",
                         actor=principal, resource_type="organization", resource_id=principal.tenant_id,
                         data={"fields": sorted(changes), "passport_version": org.passport_version})
        return _view(org)


def confirm_organization_fields(principal: Principal, names: list[str], expected_version: int,
                                expected_organization_version: int) -> dict[str, Any]:
    _owner(principal)
    if not names or len(set(names)) != len(names) or not set(names) <= set(ORGANIZATION_FIELDS):
        raise validation_failed("Choose organization name, country or timezone to confirm.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id, with_for_update=True)
        if org is None:
            raise not_found("Organization")
        if org.passport_version != expected_version:
            raise conflict_version(org.passport_version)
        if org.version != expected_organization_version:
            raise conflict_version(org.version)
        if any(not getattr(org, ORGANIZATION_FIELDS[name]) for name in names):
            raise validation_failed("Fill each selected organization field before confirming it.")
        if any(SENSITIVE_MARKERS.search(getattr(org, ORGANIZATION_FIELDS[name])) for name in names):
            raise validation_failed("Credentials and sensitive identifiers do not belong in the Passport.")
        if "country" in names and (len(org.country) != 2 or not org.country.isascii() or not org.country.isalpha() or not org.country.isupper()):
            raise validation_failed("Use an uppercase two-letter organization country before confirming it.")
        if "timezone" in names:
            try:
                ZoneInfo(org.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                raise validation_failed("Choose a valid IANA organization timezone before confirming it.") from None
        fields = dict(org.business_passport)
        for name in names:
            fields[name] = {"source": "organization", "source_ref": ORGANIZATION_FIELDS[name],
                            "value_hash": fingerprint(getattr(org, ORGANIZATION_FIELDS[name])),
                            "confirmed_at": utcnow().isoformat(), "confirmed_by": principal.user_id, **field_metadata(name)}
        org.business_passport = fields
        org.passport_version += 1
        audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="passport.organization_confirmed",
                     actor=principal, resource_type="organization", resource_id=principal.tenant_id,
                     data={"fields": sorted(names), "passport_version": org.passport_version})
        return _view(org)
