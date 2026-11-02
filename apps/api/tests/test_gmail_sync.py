"""Provider-double Gmail/PubSub contracts using real tenant PostgreSQL storage.

No Google consent, real mailbox, Cloud Pub/Sub delivery or external send is claimed.
"""
from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select

from gluons import vault
from gluons.authz import Principal, Role
from gluons.config import get_settings
from gluons.connections import gmail_sync as gmail
from gluons.connections.google import READ_SCOPE
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import (
    ActionReceipt,
    Artifact,
    Automation,
    ChannelMessage,
    Connection,
    Job,
    ProviderEventSubscription,
    Run,
    WebhookInbox,
)
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle
from helpers import World


def encoded(value: str | bytes) -> str:
    return base64.urlsafe_b64encode(value.encode() if isinstance(value, str) else value).decode().rstrip("=")


class GmailDouble:
    def __init__(self):
        self.requests = []
        self.messages = [{"id": "message-1"}]
        self.next_page = None
        self.history_status = 200
        self.history = {"historyId": "150", "history": []}
        self.document = {"id": "message-1", "threadId": "thread-1", "labelIds": ["INBOX"], "payload": {
            "mimeType": "multipart/mixed", "headers": [{"name": "From", "value": "Harbor Bistro <orders@harbor-bistro.com>"}, {"name": "Subject", "value": "Order"}],
            "parts": [{"mimeType": "text/plain", "body": {"data": encoded("2 cases FISH-1"), "size": 15}}]}}
        self.attachment = encoded("1 bag RICE-2")

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "100", "emailAddress": "mailbox@example.com"})
        if path.endswith("/watch"):
            return httpx.Response(200, json={"historyId": "101", "expiration": str(int((utcnow() + timedelta(days=7)).timestamp() * 1000))})
        if path.endswith("/history"):
            return httpx.Response(self.history_status, json=self.history)
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": self.messages, **({"nextPageToken": self.next_page} if self.next_page else {})})
        if "/attachments/" in path:
            return httpx.Response(200, json={"data": self.attachment, "size": 12})
        return httpx.Response(200, json=self.document)


@pytest.fixture
def intake(world):
    owner = replace(world.owner, session_id="real-owner-browser")
    with tenant_tx(world.tenant_id) as session:
        credential = vault.store(session, world.tenant_id, "gmail", {"access_token": "provider-double"})
        connection = Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="gmail", environment="sandbox", external_account_id=new_id("google"),
                                account_display="mailbox@example.com", metadata_json={"account_email": "mailbox@example.com"}, account_confirmed_at=utcnow(),
                                granted_scopes=[READ_SCOPE], credential_ref=credential, created_by=world.user_id)
        session.add(connection)
    automation = lifecycle.create(owner, skill_id="multichannel_order_entry", config={"connections": {"email": connection.id}})
    lifecycle.start_trial(owner, automation["id"], "shadow", automation["version"])
    provider = GmailDouble()
    return world, owner, connection.id, automation["id"], provider, httpx.MockTransport(provider)


def setup_intake(intake, **kwargs):
    world, owner, connection_id, automation_id, provider, transport = intake
    result = gmail.configure(owner, connection_id, automation_id, labels=["INBOX"], transport=transport, **kwargs)
    return result


def test_connection_alone_cannot_ingest_and_an_assistant_cannot_grant_reads(intake):
    world, owner, connection_id, automation_id, provider, transport = intake
    assert gmail.ingest_message(world.tenant_id, connection_id, "message-1", transport=transport)["status"] == "no_active_owner_grant"
    assert provider.requests == []
    assistant = Principal(kind="assistant", tenant_id=world.tenant_id, user_id=world.user_id, role=Role.OWNER,
                          scopes=frozenset({"gluons.connections", "gluons.read"}))
    with pytest.raises(GluonsError, match="owner"):
        gmail.configure(assistant, connection_id, automation_id, labels=["INBOX"])


