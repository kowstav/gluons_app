"""WhatsApp public signature boundary and authenticated owner setup/media reads."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from gluons.api.deps import PrincipalDep
from gluons.core import GluonsError
from gluons.whatsapp import service

router = APIRouter(tags=["WhatsApp"])


class Begin(BaseModel):
    account_type: str = "business_platform"
    capabilities: list[str] = Field(default_factory=lambda: ["read_messages"], max_length=10)


@router.post("/v1/whatsapp/sessions", status_code=201)
def begin_session(body: Begin, principal: PrincipalDep) -> dict:
    return service.begin_session(principal, capabilities=body.capabilities, account_type=body.account_type)


@router.get("/v1/webhooks/whatsapp")
def verify_webhook(mode: str = Query(alias="hub.mode"), token: str = Query(alias="hub.verify_token"),
                   challenge: str = Query(alias="hub.challenge")) -> PlainTextResponse:
    return PlainTextResponse(service.challenge(mode, token, challenge), headers={"Cache-Control": "no-store"})


@router.post("/v1/webhooks/whatsapp")
async def webhook(request: Request) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > service.MAX_WEBHOOK_BYTES:
            raise GluonsError("PAYLOAD_TOO_LARGE", "Webhook body exceeds the limit.", status=413)
    return service.receive_webhook(bytes(body), request.headers.get("X-Hub-Signature-256", ""))


@router.get("/v1/whatsapp/connections/{connection_id}/media/{media_id}")
def media(connection_id: str, media_id: str, principal: PrincipalDep) -> Response:
    content, mime = service.download_media(principal, connection_id, media_id)
    return Response(content, media_type=mime, headers={"Cache-Control": "no-store", "Content-Disposition": "attachment"})
