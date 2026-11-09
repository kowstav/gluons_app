"""Authenticated owner setup sessions, independent of provider authorization."""
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from gluons import setup_sessions
from gluons.api.deps import IdempotencyKey, PrincipalDep

router = APIRouter(prefix="/v1/setup-sessions", tags=["setup"])


class StartSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["business_launch"]
    launch_plan_id: str | None = Field(default=None, max_length=40)


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class AttachConnection(Version):
    connection_session_id: str = Field(min_length=1, max_length=40)


class RequestConnection(Version):
    provider: Literal["gmail"]
    capabilities: list[str] = Field(min_length=1, max_length=10)


class Redeem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume_token: str = Field(min_length=20, max_length=300)


@router.post("", status_code=201)
def start(body: StartSetup, principal: PrincipalDep, key: IdempotencyKey) -> dict:
    return setup_sessions.start(principal, intent=body.intent, launch_plan_id=body.launch_plan_id, idempotency_key=key)


@router.get("")
def list_sessions(principal: PrincipalDep, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"sessions": setup_sessions.list_sessions(principal)}


@router.get("/{setup_id}")
def get(setup_id: str, principal: PrincipalDep, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return setup_sessions.get(principal, setup_id)


@router.post("/{setup_id}/remove-expired")
def remove_expired(setup_id: str, body: Version, principal: PrincipalDep) -> dict:
    return setup_sessions.remove_expired(principal, setup_id, body.expected_version)


@router.post("/{setup_id}/connection")
def attach(setup_id: str, body: AttachConnection, principal: PrincipalDep) -> dict:
    return setup_sessions.attach_connection(principal, setup_id, body.connection_session_id, body.expected_version)


@router.post("/{setup_id}/connection/request")
def request_connection(setup_id: str, body: RequestConnection, principal: PrincipalDep, key: IdempotencyKey) -> dict:
    return setup_sessions.request_connection(principal, setup_id, provider=body.provider, capabilities=body.capabilities,
                                             expected_version=body.expected_version, idempotency_key=key)


@router.post("/{setup_id}/resume-token")
def issue(setup_id: str, body: Version, principal: PrincipalDep, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return setup_sessions.issue_resume_token(principal, setup_id, body.expected_version)


@router.post("/{setup_id}/redeem")
def redeem(setup_id: str, body: Redeem, principal: PrincipalDep, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return setup_sessions.redeem(principal, setup_id, body.resume_token)
