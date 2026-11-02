"""Owner-consented setup assistance and native assistant control."""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gluons import assist, native_assistant
from gluons.api.deps import IdempotencyKey, PrincipalDep, idempotent

router = APIRouter(prefix="/v1", tags=["assistance"])


class Completion(BaseModel):
    diagnosis: bool = False
    permissions_explained: bool = False
    shadow_trial: bool = False
    handoff: bool = False


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class Command(BaseModel):
    action: Literal["pause", "resume", "cancel"]
    automation_id: str = Field(pattern=r"^aut_[a-f0-9]{24}$")
    expected_version: int = Field(ge=1)


@router.get("/assist/sessions")
def sessions(principal: PrincipalDep) -> dict:
    return {"sessions": assist.list_sessions(principal)}


@router.post("/assist/sessions", status_code=201)
def request_session(principal: PrincipalDep) -> dict:
    return assist.request_session(principal)


@router.post("/assist/sessions/{session_id}/consent")
def consent(session_id: str, principal: PrincipalDep) -> dict:
    return assist.consent(principal, session_id)


@router.post("/assist/sessions/{session_id}/end")
def end(session_id: str, body: Completion, principal: PrincipalDep) -> dict:
    return assist.end_session(principal, session_id, body.model_dump())


@router.post("/assistant/messages")
def message(body: Message, principal: PrincipalDep) -> dict:
    return native_assistant.message(principal, body.text)


@router.post("/assistant/actions")
def action(body: Command, principal: PrincipalDep, key: IdempotencyKey) -> dict:
    return idempotent(principal, key, {"operation": "native_assistant", **body.model_dump()}, lambda: native_assistant.act(principal, **body.model_dump()))