def test_full_sync_checkpoints_pages_only_after_durable_message_jobs(intake):
    world, owner, connection_id, automation_id, provider, transport = intake
    configured = setup_intake(intake)
    provider.next_page = "page-two"
    first = gmail.sync_connection(world.tenant_id, connection_id, transport=transport)
    assert first["status"] == "page_queued"
    with tenant_tx(world.tenant_id) as session:
        row = session.get(ProviderEventSubscription, configured["subscription_id"])
        checkpoint = json.loads(row.cursor)
        assert checkpoint["resync_history"] == "100" and checkpoint["page"] == "page-two"
        assert session.execute(select(func.count()).select_from(Job).where(Job.kind == "gmail.ingest")).scalar_one() == 1
    provider.next_page, provider.messages = None, [{"id": "message-2"}]
    second = gmail.sync_connection(world.tenant_id, connection_id, transport=transport)
    assert second["status"] == "synchronized"
    with tenant_tx(world.tenant_id) as session:
        checkpoint = json.loads(session.get(ProviderEventSubscription, configured["subscription_id"]).cursor)
        assert checkpoint == {"history": "100", "labels": ["INBOX"]}
        assert session.execute(select(func.count()).select_from(Job).where(Job.kind == "gmail.ingest")).scalar_one() == 2
    assert len([r for r in provider.requests if r.url.path.endswith("/profile")]) == 1
    assert all(r.method == "GET" for r in provider.requests), "shadow intake never creates a provider watch"


def test_stale_history_schedules_full_resync_without_advancing_unknown_cursor(intake):
    world, owner, connection_id, automation_id, provider, transport = intake
    configured = setup_intake(intake)
    gmail.sync_connection(world.tenant_id, connection_id, transport=transport)
    provider.history_status = 404
    assert gmail.sync_connection(world.tenant_id, connection_id, transport=transport)["status"] == "full_resync_required"
    with tenant_tx(world.tenant_id) as session:
        checkpoint = json.loads(session.get(ProviderEventSubscription, configured["subscription_id"]).cursor)
        assert checkpoint == {"labels": ["INBOX"], "resync": True}
        assert session.execute(select(func.count()).select_from(Job).where(Job.dedupe_key.like(f"gmail.resync:{connection_id}:%"))).scalar_one() == 1


def test_duplicate_message_creates_one_channel_record_and_one_shared_order_run(intake):
    world, owner, connection_id, automation_id, provider, transport = intake
    setup_intake(intake)
    first = gmail.ingest_message(world.tenant_id, connection_id, "message-1", transport=transport)
    second = gmail.ingest_message(world.tenant_id, connection_id, "message-1", transport=transport)
    assert first["runs"][0]["run_id"] == second["runs"][0]["run_id"]
    assert second["runs"][0]["duplicate"] is True
    assert len(provider.requests) == 1
    with tenant_tx(world.tenant_id) as session:
        assert session.execute(select(func.count()).select_from(ChannelMessage)).scalar_one() == 1
        assert session.execute(select(func.count()).select_from(Run)).scalar_one() == 1
        assert session.execute(select(func.count()).select_from(ActionReceipt)).scalar_one() == 0
        run = session.execute(select(Run)).scalar_one()
        assert run.input["thread_id"] == "thread-1" and run.mode == "shadow"


def test_attachments_are_encrypted_and_extracted_as_untrusted_input(intake):
    world, owner, connection_id, automation_id, provider, transport = intake
    setup_intake(intake)
    provider.document["payload"]["parts"].append({"filename": "order.txt", "mimeType": "text/plain", "body": {"attachmentId": "attachment-1", "size": 12}})
    result = gmail.ingest_message(world.tenant_id, connection_id, "message-1", transport=transport)
    assert len(result["runs"]) == 1
    with tenant_tx(world.tenant_id) as session:
        artifact = session.execute(select(Artifact)).scalar_one()
        assert artifact.storage_uri.startswith("vault://")
        content = vault.read(session, world.tenant_id, artifact.storage_uri.removeprefix("vault://"))
        assert base64.b64decode(content["data_b64"]) == b"1 bag RICE-2"
        run = session.execute(select(Run)).scalar_one()
        assert "RICE-2" in run.input["text"] and run.input["attachment_ids"] == [artifact.id]


