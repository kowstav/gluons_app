"""Field representative sessions. Consent is an owner action in a signed-in web session."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gluons import assist
from gluons.api.deps import PrincipalDep

router = APIRouter(prefix="/v1/assist", tags=["assist"])


class Consent(BaseModel):
    allow_shadow_trial: bool = False
    allow_sample_data: bool = False


class ShadowTrial(BaseModel):
    skill_id: str = Field(min_length=1, max_length=100)
    data_basis: Literal["synthetic", "owner_consented_sample"] = "synthetic"


class EndSession(BaseModel):
    report: dict[str, Any] = Field(default_factory=dict)


@router.post("/sessions", status_code=201)
def request_session(principal: PrincipalDep) -> dict[str, Any]:
    return assist.request_session(principal)


@router.get("/sessions")
def list_sessions(principal: PrincipalDep) -> dict[str, Any]:
    return {"sessions": assist.list_sessions(principal)}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, principal: PrincipalDep) -> dict[str, Any]:
    return assist.get_session(principal, session_id)


@router.post("/sessions/{session_id}/consent")
def consent(session_id: str, body: Consent, principal: PrincipalDep) -> dict[str, Any]:
    return assist.consent(principal, session_id, **body.model_dump())


@router.post("/sessions/{session_id}/revoke")
def revoke(session_id: str, principal: PrincipalDep) -> dict[str, Any]:
    return assist.revoke(principal, session_id)


@router.get("/sessions/{session_id}/workspace")
def workspace(session_id: str, principal: PrincipalDep) -> dict[str, Any]:
    return assist.workspace(principal, session_id)


@router.post("/sessions/{session_id}/shadow-trial", status_code=201)
def shadow_trial(session_id: str, body: ShadowTrial, principal: PrincipalDep) -> dict[str, Any]:
    return assist.setup_shadow_trial(principal, session_id, body.skill_id, data_basis=body.data_basis)


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str, body: EndSession, principal: PrincipalDep) -> dict[str, Any]:
    return assist.end_session(principal, session_id, body.report)
