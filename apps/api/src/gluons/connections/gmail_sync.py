"""Durable Gmail ingestion. Google consent, owner read grants and action policies are separate.

Only the owner setup/renewal path can create a mailbox watch. Shadow intake polls
read-only APIs. Provider cursors advance only with durable message work in the same
transaction; message dispatch is idempotent across crashes and redelivery.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses
from typing import Any

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from gluons import audit, events, queue, vault
from gluons.authz import Principal, Role
from gluons.config import get_settings
from gluons.connections.google import READ_SCOPE
from gluons.connections.service import get_credential
from gluons.core import (
    GluonsError,
    canonical_json,
    constant_time_equals,
    fingerprint,
    hmac_sha256_hex,
    new_id,
    not_found,
    sha256_hex,
    utcnow,
    validation_failed,
)
from gluons.db.models import (
    Artifact,
    Automation,
    ChannelMessage,
    ChannelThread,
    Connection,
    Organization,
    ProviderEventSubscription,
    WebhookInbox,
)
from gluons.db.session import operation_lock, system_tx, tenant_tx
from gluons.net import GuardedClient, ResponseTooLarge, write_permit
from gluons.runtime import lifecycle
from gluons.skills.domain.documents import ExtractionLimits, extract_bytes

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_MESSAGE_BYTES = 12 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS = 10
MAX_BODY_CHARACTERS = 100_000
MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,180}")
SUBSCRIPTION = re.compile(r"projects/([^/]+)/subscriptions/[A-Za-z][A-Za-z0-9._~+%-]{2,254}")
_jwks_cache: tuple[float, list[dict]] = (0, [])


def _error(code: str, message: str, status: int = 409) -> GluonsError:
    return GluonsError(code, message, status=status)


def _sign(grant: dict) -> str:
    settings = get_settings()
    return hmac_sha256_hex(settings.key_bytes(settings.signing_key_b64), canonical_json(grant))


def _connection(session, connection_id: str) -> Connection:
    row = session.get(Connection, connection_id)
    if row is None:
        raise not_found("Connection")
    if row.provider != "gmail" or row.status != "connected" or row.disconnected_at or row.account_confirmed_at is None or READ_SCOPE not in row.granted_scopes:
        raise _error("GMAIL_CONNECTION_REQUIRED", "Connect, confirm and authorize Gmail readonly access first.")
    return row


def _grants(session, connection: Connection, *, watch_only: bool = False) -> list[Automation]:
    org = session.get(Organization, connection.tenant_id)
    if org is None or org.emergency_stop_at:
        return []
    result = []
    for automation in session.execute(select(Automation).where(Automation.status.in_(("active", "trial_shadow")))).scalars():
        record = automation.config.get("gmail_ingestion") or {}
        grant = record.get("grant") or {}
        if not isinstance(grant, dict) or not isinstance(record.get("signature"), str):
            continue
        if not constant_time_equals(record["signature"], _sign(grant)):
            continue
        if grant.get("connection_id") != connection.id or grant.get("tenant_id") != connection.tenant_id or grant.get("automation_id") != automation.id:
            continue
        try:
            if datetime.fromisoformat(grant["expires_at"]) <= utcnow():
                continue
        except (KeyError, ValueError, TypeError):
            continue
        if watch_only and (not grant.get("watch") or automation.mode not in ("supervised", "live")):
            continue
        result.append(automation)
    return result


def _request(tenant_id: str, connection_id: str, path: str, *, method: str = "GET", params: dict | None = None,
             body: dict | None = None, transport: Any = None, permit: bool = False) -> dict:
    with tenant_tx(tenant_id) as session:
        connection = _connection(session, connection_id)
        credential = get_credential(session, tenant_id, connection)
    client = GuardedClient(("gmail.googleapis.com",), transport=transport, headers={"Authorization": f"Bearer {credential['access_token']}"})
    try:
        try:
            if permit:
                with write_permit("owner_gmail_watch_consent"):
                    response = client.request(method, BASE + path, params=params, json=body)
            elif method == "GET":
                raw, _, status = client.get_bytes(BASE + path, max_bytes=MAX_MESSAGE_BYTES, params=params)
                response = httpx.Response(status, content=raw)
            else:
                response = client.request(method, BASE + path, params=params)
        except ResponseTooLarge:
            raise _error("GMAIL_MESSAGE_LIMIT", "The Gmail response exceeds intake limits.", 422) from None
        except httpx.HTTPError:
            raise _error("GMAIL_UNAVAILABLE", "Gmail is unavailable; durable intake will retry.", 502) from None
        if response.status_code == 404:
            raise _error("GMAIL_NOT_FOUND", "The Gmail history cursor or message is no longer available.", 404)
        if response.status_code in (401, 403):
            raise _error("GMAIL_RECONNECT_REQUIRED", "Google refused mailbox access. Reconnect in Gluons.")
        if response.status_code >= 400:
            raise _error("GMAIL_UNAVAILABLE", "Gmail rejected the request; durable intake will retry.", 502)
        if len(response.content) > MAX_MESSAGE_BYTES:
            raise _error("GMAIL_MESSAGE_LIMIT", "The Gmail response exceeds intake limits.", 422)
        try:
            result = response.json()
        except ValueError:
            raise _error("GMAIL_INVALID_RESPONSE", "Gmail returned an invalid response.", 502) from None
        if not isinstance(result, dict):
            raise _error("GMAIL_INVALID_RESPONSE", "Gmail returned an invalid response.", 502)
        return result
    finally:
        client.close()


def configure(principal: Principal, connection_id: str, automation_id: str, *, labels: list[str], watch: bool = False,
              pubsub_subscription: str | None = None, transport: Any = None) -> dict:
    principal.require("connections:manage")
    if principal.kind != "user" or principal.role != Role.OWNER or not principal.session_id:
        raise _error("OWNER_WEB_REQUIRED", "The owner must approve mailbox intake in Gluons Web.", 403)
    if not labels or len(labels) > 10 or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", label) for label in labels):
        raise validation_failed("Choose one to ten explicit Gmail label IDs.")
    labels = sorted(set(labels))
    settings = get_settings()
    if watch:
        match = SUBSCRIPTION.fullmatch(pubsub_subscription or "")
        if not (match and settings.gmail_pubsub_topic and settings.gmail_push_audience and settings.gmail_push_service_account):
            raise _error("CONFIGURATION_REQUIRED", "Configure a Gmail Pub/Sub topic, authenticated subscription, audience and push service account.")
        if settings.gmail_pubsub_topic.split("/")[1] != match.group(1):
            raise validation_failed("The subscription must belong to the configured Gmail topic's project.")
    with operation_lock(f"gmail.setup:{principal.tenant_id}:{connection_id}") as acquired:
        if not acquired:
            raise _error("GMAIL_BUSY", "Mailbox configuration is already running.")
        with tenant_tx(principal.tenant_id, principal.user_id) as session:
            connection = _connection(session, connection_id)
            automation = session.get(Automation, automation_id, with_for_update=True)
            if automation is None:
                raise not_found("Automation")
            if automation.skill_id != "multichannel_order_entry":
                raise validation_failed("Gmail intake currently supports the shared order skill.")
            if watch and automation.mode in ("shadow", "historical_replay"):
                raise _error("MODE_FORBIDS_EXTERNAL_WRITES", "Shadow intake uses read-only polling; it cannot create a provider watch.")
            existing = session.execute(select(ProviderEventSubscription).where(ProviderEventSubscription.connection_id == connection_id)).scalars().all()
            for row in existing:
                if row.status == "active":
                    cursor = json.loads(row.cursor or "{}")
                    if cursor.get("labels") != labels:
                        raise _error("GMAIL_LABEL_CONFLICT", "This mailbox already has an intake grant for different labels. Disable it before changing scope.")
            grant = {"tenant_id": principal.tenant_id, "connection_id": connection.id, "automation_id": automation.id,
                     "labels": labels, "watch": watch, "approved_by": principal.actor_id, "approved_at": utcnow().isoformat(),
                     "expires_at": (utcnow() + timedelta(days=90)).isoformat()}
            automation.config = {**automation.config, "gmail_ingestion": {"grant": grant, "signature": _sign(grant)}}
            automation.version += 1
            row = next((item for item in existing if item.status == "active"), None)
            if row is None:
                row = ProviderEventSubscription(id=new_id("sub"), tenant_id=principal.tenant_id, connection_id=connection.id,
                                                topic=pubsub_subscription if watch else "gmail.poll", routing_key=connection.account_display.lower(),
                                                cursor=canonical_json({"resync": True, "labels": labels}), status="active")
                session.add(row)
                session.flush()
            subscription_id = row.id
            audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="gmail.intake_granted",
                         actor=principal, resource_type="automation", resource_id=automation.id,
                         data={"connection_id": connection.id, "labels": labels, "watch": watch})
        # The owner explicitly requested this administrative subscription. Task
        # execution never creates a watch, and shadow cannot take this branch.
        if watch:
            result = _request(principal.tenant_id, connection_id, "/watch", method="POST", permit=True,
                              body={"topicName": settings.gmail_pubsub_topic, "labelIds": labels, "labelFilterBehavior": "INCLUDE"}, transport=transport)
            _record_watch(principal.tenant_id, subscription_id, result, pubsub_subscription)
        with tenant_tx(principal.tenant_id) as session:
            queue.enqueue(session, "gmail.sync", {"connection_id": connection_id}, tenant_id=principal.tenant_id,
                          dedupe_key=f"gmail.sync:{connection_id}")
        return {"subscription_id": subscription_id, "connection_id": connection_id, "automation_id": automation_id,
                "status": "configured", "mode": "push" if watch else "read_only_polling", "labels": labels,
                "notice": "Intake permission does not grant order submission or customer email permission."}


def _record_watch(tenant_id: str, subscription_id: str, result: dict, topic: str | None = None) -> None:
    if not str(result.get("historyId", "")).isdigit() or not str(result.get("expiration", "")).isdigit():
        raise _error("GMAIL_INVALID_RESPONSE", "Google did not confirm the watch history and expiry.", 502)
    with tenant_tx(tenant_id) as session:
        row = session.get(ProviderEventSubscription, subscription_id, with_for_update=True)
        if row is None or row.status != "active":
            raise _error("GMAIL_SUBSCRIPTION_DISABLED", "Mailbox intake was disabled during watch setup.")
        if topic:
            row.topic = topic
        row.expires_at = datetime.fromtimestamp(int(result["expiration"]) / 1000, UTC)
        row.last_renewed_at = utcnow()


def disable(principal: Principal, connection_id: str, automation_id: str) -> dict:
    with operation_lock(f"gmail.grant:{principal.tenant_id}:{automation_id}") as acquired:
        if not acquired:
            raise _error("GMAIL_BUSY", "This intake grant is currently dispatching a message. Retry shortly.")
        return _disable_locked(principal, connection_id, automation_id)


def intake_status(principal: Principal, connection_id: str) -> dict:
    principal.require("connections:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        connection = _connection(session, connection_id)
        subscriptions = session.execute(select(ProviderEventSubscription).where(ProviderEventSubscription.connection_id == connection_id)).scalars().all()
        return {"connection_id": connection_id, "active_automation_ids": [row.id for row in _grants(session, connection)],
                "subscriptions": [{"id": row.id, "status": row.status,
                                   "mode": "read_only_polling" if row.topic == "gmail.poll" else "authenticated_push",
                                   "labels": json.loads(row.cursor or "{}").get("labels", []),
                                   "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                                   "last_renewed_at": row.last_renewed_at.isoformat() if row.last_renewed_at else None,
                                   "last_reconciled_at": row.last_event_at.isoformat() if row.last_event_at else None,
                                   "watch_expired": row.topic != "gmail.poll" and (row.expires_at is None or row.expires_at <= utcnow())}
                                  for row in subscriptions]}


def _disable_locked(principal: Principal, connection_id: str, automation_id: str) -> dict:
    principal.require("connections:manage")
    if principal.kind != "user" or principal.role != Role.OWNER:
        raise _error("OWNER_WEB_REQUIRED", "The owner must change mailbox intake in Gluons Web.", 403)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = session.get(Automation, automation_id, with_for_update=True)
        if automation is None:
            raise not_found("Automation")
        record = automation.config.get("gmail_ingestion") or {}
        if (record.get("grant") or {}).get("connection_id") != connection_id:
            raise not_found("Intake grant")
        automation.config = {key: value for key, value in automation.config.items() if key != "gmail_ingestion"}
        automation.version += 1
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="gmail.intake_disabled", actor=principal,
                     resource_type="automation", resource_id=automation_id, data={"connection_id": connection_id})
    return {"status": "disabled", "automation_id": automation_id}


def sync_connection(tenant_id: str, connection_id: str, *, transport: Any = None) -> dict:
    with operation_lock(f"gmail.sync:{tenant_id}:{connection_id}") as acquired:
        if not acquired:
            raise _error("GMAIL_BUSY", "A mailbox reconciliation is already running.")
        with tenant_tx(tenant_id) as session:
            connection = _connection(session, connection_id)
            if not _grants(session, connection):
                return {"status": "no_active_owner_grant"}
            subscription = session.execute(select(ProviderEventSubscription).where(ProviderEventSubscription.connection_id == connection_id,
                                                                                   ProviderEventSubscription.status == "active")).scalar_one_or_none()
            if subscription is None:
                return {"status": "disabled"}
            subscription_id = subscription.id
            state = json.loads(subscription.cursor or "{}")
        if state.get("resync") and not state.get("resync_history"):
            profile = _request(tenant_id, connection_id, "/profile", transport=transport)
            if not str(profile.get("historyId", "")).isdigit():
                raise _error("GMAIL_INVALID_RESPONSE", "Gmail did not provide a mailbox history cursor.", 502)
            state["resync_history"] = str(profile["historyId"])
        full = bool(state.get("resync"))
        params: dict = {"maxResults": 50}
        if state.get("page"):
            params["pageToken"] = state["page"]
        if full:
            params["labelIds"] = state["labels"]
            path = "/messages"
        else:
            params.update(startHistoryId=state["history"], historyTypes="messageAdded")
            path = "/history"
        try:
            response = _request(tenant_id, connection_id, path, params=params, transport=transport)
        except GluonsError as exc:
            if exc.code != "GMAIL_NOT_FOUND" or full:
                raise
            with tenant_tx(tenant_id) as session:
                row = session.get(ProviderEventSubscription, subscription_id, with_for_update=True)
                row.cursor = canonical_json({"labels": state["labels"], "resync": True})
            return sync_connection_unlocked(tenant_id, connection_id)
        if full:
            ids = [item.get("id") for item in response.get("messages", [])]
        else:
            ids = [added.get("message", {}).get("id") for item in response.get("history", []) for added in item.get("messagesAdded", [])]
        ids = sorted({item for item in ids if isinstance(item, str) and MESSAGE_ID.fullmatch(item)})
        if len(ids) > 500:
            raise _error("GMAIL_PAGE_LIMIT", "Gmail history page exceeds the safe processing limit; cursor retained.", 422)
        with tenant_tx(tenant_id) as session:
            connection = _connection(session, connection_id)
            if not _grants(session, connection):
                return {"status": "no_active_owner_grant"}
            row = session.get(ProviderEventSubscription, subscription_id, with_for_update=True)
            if row is None or row.status != "active":
                return {"status": "disabled"}
            for message_id in ids:
                queue.enqueue(session, "gmail.ingest", {"connection_id": connection_id, "message_id": message_id}, tenant_id=tenant_id,
                              dedupe_key=f"gmail.ingest:{connection_id}:{message_id}")
            next_page = response.get("nextPageToken")
            if next_page:
                state["page"] = next_page
            else:
                cursor = state.get("resync_history") if full else str(response.get("historyId", state.get("history", "")))
                if not str(cursor).isdigit():
                    raise _error("GMAIL_INVALID_RESPONSE", "Gmail omitted the final reconciliation cursor.", 502)
                state = {"labels": state["labels"], "history": cursor}
                for inbox in session.execute(select(WebhookInbox).where(WebhookInbox.connection_id == connection_id, WebhookInbox.provider == "gmail", WebhookInbox.status == "queued")).scalars():
                    if int(inbox.payload.get("history_id", "0")) <= int(cursor):
                        inbox.status = "processed"
                        inbox.processed_at = utcnow()
            row.cursor = canonical_json(state)
            row.last_event_at = utcnow()
            # This distinct continuation key survives the currently leased sync
            # job. It is safe if maintenance also schedules the connection.
            if next_page:
                queue.enqueue(session, "gmail.sync", {"connection_id": connection_id}, tenant_id=tenant_id,
                              dedupe_key=f"gmail.page:{connection_id}:{fingerprint(state)}")
        return {"status": "page_queued" if next_page else "synchronized", "messages_queued": len(ids), "full_resync": full}


def sync_connection_unlocked(tenant_id: str, connection_id: str) -> dict:
    # Called after stale history under an already held reconciliation lock. Queue
    # a fresh pass instead of recursively taking the same advisory lock.
    with tenant_tx(tenant_id) as session:
        queue.enqueue(session, "gmail.sync", {"connection_id": connection_id}, tenant_id=tenant_id,
                      dedupe_key=f"gmail.resync:{connection_id}:{new_id('pass')}")
    return {"status": "full_resync_required"}


def _decode(encoded: str, *, limit: int) -> bytes:
    if not isinstance(encoded, str) or len(encoded) > (limit + 2) * 4 // 3 + 8:
        raise _error("GMAIL_CONTENT_LIMIT", "A message part exceeds intake limits.", 422)
    try:
        value = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise _error("GMAIL_INVALID_CONTENT", "Gmail returned invalid message encoding.", 422) from None
    if len(value) > limit:
        raise _error("GMAIL_CONTENT_LIMIT", "A message part exceeds intake limits.", 422)
    return value


def _parts(part: dict, *, depth: int = 0) -> list[dict]:
    if depth > 15:
        raise _error("GMAIL_CONTENT_LIMIT", "The MIME nesting exceeds intake limits.", 422)
    nested = part.get("parts", [])
    if not isinstance(nested, list) or len(nested) > 100:
        raise _error("GMAIL_CONTENT_LIMIT", "The MIME part count exceeds intake limits.", 422)
    result = [part]
    for item in nested:
        if not isinstance(item, dict):
            raise _error("GMAIL_INVALID_CONTENT", "Gmail returned invalid MIME parts.", 422)
        result.extend(_parts(item, depth=depth + 1))
        if len(result) > 250:
            raise _error("GMAIL_CONTENT_LIMIT", "The MIME part count exceeds intake limits.", 422)
    return result


def ingest_message(tenant_id: str, connection_id: str, message_id: str, *, transport: Any = None) -> dict:
    if not MESSAGE_ID.fullmatch(message_id):
        raise validation_failed("Invalid Gmail message identifier.")
    with operation_lock(f"gmail.message:{tenant_id}:{connection_id}:{message_id}") as acquired:
        if not acquired:
            raise _error("GMAIL_BUSY", "This mailbox message is already being ingested.")
        external_id = f"{connection_id}:{message_id}"
        with tenant_tx(tenant_id) as session:
            connection = _connection(session, connection_id)
            automations = _grants(session, connection)
            if not automations:
                return {"status": "no_active_owner_grant"}
            saved = session.execute(select(ChannelMessage).where(ChannelMessage.channel == "email", ChannelMessage.external_message_id == external_id)).scalar_one_or_none()
            stored = {"text": saved.body, "sender": saved.sender, "thread_id": saved.metadata_json.get("provider_thread_id"),
                      "labels": saved.metadata_json.get("gmail_labels", []),
                      "attachment_review_required": saved.metadata_json.get("attachment_review_required", True),
                      "attachment_ids": [a["artifact_id"] for a in saved.attachments if a.get("artifact_id")]} if saved else None
        if stored is None:
            try:
                document = _request(tenant_id, connection_id, f"/messages/{message_id}", params={"format": "full"}, transport=transport)
            except GluonsError as exc:
                if exc.code == "GMAIL_NOT_FOUND":
                    return {"status": "deleted_before_ingestion"}
                raise
            labels = set(document.get("labelIds", []))
            allowed = [a for a in automations if labels.intersection(a.config["gmail_ingestion"]["grant"]["labels"])]
            if not allowed or labels.intersection({"SENT", "DRAFT", "SPAM", "TRASH"}):
                return {"status": "outside_owner_label_scope"}
            headers = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in document.get("payload", {}).get("headers", [])}
            sender = next((address for _, address in getaddresses([headers.get("from", "")]) if address), "")
            if len(sender) > 320:
                raise _error("GMAIL_INVALID_CONTENT", "The sender address exceeds intake limits.", 422)
            text_parts, attachments = [], []
            total_bytes = 0
            for part in _parts(document.get("payload", {})):
                body = part.get("body") or {}
                filename = str(part.get("filename", ""))[:300]
                if not filename and part.get("mimeType") == "text/plain" and body.get("data"):
                    text_parts.append(_decode(body["data"], limit=MAX_BODY_CHARACTERS).decode("utf-8", errors="replace"))
                elif filename:
                    if len(attachments) >= MAX_ATTACHMENTS:
                        raise _error("GMAIL_CONTENT_LIMIT", "The message has too many attachments.", 422)
                    size = int(body.get("size", 0))
                    if size < 0 or size > MAX_ATTACHMENT_BYTES or total_bytes + size > MAX_MESSAGE_BYTES:
                        attachments.append({"filename": filename, "status": "requires_manual_review_size"})
                        continue
                    encoded = body.get("data")
                    if body.get("attachmentId"):
                        attachment_id = str(body["attachmentId"])
                        if not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", attachment_id):
                            raise _error("GMAIL_INVALID_CONTENT", "Invalid attachment identifier.", 422)
                        encoded = _request(tenant_id, connection_id, f"/messages/{message_id}/attachments/{attachment_id}", transport=transport).get("data")
                    data = _decode(encoded or "", limit=MAX_ATTACHMENT_BYTES)
                    total_bytes += len(data)
                    if total_bytes > MAX_MESSAGE_BYTES:
                        raise _error("GMAIL_CONTENT_LIMIT", "Message attachments exceed aggregate intake limits.", 422)
                    item = {"filename": filename, "content_type": str(part.get("mimeType", "application/octet-stream"))[:100], "data": data,
                            "size_bytes": len(data), "sha256": sha256_hex(data), "status": "stored_for_review"}
                    try:
                        extracted = extract_bytes(data, filename=filename, limits=ExtractionLimits(max_bytes=MAX_ATTACHMENT_BYTES, max_pages=30, max_characters=MAX_BODY_CHARACTERS))
                        text_parts.append(extracted["text"])
                        item.update(status="extracted_untrusted", extractor=extracted["extractor"], warnings=extracted["warnings"])
                    except GluonsError as exc:
                        item["extraction_status"] = exc.code
                    attachments.append(item)
            text = "\n".join(text_parts)
            if len(text) > MAX_BODY_CHARACTERS:
                raise _error("GMAIL_CONTENT_LIMIT", "The extracted message exceeds intake limits.", 422)
            review_required = any(item.get("status") != "extracted_untrusted" for item in attachments)
            if review_required:
                # Never silently process only part of a customer's order.
                text = "[Attachment requires human review; automatic order submission is not permitted.]\n" + text
            thread_id = str(document.get("threadId") or message_id)
            if not MESSAGE_ID.fullmatch(thread_id):
                raise _error("GMAIL_INVALID_CONTENT", "Invalid Gmail thread identifier.", 422)
            with tenant_tx(tenant_id) as session:
                connection = _connection(session, connection_id)
                if not _grants(session, connection):
                    return {"status": "no_active_owner_grant"}
                thread_external = f"{connection_id}:{thread_id}"
                thread = session.execute(select(ChannelThread).where(ChannelThread.channel == "email", ChannelThread.external_thread_id == thread_external)).scalar_one_or_none()
                if thread is None:
                    thread = ChannelThread(id=new_id("thr"), tenant_id=tenant_id, channel="email", connection_id=connection_id,
                                           external_thread_id=thread_external, counterparty=sender)
                    session.add(thread)
                    session.flush()
                saved_attachments = []
                for item in attachments:
                    content = item.pop("data", None)
                    if content is not None:
                        secret_ref = vault.store(session, tenant_id, "gmail_attachment", {"data_b64": base64.b64encode(content).decode()})
                        artifact = Artifact(id=new_id("art"), tenant_id=tenant_id, kind="gmail_attachment", filename=item["filename"],
                                            content_type=item["content_type"], size_bytes=item["size_bytes"], sha256=item["sha256"], storage_uri=f"vault://{secret_ref}")
                        session.add(artifact)
                        item["artifact_id"] = artifact.id
                    saved_attachments.append(item)
                saved = ChannelMessage(id=new_id("msg"), tenant_id=tenant_id, thread_id=thread.id, channel="email", direction="inbound",
                                       external_message_id=external_id, sender=sender, recipient=connection.account_display, subject=headers.get("subject", "")[:2000],
                                       body=text, attachments=saved_attachments, classification="order_candidate", status="needs_review" if review_required else "received",
                                       metadata_json={"gmail_labels": sorted(labels), "provider_thread_id": thread_id, "attachment_review_required": review_required})
                session.add(saved)
                if review_required:
                    events.notify(session, tenant_id, kind="gmail_attachment_review", title="An incoming order email needs attachment review",
                                  body="The complete message was retained. No order run was started because an attachment could not be safely processed.",
                                  severity="action", link="/connections", resource_type="channel_message", resource_id=saved.id)
                thread.last_message_at = utcnow()
                connection.last_event_at = utcnow()
                stored = {"text": text, "sender": sender, "thread_id": thread_id, "labels": sorted(labels), "attachment_review_required": review_required,
                          "attachment_ids": [item["artifact_id"] for item in saved_attachments if item.get("artifact_id")]}
        with tenant_tx(tenant_id) as session:
            connection = _connection(session, connection_id)
            eligible = _grants(session, connection)
        runs = []
        for automation in eligible:
            # Existing messages remain data only when a new grant has different
            # labels. Scope labels are preserved on the signed grant and message.
            grant_labels = set(automation.config["gmail_ingestion"]["grant"]["labels"])
            if stored.get("labels") is not None and not grant_labels.intersection(stored["labels"]):
                continue
            if stored.get("attachment_review_required"):
                continue
            with operation_lock(f"gmail.grant:{tenant_id}:{automation.id}") as dispatch_allowed:
                if not dispatch_allowed:
                    raise _error("GMAIL_BUSY", "The intake grant is being changed. Retry shortly.")
                with tenant_tx(tenant_id) as session:
                    current_connection = _connection(session, connection_id)
                    current_ids = {row.id for row in _grants(session, current_connection)}
                if automation.id not in current_ids:
                    continue
                runs.append(lifecycle.submit_event(tenant_id, automation.id, source="email", source_event_id=f"gmail:{external_id}",
                                                  payload={**stored, "channel": "email", "connection_id": connection_id}, actor="gmail_ingestion"))
        with tenant_tx(tenant_id) as session:
            saved = session.execute(select(ChannelMessage).where(ChannelMessage.external_message_id == external_id, ChannelMessage.channel == "email")).scalar_one()
            saved.status = "needs_review" if stored.get("attachment_review_required") else "ingested"
        return {"status": "needs_review" if stored.get("attachment_review_required") else "ingested", "runs": runs, "message_id": message_id}


def _keys() -> list[dict]:
    global _jwks_cache
    if time.monotonic() < _jwks_cache[0]:
        return _jwks_cache[1]
    client = GuardedClient(("www.googleapis.com",), timeout=10)
    try:
        raw, _, status = client.get_bytes("https://www.googleapis.com/oauth2/v3/certs", max_bytes=65536)
        if status != 200:
            raise ValueError("Google signing keys unavailable")
        keys = json.loads(raw).get("keys", [])
        if not isinstance(keys, list) or len(keys) > 20:
            raise ValueError("Invalid Google signing key response")
        _jwks_cache = (time.monotonic() + 300, keys)
        return keys
    finally:
        client.close()


def verify_push(authorization: str) -> None:
    settings = get_settings()
    if not settings.gmail_push_audience or not settings.gmail_push_service_account:
        raise _error("CONFIGURATION_REQUIRED", "Authenticated Gmail push is not configured.", 503)
    if not authorization.startswith("Bearer ") or len(authorization) > 12000:
        raise _error("WEBHOOK_AUTH_INVALID", "Google push authentication is required.", 401)
    token = authorization[7:]
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise ValueError
        key = next(key for key in _keys() if key.get("kid") == header["kid"] and key.get("kty") == "RSA")
        claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=["RS256"], audience=settings.gmail_push_audience,
                            issuer=["https://accounts.google.com", "accounts.google.com"], options={"require": ["exp", "iat", "aud", "iss", "sub"]})
        if claims.get("email") != settings.gmail_push_service_account or claims.get("email_verified") is not True:
            raise ValueError
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, StopIteration, httpx.HTTPError, ResponseTooLarge):
        raise _error("WEBHOOK_AUTH_INVALID", "Google push authentication failed.", 401) from None


def receive_push(authorization: str, envelope: dict) -> dict:
    verify_push(authorization)
    message = envelope.get("message") or {}
    if not isinstance(message, dict):
        raise validation_failed("Invalid Pub/Sub message.")
    subscription_name = envelope.get("subscription")
    message_id = message.get("messageId")
    if not isinstance(subscription_name, str) or not SUBSCRIPTION.fullmatch(subscription_name) or not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", message_id):
        raise validation_failed("Invalid Pub/Sub envelope.")
    try:
        data = json.loads(_decode(message.get("data", ""), limit=4096))
        email = data["emailAddress"].lower()
        history = str(data["historyId"])
        if not history.isdigit() or not isinstance(email, str):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise validation_failed("Invalid Gmail notification.") from None
    with system_tx() as session:
        routes = session.execute(select(ProviderEventSubscription.tenant_id, ProviderEventSubscription.connection_id, ProviderEventSubscription.id)
                                 .where(ProviderEventSubscription.topic == subscription_name, ProviderEventSubscription.routing_key == email,
                                        ProviderEventSubscription.status == "active")).all()
    if not routes:
        return {"status": "ignored_unknown_or_disabled_subscription"}
    received = 0
    for tenant_id, connection_id, subscription_id in routes:
        with tenant_tx(tenant_id) as session:
            connection = session.get(Connection, connection_id)
            if connection is None or connection.status != "connected" or connection.disconnected_at or not _grants(session, connection):
                continue
            event_key = f"{subscription_id}:{message_id}"
            result = session.execute(insert(WebhookInbox).values(id=new_id("whk"), tenant_id=tenant_id, connection_id=connection_id,
                                     provider="gmail", event_id=event_key, event_type="mailbox_changed", payload_hash=fingerprint(data),
                                     payload={"history_id": history}, status="queued").on_conflict_do_nothing(constraint="uq_webhook_provider_event").returning(WebhookInbox.id)).first()
            if result:
                queue.enqueue(session, "gmail.sync", {"connection_id": connection_id}, tenant_id=tenant_id, dedupe_key=f"gmail.sync:{connection_id}")
                received += 1
    return {"status": "accepted", "new_events": received}


def maintain(*, transport: Any = None) -> dict:
    with system_tx() as session:
        subscriptions = session.execute(select(ProviderEventSubscription.tenant_id, ProviderEventSubscription.connection_id, ProviderEventSubscription.id)
                                        .where(ProviderEventSubscription.status == "active")).all()
    scheduled = 0
    for tenant_id, connection_id, subscription_id in subscriptions:
        with tenant_tx(tenant_id) as session:
            connection = session.get(Connection, connection_id)
            if connection is None or connection.provider != "gmail" or connection.status != "connected" or connection.disconnected_at:
                continue
            automations = _grants(session, connection)
            if not automations:
                continue
            row = session.get(ProviderEventSubscription, subscription_id)
            renew = row.topic != "gmail.poll" and (row.last_renewed_at is None or row.last_renewed_at <= utcnow() - timedelta(hours=20))
            watchers = _grants(session, connection, watch_only=True)
            labels = json.loads(row.cursor or "{}").get("labels", [])
            queue.enqueue(session, "gmail.sync", {"connection_id": connection_id}, tenant_id=tenant_id, dedupe_key=f"gmail.sync:{connection_id}")
            scheduled += 1
        if renew and watchers:
            result = _request(tenant_id, connection_id, "/watch", method="POST", permit=True,
                              body={"topicName": get_settings().gmail_pubsub_topic, "labelIds": labels, "labelFilterBehavior": "INCLUDE"}, transport=transport)
            _record_watch(tenant_id, subscription_id, result)
    return {"scheduled": scheduled}