@pytest.mark.parametrize("filename,size", [("unknown.bin", 12), ("huge.txt", gmail.MAX_ATTACHMENT_BYTES + 1)])
def test_unsupported_or_large_attachment_prevents_partial_order_execution(intake, filename, size):
    world, owner, connection_id, automation_id, provider, transport = intake
    setup_intake(intake)
    provider.document["payload"]["parts"].append({"filename": filename, "mimeType": "application/octet-stream", "body": {"attachmentId": "attachment-1", "size": size}})
    result = gmail.ingest_message(world.tenant_id, connection_id, "message-1", transport=transport)
    assert result["status"] == "needs_review" and result["runs"] == []
    assert gmail.ingest_message(world.tenant_id, connection_id, "message-1", transport=transport)["runs"] == []
    with tenant_tx(world.tenant_id) as session:
        assert session.execute(select(func.count()).select_from(Run)).scalar_one() == 0
        assert session.execute(select(ChannelMessage)).scalar_one().status == "needs_review"


def test_grant_tampering_pause_disconnect_and_wrong_tenant_block_reads(intake):
    world, owner, connection_id, automation_id, provider, transport = intake
    setup_intake(intake)
    with tenant_tx(world.tenant_id) as session:
        automation = session.get(Automation, automation_id)
        record = automation.config["gmail_ingestion"]
        record["grant"]["labels"] = ["ALL_MAIL"]
        automation.config = {"gmail_ingestion": record}
    assert gmail.sync_connection(world.tenant_id, connection_id, transport=transport)["status"] == "no_active_owner_grant"
    assert provider.requests == []
    other = World.create()
    with pytest.raises(GluonsError) as error:
        gmail.configure(replace(other.owner, session_id="other-browser"), connection_id, automation_id, labels=["INBOX"])
    assert error.value.status == 404
    setup_intake(intake)
    with tenant_tx(world.tenant_id) as session:
        session.get(Automation, automation_id).status = "paused"
    assert gmail.sync_connection(world.tenant_id, connection_id, transport=transport)["status"] == "no_active_owner_grant"
    with tenant_tx(world.tenant_id) as session:
        session.get(Connection, connection_id).status = "disconnected"
    with pytest.raises(GluonsError):
        gmail.ingest_message(world.tenant_id, connection_id, "message-1", transport=transport)


def test_provider_watch_is_separate_owner_action_and_refused_in_shadow(intake, monkeypatch):
    world, owner, connection_id, automation_id, provider, transport = intake
    settings = get_settings()
    monkeypatch.setattr(settings, "gmail_pubsub_topic", "projects/test-project/topics/gmail")
    monkeypatch.setattr(settings, "gmail_push_audience", "https://api.example/v1/webhooks/gmail")
    monkeypatch.setattr(settings, "gmail_push_service_account", "pubsub@test-project.iam.gserviceaccount.com")
    with pytest.raises(GluonsError, match="Shadow"):
        setup_intake(intake, watch=True, pubsub_subscription="projects/test-project/subscriptions/gmail-push")
    assert provider.requests == []
    with tenant_tx(world.tenant_id) as session:
        row = session.get(Automation, automation_id)
        row.mode, row.status = "supervised", "active"
    configured = setup_intake(intake, watch=True, pubsub_subscription="projects/test-project/subscriptions/gmail-push")
    assert configured["mode"] == "push"
    assert len(provider.requests) == 1 and provider.requests[0].method == "POST"
    with tenant_tx(world.tenant_id) as session:
        row = session.get(ProviderEventSubscription, configured["subscription_id"])
        assert row.expires_at > utcnow() and row.last_renewed_at


@pytest.fixture
def signed_push(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "gmail_push_audience", "https://api.example/v1/webhooks/gmail")
    monkeypatch.setattr(settings, "gmail_push_service_account", "pubsub@test-project.iam.gserviceaccount.com")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public["kid"] = "test-key"
    monkeypatch.setattr(gmail, "_keys", lambda: [public])
    def issue(**changes):
        now = int(utcnow().timestamp())
        claims = {"iss": "https://accounts.google.com", "aud": settings.gmail_push_audience, "sub": "service-subject",
                  "iat": now - 10, "exp": now + 3500, "email": settings.gmail_push_service_account, "email_verified": True, **changes}
        return "Bearer " + jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    return issue


