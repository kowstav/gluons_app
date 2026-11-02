"""WhatsApp sends are gateway-only and verified from signed delivery webhooks.

An accepted Messages API response is not delivery evidence. Verification retries
the read until a signed delivered/read event for the exact message and recipient
arrives. Missing provider results never authorize an automatic resend.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from gluons.actions import (
    ActionSpec,
    ConnectorContext,
    ProviderError,
    ProviderResult,
    Readback,
    register_action,
    register_connector,
)
from gluons.core import canonical_json, new_id, sha256_hex, utcnow
from gluons.db.models import ChannelMessage, ChannelThread, Connection, Customer
from gluons.db.session import tenant_tx
from gluons.whatsapp.provider import MetaClient, identifier


class SendWhatsApp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(pattern=r"^\+[1-9][0-9]{5,19}$")
    kind: Literal["text", "template"] = "text"
    text: str | None = Field(default=None, min_length=1, max_length=4096)
    template_name: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")
    template_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(_[A-Z]{2})?$")
    template_parameters: list[str] = Field(default_factory=list, max_length=10)


def _callback(tenant_id: str, key: str) -> str:
    return "gluons-wa-" + sha256_hex(f"{tenant_id}:{key}")


def _body(payload: SendWhatsApp) -> str:
    return payload.text or "" if payload.kind == "text" else canonical_json({
        "template": payload.template_name, "language": payload.template_language, "parameters": payload.template_parameters})


def _gate(ctx: ConnectorContext, payload: SendWhatsApp) -> tuple[str, str, str]:
    with tenant_tx(ctx.tenant_id) as session:
        connection = session.get(Connection, ctx.connection_id)
        if connection is None or connection.provider != "whatsapp_business" or connection.status != "connected" or connection.disconnected_at or not connection.account_confirmed_at:
            raise ProviderError("auth", "Verify and confirm the WhatsApp Business connection first.")
        customer = session.execute(select(Customer).where(Customer.phones.contains([payload.to]))).scalars().all()
        if len(customer) != 1 or customer[0].suppressed or "whatsapp" not in customer[0].opted_in_channels:
            raise ProviderError("permanent", "Verified customer WhatsApp opt-in is required; suppressed contacts cannot be messaged.")
        thread = session.execute(select(ChannelThread).where(ChannelThread.connection_id == connection.id,
            ChannelThread.channel == "whatsapp", ChannelThread.counterparty == payload.to)).scalar_one_or_none()
        if payload.kind == "text":
            if not payload.text or payload.template_name or payload.template_parameters:
                raise ProviderError("permanent", "A free-form text reply requires text without template parameters.")
            if thread is None or thread.service_window_expires_at is None or thread.service_window_expires_at <= utcnow():
                raise ProviderError("permanent", "The customer-service window is closed. Use an approved template with opt-in.")
        elif not payload.template_name or not payload.template_language or payload.text or any(len(v) > 1000 for v in payload.template_parameters):
            raise ProviderError("permanent", "Provide an approved template name/language and bounded body parameters.")
        if thread is None:
            thread = ChannelThread(id=new_id("thr"), tenant_id=ctx.tenant_id, channel="whatsapp", connection_id=connection.id,
                external_thread_id=f"{connection.external_account_id}:{payload.to}", counterparty=payload.to, customer_id=customer[0].id)
            session.add(thread)
            session.flush()
        return thread.id, identifier(connection.external_account_id), identifier(str(connection.metadata_json.get("account_id", "")))


class WhatsAppSendConnector:
    def client(self, ctx: ConnectorContext):  # type: ignore[no-untyped-def]
        return MetaClient(ctx.credentials()["access_token"], transport=ctx.transport).client

    def prepare(self, ctx: ConnectorContext, payload: BaseModel) -> dict:
        return {"would_send": payload.model_dump(), "provider": "whatsapp_business", "external_write": False,
                "requirements": ["signed action policy", "customer opt-in", "open service window or approved template", "signed delivery evidence"]}

    def execute(self, ctx: ConnectorContext, payload: SendWhatsApp, idempotency_key: str) -> ProviderResult:
        thread_id, phone_id, account_id = _gate(ctx, payload)
        client = MetaClient(ctx.credentials()["access_token"], transport=ctx.transport)
        callback = _callback(ctx.tenant_id, idempotency_key)
        try:
            if payload.kind == "template":
                templates = client.call("GET", f"{account_id}/message_templates", params={"name": payload.template_name,
                    "fields": "name,language,status,components", "limit": 100}).get("data", [])
                template = next((t for t in templates if t.get("name") == payload.template_name and t.get("language") == payload.template_language and t.get("status") == "APPROVED"), None)
                if template is None:
                    raise ProviderError("permanent", "Meta has not confirmed this exact template and language as approved.")
                # Rich media/buttons are deliberately not inferred from arbitrary model output.
                if any(c.get("type") != "BODY" for c in template.get("components", [])):
                    raise ProviderError("permanent", "This template's components require a supported typed adapter.")
                import re
                placeholders = set(re.findall(r"\{\{([0-9]+)\}\}", " ".join(c.get("text", "") for c in template.get("components", []))))
                if placeholders != {str(i) for i in range(1, len(payload.template_parameters) + 1)}:
                    raise ProviderError("permanent", "Template parameters do not match the approved body.")
            # Persist the callback binding before the send; a webhook may beat its response.
            with tenant_tx(ctx.tenant_id) as session:
                message = session.execute(select(ChannelMessage).where(ChannelMessage.channel == "whatsapp", ChannelMessage.subject == callback).with_for_update()).scalar_one_or_none()
                if message is None:
                    message = ChannelMessage(id=new_id("msg"), tenant_id=ctx.tenant_id, thread_id=thread_id, channel="whatsapp", direction="outbound",
                        recipient=payload.to, subject=callback, body=_body(payload), status="pending")
                    session.add(message)
                elif message.external_message_id:
                    return ProviderResult(message.external_message_id, receipt={"callback_id": callback, "recovered_by_lookup": True})
                elif message.recipient != payload.to or message.body != _body(payload):
                    raise ProviderError("permanent", "The message changed under an existing idempotency key.")
                session.flush()
                message_id = message.id
            body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": payload.to.lstrip("+"),
                    "type": payload.kind, "biz_opaque_callback_data": callback}
            if payload.kind == "text":
                body["text"] = {"body": payload.text, "preview_url": False}
            else:
                body["template"] = {"name": payload.template_name, "language": {"code": payload.template_language},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": value} for value in payload.template_parameters]}] if payload.template_parameters else []}
            try:
                response = client.call("POST", f"{phone_id}/messages", json=body)
            except ProviderError as exc:
                if exc.status is not None and exc.status >= 500:
                    raise ProviderError("ambiguous", "Meta could not confirm whether the message was sent.") from exc
                raise
            provider_id = (response.get("messages") or [{}])[0].get("id")
            if not provider_id:
                raise ProviderError("ambiguous", "Meta returned no message ID. Reconcile before resending.")
            with tenant_tx(ctx.tenant_id) as session:
                message = session.get(ChannelMessage, message_id, with_for_update=True)
                if message.external_message_id and message.external_message_id != provider_id:
                    raise ProviderError("ambiguous", "Delivery webhook and send response disagree; human reconciliation is required.")
                message.external_message_id = provider_id
                if message.status == "pending":
                    message.status = "accepted"
            return ProviderResult(provider_id, receipt={"callback_id": callback, "status": "accepted", "delivery_verified": False})
        finally:
            client.close()

    def lookup(self, ctx: ConnectorContext, payload: BaseModel, idempotency_key: str) -> ProviderResult | None:
        with tenant_tx(ctx.tenant_id) as session:
            row = session.execute(select(ChannelMessage).where(ChannelMessage.channel == "whatsapp",
                ChannelMessage.subject == _callback(ctx.tenant_id, idempotency_key))).scalar_one_or_none()
            if row and row.external_message_id:
                return ProviderResult(row.external_message_id, receipt={"callback_id": row.subject, "recovered_by_lookup": True})
        raise ProviderError("ambiguous", "No conclusive signed provider outcome exists. A person must reconcile before resending.")

    def verify(self, ctx: ConnectorContext, payload: SendWhatsApp, result: ProviderResult) -> Readback:
        with tenant_tx(ctx.tenant_id) as session:
            row = session.execute(select(ChannelMessage).join(ChannelThread).where(ChannelMessage.channel == "whatsapp",
                ChannelMessage.direction == "outbound", ChannelMessage.external_message_id == result.provider_object_id,
                ChannelThread.connection_id == ctx.connection_id)).scalar_one_or_none()
            if row is None or row.status not in {"delivered", "read", "failed", "mismatch"}:
                raise ProviderError("retryable", "Awaiting an authenticated WhatsApp delivery webhook.", retry_after=30)
            matches = row.status in {"delivered", "read"} and row.recipient == payload.to and row.body == _body(payload)
            return Readback(found=True, matches=matches, record={"provider_message_id": row.external_message_id,
                "status": row.status, "recipient": row.recipient, "evidence": "signed Meta delivery status; provider does not expose message-content readback"},
                mismatches=() if matches else (row.provider_error or "message did not reach the approved recipient",))


register_action(ActionSpec(name="whatsapp.message.send", permission="send", risk_tier="R2", capability="messaging", input_model=SendWhatsApp,
    description="Send an opted-in customer a permitted WhatsApp message", required_scopes={"whatsapp_business": ("whatsapp_business_messaging", "whatsapp_business_management")},
    irreversible=True, resources=lambda p: {"recipient": p.to}))
register_connector("whatsapp_business", "whatsapp.message.send", WhatsAppSendConnector())
