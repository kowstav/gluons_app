"""Businesses, members and whole-business controls."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field

from gluons import audit, identity
from gluons.api.deps import PrincipalDep, SessionDep
from gluons.core import not_found
from gluons.db.models import Organization
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle

router = APIRouter(prefix="/v1/organizations", tags=["organizations"])


class CreateOrganization(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, min_length=2, max_length=2)


class UpdateOrganization(BaseModel):
    legal_name: str | None = Field(default=None, max_length=300)
    industry: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str | None = Field(default=None, max_length=64)
    business_facts: dict[str, Any] | None = None
    allowed_model_providers: list[str] | None = None
    monthly_budget_usd: float | None = Field(default=None, ge=0)


class Invite(BaseModel):
    email: EmailStr
    role: str


class ChangeRole(BaseModel):
    role: str


class AcceptInvite(BaseModel):
    token: str = Field(min_length=10, max_length=300)


class EmergencyStop(BaseModel):
    active: bool


class PausedCategories(BaseModel):
    categories: list[str]


@router.get("")
def list_organizations(view: SessionDep) -> dict[str, Any]:
    return {"organizations": identity.list_organizations(view.user_id), "active": view.active_tenant_id}


@router.post("", status_code=201)
def create_organization(body: CreateOrganization, view: SessionDep) -> dict[str, Any]:
    org_id = identity.create_organization(view.user_id, body.name, industry=body.industry, country=body.country)
    identity.select_organization(view.session_id, view.user_id, org_id)
    return {"id": org_id, "name": body.name}


@router.post("/{org_id}/select")
def select_organization(org_id: str, view: SessionDep) -> dict[str, str]:
    identity.select_organization(view.session_id, view.user_id, org_id)
    return {"active": org_id}


@router.post("/{org_id}/invitations/accept")
def accept_invitation(org_id: str, body: AcceptInvite, view: SessionDep) -> dict[str, str]:
    identity.accept_invitation(view.user_id, org_id, body.token)
    return {"status": "joined"}


@router.get("/current")
def current(principal: PrincipalDep) -> dict[str, Any]:
    principal.require("org:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id)
        if org is None:
            raise not_found("Organization")
        return {
            "id": org.id,
            "name": org.name,
            "legal_name": org.legal_name,
            "industry": org.industry,
            "country": org.country,
            "timezone": org.timezone,
            "business_facts": org.business_facts,
            "emergency_stop_at": org.emergency_stop_at.isoformat() if org.emergency_stop_at else None,
            "paused_categories": org.paused_categories,
            "allowed_model_providers": org.allowed_model_providers,
            "monthly_budget_usd": str(org.monthly_budget_usd) if org.monthly_budget_usd is not None else None,
            "role": principal.role,
            "permissions": sorted(principal.permissions()),
        }


@router.patch("/current")
def update_current(body: UpdateOrganization, principal: PrincipalDep) -> dict[str, str]:
    principal.require("org:manage")
    changes = body.model_dump(exclude_unset=True)
    if "monthly_budget_usd" in changes:
        principal.require("billing:manage")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id, with_for_update=True)
        for key, value in changes.items():
            setattr(org, key, value)
        org.version += 1
        audit.record(
            session,
            tenant_id=principal.tenant_id,
            category="organization",
            event_type="organization.updated",
            actor=principal,
            resource_type="organization",
            resource_id=principal.tenant_id,
            data={"fields": sorted(changes)},
        )
    return {"status": "updated"}


@router.post("/current/emergency-stop")
def emergency_stop(body: EmergencyStop, principal: PrincipalDep) -> dict[str, bool]:
    lifecycle.set_emergency_stop(principal, body.active)
    return {"active": body.active}


@router.post("/current/paused-categories")
def paused_categories(body: PausedCategories, principal: PrincipalDep) -> dict[str, list[str]]:
    return {"paused_categories": lifecycle.set_paused_categories(principal, body.categories)}


@router.get("/current/members")
def members(principal: PrincipalDep) -> dict[str, Any]:
    return {"members": identity.list_members(principal)}


@router.post("/current/invitations", status_code=201)
def invite(body: Invite, principal: PrincipalDep) -> dict[str, Any]:
    return identity.invite_member(principal, body.email, body.role)


@router.patch("/current/members/{member_id}")
def change_role(member_id: str, body: ChangeRole, principal: PrincipalDep) -> dict[str, str]:
    identity.change_member_role(principal, member_id, body.role)
    return {"status": "updated"}


@router.delete("/current/members/{member_id}")
def remove_member(member_id: str, principal: PrincipalDep) -> dict[str, str]:
    identity.remove_member(principal, member_id)
    return {"status": "removed"}
