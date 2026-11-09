"""Owner-controlled Business Passport facts."""
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from gluons import passport, passport_previews
from gluons.api.deps import PrincipalDep
from gluons.passport import PassportFields

router = APIRouter(prefix="/v1/business-passport", tags=["business-passport"])


class UpdatePassport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    fields: PassportFields


class ImportPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str = Field(min_length=1, max_length=40)


class ConfirmOrganizationFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[Literal["business_name", "country", "timezone"]] = Field(min_length=1, max_length=3)
    expected_version: int = Field(ge=1)
    expected_organization_version: int = Field(ge=1)


class SharePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["gmail", "shopify"]
    purpose: Literal["account_setup"]
    fields: list[str] = Field(min_length=1, max_length=10)


class ConfirmPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_token: str = Field(min_length=20, max_length=8192)


@router.get("")
def get_passport(principal: PrincipalDep, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return passport.get(principal)


@router.patch("")
def update_passport(body: UpdatePassport, principal: PrincipalDep) -> dict:
    return passport.update(principal, body.expected_version, body.fields.model_dump(exclude_unset=True, mode="json"))


@router.post("/organization-fields/confirm")
def confirm_organization_fields(body: ConfirmOrganizationFields, principal: PrincipalDep) -> dict:
    return passport.confirm_organization_fields(principal, body.fields, body.expected_version, body.expected_organization_version)


@router.post("/imports/preview")
def import_preview(body: ImportPreview, principal: PrincipalDep, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return passport_previews.preview_import(principal, body.connection_id)


@router.post("/imports/confirm")
def import_confirm(body: ConfirmPreview, principal: PrincipalDep) -> dict:
    return passport_previews.confirm_import(principal, body.preview_token)


@router.post("/sharing/preview")
def sharing_preview(body: SharePreview, principal: PrincipalDep, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return passport_previews.preview_share(principal, body.provider, body.fields, body.purpose)


@router.post("/sharing/confirm")
def sharing_confirm(body: ConfirmPreview, principal: PrincipalDep) -> dict:
    return passport_previews.confirm_share(principal, body.preview_token)


@router.get("/sharing/consents")
def sharing_consents(principal: PrincipalDep, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"consents": passport_previews.list_consents(principal)}


@router.post("/sharing/consents/{consent_id}/revoke")
def sharing_revoke(consent_id: str, principal: PrincipalDep) -> dict:
    return passport_previews.revoke_consent(principal, consent_id)
