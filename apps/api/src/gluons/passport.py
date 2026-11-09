"""Confirmed, non-secret business facts, separate from provider authority."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator

from gluons import audit
from gluons.authz import Principal, Role
from gluons.core import conflict_version, forbidden, not_found, utcnow, validation_failed
from gluons.db.models import Organization
from gluons.db.session import tenant_tx


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


def _owner(principal: Principal) -> None:
    principal.require("org:manage")
    if principal.kind != "user" or principal.role != Role.OWNER or not principal.session_id or not principal.user_id:
        raise forbidden("An authenticated business owner must manage the Passport in Gluons Web.")


def _view(org: Organization) -> dict[str, Any]:
    return {"organization_id": org.id, "version": org.passport_version, "fields": org.business_passport,
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
                                "confirmed_by": principal.user_id}
        if changes:
            org.business_passport = fields
            org.passport_version += 1
            audit.record(session, tenant_id=principal.tenant_id, category="organization", event_type="passport.updated",
                         actor=principal, resource_type="organization", resource_id=principal.tenant_id,
                         data={"fields": sorted(changes), "passport_version": org.passport_version})
        return _view(org)
