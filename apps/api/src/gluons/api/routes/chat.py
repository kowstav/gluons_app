"""Native chat fallback. A signed-in web session only; assistants use MCP instead."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gluons import chat
from gluons.api.deps import PrincipalDep

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=chat.MAX_MESSAGE)
    confirmation_token: str | None = Field(default=None, max_length=4000)


@router.post("/chat")
def post_message(body: ChatMessage, principal: PrincipalDep) -> dict[str, Any]:
    return chat.respond(principal, body.message, confirmation_token=body.confirmation_token)
