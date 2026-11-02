"""Account linking is separate from an automation's operational permission."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from gluons.api.deps import PrincipalDep
from gluons.config import get_settings
from gluons.connections import service

router = APIRouter(prefix="/v1", tags=["connections"])


class BeginConnection(BaseModel):
    provider: str
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    automation_plan_id: str | None = None


@router.post("/connections/sessions", status_code=201)
@router.post("/connection-sessions", status_code=201)
def begin(body: BeginConnection, principal: PrincipalDep) -> dict:
    return service.begin_session(principal, body.provider, capabilities=body.capabilities, automation_plan_id=body.automation_plan_id)


@router.get("/connections/sessions/{session_id}")
@router.get("/connection-sessions/{session_id}")
def status(session_id: str, principal: PrincipalDep) -> dict:
    return service.session_status(principal, session_id)


@router.post("/connections/sessions/{session_id}/authorize")
def authorize(session_id: str, principal: PrincipalDep) -> dict:
    return service.authorize(principal, session_id)


@router.get("/oauth/gmail/callback")
def callback(principal: PrincipalDep, state: str = Query(min_length=20, max_length=300), code: str | None = Query(default=None, max_length=4096), error: str | None = Query(default=None, max_length=200)) -> RedirectResponse:
    result = service.callback(principal, state=state, code=code, error=error)
    return RedirectResponse(f"{get_settings().public_web_url.rstrip('/')}/setup/{result['session_id']}", status_code=303)


@router.post("/connections/sessions/{session_id}/cancel")
def cancel_setup(session_id: str, principal: PrincipalDep) -> dict:
    return service.cancel_session(principal, session_id)


@router.get("/connections")
def list_connections(principal: PrincipalDep) -> dict:
    return {"connections": service.list_connections(principal)}


@router.post("/connections/{connection_id}/verify")
def verify(connection_id: str, principal: PrincipalDep) -> dict:
    return service.verify_connection(principal, connection_id)


@router.post("/connections/{connection_id}/confirm")
def confirm(connection_id: str, principal: PrincipalDep) -> dict:
    return service.confirm_account(principal, connection_id)


@router.delete("/connections/{connection_id}")
def disconnect(connection_id: str, principal: PrincipalDep) -> dict:
    return service.disconnect(principal, connection_id)
