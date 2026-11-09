"""Owner-requested Gmail readback of an existing gateway receipt; never sends mail."""
from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from gluons.actions import ConnectorContext, ProviderError, ProviderResult
from gluons.authz import Principal, Role
from gluons.connections.service import get_credential
from gluons.connectors.gmail import GMAIL_READ_SCOPE, GmailSendConnector, SendEmail, _message_id
from gluons.core import GluonsError, fingerprint, forbidden, not_found, utcnow
from gluons.db.models import ActionReceipt, Connection
from gluons.db.session import tenant_tx


def readback(principal: Principal, receipt_id: str, *, transport: Any = None) -> dict[str, Any]:
    """Read Sent mail again without changing the stored receipt or opening a write permit."""
    if principal.kind != "user" or principal.role != Role.OWNER or not principal.session_id:
        raise forbidden("Only an authenticated owner can request a Gmail proof readback.")
    principal.require("tasks:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        receipt = session.get(ActionReceipt, receipt_id)
        if receipt is None:
            raise not_found("Receipt")
        connection = session.get(Connection, receipt.connection_id) if receipt.connection_id else None
        if (receipt.provider != "gmail" or receipt.action != "email.send" or receipt.mode not in ("supervised", "live")
                or receipt.status != "succeeded" or not receipt.verified or receipt.verified_at is None or not receipt.provider_object_id
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", receipt.provider_object_id)):
            raise GluonsError("PROOF_RECEIPT_INVALID", "A verified Gmail send receipt is required.", status=409)
        if (connection is None or connection.provider != "gmail" or connection.environment != receipt.environment
                or receipt.environment not in ("simulator", "sandbox", "production") or connection.status != "connected"
                or connection.disconnected_at or connection.account_confirmed_at is None
                or GMAIL_READ_SCOPE not in connection.granted_scopes):
            raise GluonsError("PROOF_CONNECTION_UNAVAILABLE", "Reconnect and confirm the Gmail account and read scope.", status=409)
        try:
            payload = SendEmail.model_validate(receipt.expected)
        except ValidationError as exc:
            raise GluonsError("PROOF_RECEIPT_INVALID", "The stored Gmail payload cannot be verified.", status=409) from exc
        expected_hash = fingerprint({"action": receipt.action, "connection_id": receipt.connection_id, "input": receipt.expected})
        if (receipt.payload_hash != expected_hash or not isinstance(receipt.receipt, dict)
                or receipt.receipt.get("message_id") != _message_id(receipt.idempotency_key)):
            raise GluonsError("PROOF_RECEIPT_INVALID", "The stored Gmail evidence is inconsistent.", status=409)
        result = ProviderResult(receipt.provider_object_id, dict(receipt.receipt or {}))
        connection_id = connection.id
        environment = receipt.environment
        account_id = connection.external_account_id
        metadata = dict(connection.metadata_json or {})

    def credentials() -> dict[str, Any]:
        with tenant_tx(principal.tenant_id, principal.user_id) as session:
            current = session.get(Connection, connection_id, with_for_update=True)
            if (current is None or current.provider != "gmail" or current.environment != environment or current.external_account_id != account_id
                    or current.account_confirmed_at is None
                    or GMAIL_READ_SCOPE not in current.granted_scopes):
                raise GluonsError("PROOF_CONNECTION_UNAVAILABLE", "The Gmail connection is no longer available.", status=409)
            return get_credential(session, principal.tenant_id, current)

    context = ConnectorContext(principal.tenant_id, connection_id, "gmail", environment, account_id,
                               metadata, credentials, transport)
    try:
        observed = GmailSendConnector().verify(context, payload, result)
    except (ProviderError, GluonsError) as exc:
        raise GluonsError("PROOF_READBACK_UNAVAILABLE", "Gmail readback could not be completed; review the connection.", status=502) from exc
    return {
        "provider": "gmail",
        "environment": environment,
        "found": observed.found,
        "matches": observed.found and observed.matches and observed.record.get("id") == result.provider_object_id,
        "observed_at": utcnow().isoformat(),
        "scope": "sent_mail_readback_not_delivery",
        "account_identity_reverified": False,
        "receipt_modified": False,
    }