@pytest.mark.parametrize("changes", [{"aud": "https://attacker.example"}, {"email": "attacker@example.com"}, {"email_verified": False}, {"iss": "https://attacker.example"}, {"exp": 1}])
def test_pubsub_rejects_wrong_audience_issuer_account_and_expiry(signed_push, changes):
    with pytest.raises(GluonsError, match="authentication failed"):
        gmail.verify_push(signed_push(**changes))


def test_verified_pubsub_routes_by_subscription_and_account_and_deduplicates(intake, signed_push):
    world, owner, connection_id, automation_id, provider, transport = intake
    configured = setup_intake(intake)
    subscription = f"projects/test-project/subscriptions/{new_id('gmailpush')}"
    with tenant_tx(world.tenant_id) as session:
        session.get(ProviderEventSubscription, configured["subscription_id"]).topic = subscription
    envelope = {"subscription": subscription, "message": {"messageId": "pubsub-1", "data": encoded(json.dumps({"emailAddress": "mailbox@example.com", "historyId": "999999"}))},
                "tenant_id": "not-authority"}
    assert gmail.receive_push(signed_push(), envelope)["new_events"] == 1
    assert gmail.receive_push(signed_push(), envelope)["new_events"] == 0
    with tenant_tx(world.tenant_id) as session:
        inbox = session.execute(select(WebhookInbox).where(WebhookInbox.provider == "gmail")).scalar_one()
        assert inbox.tenant_id == world.tenant_id and inbox.connection_id == connection_id
        # A forged notification history cannot move the provider cursor.
        assert "999999" not in session.get(ProviderEventSubscription, configured["subscription_id"]).cursor
    envelope["subscription"] = "projects/test-project/subscriptions/unconfigured"
    assert gmail.receive_push(signed_push(), envelope)["status"] == "ignored_unknown_or_disabled_subscription"


def test_disable_invalidates_read_grant_and_maintenance_is_readonly_for_shadow(intake):
    world, owner, connection_id, automation_id, provider, transport = intake
    setup_intake(intake)
    status = gmail.intake_status(owner, connection_id)
    assert status["active_automation_ids"] == [automation_id]
    assert status["subscriptions"][0]["mode"] == "read_only_polling"
    assert "access_token" not in str(status)
    assert gmail.maintain(transport=transport)["scheduled"] >= 1
    assert provider.requests == []
    gmail.disable(owner, connection_id, automation_id)
    assert gmail.sync_connection(world.tenant_id, connection_id, transport=transport)["status"] == "no_active_owner_grant"


def test_expired_or_cancelled_intake_grant_never_reads_mailbox(intake):
    world, owner, connection_id, automation_id, provider, transport = intake
    setup_intake(intake)
    with tenant_tx(world.tenant_id) as session:
        automation = session.get(Automation, automation_id)
        record = dict(automation.config["gmail_ingestion"])
        record["grant"] = {**record["grant"], "expires_at": (utcnow() - timedelta(seconds=1)).isoformat()}
        record["signature"] = gmail._sign(record["grant"])
        automation.config = {**automation.config, "gmail_ingestion": record}
    assert gmail.sync_connection(world.tenant_id, connection_id, transport=transport)["status"] == "no_active_owner_grant"
    setup_intake(intake)
    with tenant_tx(world.tenant_id) as session:
        session.get(Automation, automation_id).status = "cancel_requested"
    assert gmail.ingest_message(world.tenant_id, connection_id, "message-1", transport=transport)["status"] == "no_active_owner_grant"
    assert provider.requests == []


def test_provider_response_budget_is_enforced_before_buffering_message(intake, monkeypatch):
    world, owner, connection_id, automation_id, provider, transport = intake
    setup_intake(intake)
    monkeypatch.setattr(gmail, "MAX_MESSAGE_BYTES", 100)
    with pytest.raises(GluonsError) as error:
        gmail.ingest_message(world.tenant_id, connection_id, "message-1", transport=transport)
    assert error.value.code == "GMAIL_MESSAGE_LIMIT"
    with tenant_tx(world.tenant_id) as session:
        assert session.execute(select(func.count()).select_from(ChannelMessage)).scalar_one() == 0
        assert session.execute(select(func.count()).select_from(Run)).scalar_one() == 0
