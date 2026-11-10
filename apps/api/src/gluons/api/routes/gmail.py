"""Explicit mailbox read consent and authenticated Google event intake."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from gluons.api.deps import PrincipalDep
from gluons.connections import gmail_sync
from gluons.core import GluonsError

router = APIRouter(prefix="/v1", tags=["gmail"])


class ConfigureIntake(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automation_id: str = Field(min_length=1, max_length=40)
    labels: list[str] = Field(min_length=1, max_length=10)
    watch: bool = False
    pubsub_subscription: str | None = Field(default=None, max_length=300)


@router.get("/proofs/gmail/receipts/{receipt_id}/readback")
def proof_readback(receipt_id: str, principal: PrincipalDep) -> dict:
    from gluons.connections import gmail_proof

    return gmail_proof.readback(principal, receipt_id)


@router.get("/connections/{connection_id}/gmail/intake")
def status(connection_id: str, principal: PrincipalDep) -> dict:
    return gmail_sync.intake_status(principal, connection_id)


@router.post("/connections/{connection_id}/gmail/intake")
def configure(connection_id: str, body: ConfigureIntake, principal: PrincipalDep) -> dict:
    return gmail_sync.configure(principal, connection_id, **body.model_dump())


@router.delete("/connections/{connection_id}/gmail/intake/{automation_id}")
def disable(connection_id: str, automation_id: str, principal: PrincipalDep) -> dict:
    return gmail_sync.disable(principal, connection_id, automation_id)


@router.post("/webhooks/gmail")
async def push(request: Request) -> dict:
    from starlette.concurrency import run_in_threadpool
    # Verify the JWT before processing potentially sensitive message data.
    authorization = request.headers.get("authorization", "")
    await run_in_threadpool(gmail_sync.verify_push, authorization)
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > 16384:
            raise GluonsError("WEBHOOK_TOO_LARGE", "Notification exceeds the permitted size.", status=413)
    try:
        envelope = json.loads(body)
        if not isinstance(envelope, dict):
            raise ValueError
    except (ValueError, TypeError):
        raise GluonsError("VALIDATION_FAILED", "Invalid Pub/Sub notification.", status=422) from None
    # Network/database work is synchronous; keep it off the ASGI event loop.
    return await run_in_threadpool(gmail_sync.receive_push, authorization, envelope)
