"""Gmail actions. The action contract is defined here; the connector registers below it."""
from __future__ import annotations

import base64
from email import policy as email_policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from gluons.actions import (
    ActionSpec,
    ConnectorContext,
    ProviderError,
    ProviderResult,
    Readback,
    classify_http,
    register_action,
    register_connector,
)
from gluons.core import sha256_hex
from gluons.net import GuardedClient

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
BASE = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


class SendEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: EmailStr
    subject: str = Field(min_length=1, max_length=998, pattern=r"^[^\r\n]+$")
    body: str = Field(min_length=1, max_length=100_000)
    thread_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,200}$")


def _message_id(key: str) -> str:
    return f"<gluons-{sha256_hex(key)}@receipts.gluons.invalid>"


def _content(payload: SendEmail, key: str, sender: str) -> bytes:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, str(payload.to), payload.subject
    message["Message-ID"] = _message_id(key)
    message.set_content(payload.body)
    return message.as_bytes()


class GmailSendConnector:
    def client(self, ctx: ConnectorContext) -> GuardedClient:
        return GuardedClient(("gmail.googleapis.com",), transport=ctx.transport)

    def _request(self, ctx: ConnectorContext, method: str, url: str, **kwargs: Any) -> dict:
        client = self.client(ctx)
        try:
            try:
                response = client.request(method, url, headers={"Authorization": f"Bearer {ctx.credentials()['access_token']}"}, **kwargs)
            except httpx.TransportError as exc:
                raise ProviderError("ambiguous" if method == "POST" else "retryable", "Gmail response could not be confirmed.") from exc
            if response.status_code >= 400:
                raise classify_http(response.status_code)
            return response.json()
        finally:
            client.close()

    def prepare(self, ctx: ConnectorContext, payload: BaseModel) -> dict:
        return {"would_send": payload.model_dump(), "provider": "gmail", "irreversible": True}

    def execute(self, ctx: ConnectorContext, payload: SendEmail, idempotency_key: str) -> ProviderResult:
        sender = ctx.metadata.get("account_email", "")
        if not sender:
            raise ProviderError("auth", "Reconnect and verify the Gmail sender identity.")
        raw = base64.urlsafe_b64encode(_content(payload, idempotency_key, sender)).decode()
        body = {"raw": raw}
        if payload.thread_id:
            body["threadId"] = payload.thread_id
        result = self._request(ctx, "POST", f"{BASE}/send", json=body)
        if not result.get("id"):
            raise ProviderError("ambiguous", "Gmail returned no message identifier.")
        return ProviderResult(result["id"], receipt={"message_id": _message_id(idempotency_key), "thread_id": result.get("threadId")})

    def lookup(self, ctx: ConnectorContext, payload: BaseModel, idempotency_key: str) -> ProviderResult | None:
        # Gmail search can lag. Absence is not proof a timed-out send did not land.
        result = self._request(ctx, "GET", BASE, params={"q": f"in:sent rfc822msgid:{_message_id(idempotency_key)}", "maxResults": 2})
        matches = result.get("messages", [])
        if len(matches) != 1:
            raise ProviderError("ambiguous", "Sent-mail lookup is inconclusive. A person must reconcile before any resend.")
        return ProviderResult(matches[0]["id"], receipt={"recovered_by_lookup": True, "message_id": _message_id(idempotency_key)})

    def verify(self, ctx: ConnectorContext, payload: SendEmail, result: ProviderResult) -> Readback:
        record = self._request(ctx, "GET", f"{BASE}/{result.provider_object_id}", params={"format": "raw"})
        raw = record.get("raw", "")
        try:
            message = BytesParser(policy=email_policy.default).parsebytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            body = message.get_body(preferencelist=("plain",))
            text = body.get_content() if body else ""
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProviderError("permanent", "Gmail readback was not a readable message.") from exc
        mismatches = []
        if [address.lower() for _, address in getaddresses(message.get_all("To", []))] != [str(payload.to).lower()]:
            mismatches.append("recipient differs")
        if str(message.get("Subject", "")) != payload.subject:
            mismatches.append("subject differs")
        if text.replace("\r\n", "\n").rstrip("\n") != payload.body.replace("\r\n", "\n").rstrip("\n"):
            mismatches.append("body differs")
        if "SENT" not in record.get("labelIds", []):
            mismatches.append("message is not in Sent mail")
        expected_message_id = result.receipt.get("message_id")
        if not expected_message_id or str(message.get("Message-ID", "")) != expected_message_id:
            mismatches.append("idempotency message identifier differs")
        return Readback(found=True, matches=not mismatches, record={"id": record.get("id"), "thread_id": record.get("threadId"), "label_ids": record.get("labelIds", []), "message_id": str(message.get("Message-ID", "")), "verification": "Gmail Sent mailbox readback; not recipient delivery proof"}, mismatches=tuple(mismatches))


register_action(
    ActionSpec(
        name="email.send",
        permission="send",
        risk_tier="R2",
        capability="email",
        input_model=SendEmail,
        description="Send an email to a customer",
        required_scopes={"gmail": (GMAIL_SEND_SCOPE, GMAIL_READ_SCOPE)},
        irreversible=True,
        resources=lambda p: {"recipient": str(p.to)},
    )
)

register_connector("gmail", "email.send", GmailSendConnector())
