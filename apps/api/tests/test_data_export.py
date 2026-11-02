"""Owner downloads against real tenant RLS; credential tables are never serialized."""
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gluons import data_export as exports
from gluons import identity, vault
from gluons.api.app import create_app
from gluons.authz import ASSISTANT_SCOPES, Principal, Role
from gluons.core import GluonsError, canonical_json, new_id, utcnow
from gluons.db.models import ActionReceipt, AuditEvent, Automation, Connection, KnowledgeDocument
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle
from helpers import PASSWORD, World, add_member, make_user


@pytest.fixture
def browser(world):
    return replace(world.owner, session_id="verified-owner-browser")


def test_manifest_has_only_explicit_safe_columns(browser):
    manifest = exports.manifest(browser)
    names = {row["name"] for row in manifest["datasets"]}
    assert {"automations", "orders", "usage", "action_receipts", "audit"} <= names
    assert not {"vault_secrets", "oauth_tokens", "identity_sessions", "connection_sessions"}.intersection(names)
    denied = {"password_hash", "credential_ref", "metadata_json", "token_hash", "csrf_token_hash", "signature", "storage_uri", "input", "output", "config"}
    assert all(not denied.intersection(row["columns"]) for row in manifest["datasets"])


@pytest.mark.parametrize("role", ["admin", "approver", "operator", "viewer", "field_rep", "support"])
def test_only_owner_browser_can_export(world, role):
    user_id, _ = make_user()
    principal = replace(add_member(world.tenant_id, user_id, role), session_id="member-browser")
    with pytest.raises(GluonsError) as error:
        exports.export_page(principal, "organization")
    assert error.value.status == 403


def test_assistant_and_sessionless_owner_cannot_export(world):
    assistant = Principal(kind="assistant", tenant_id=world.tenant_id, user_id=world.user_id, role=Role.OWNER,
                          session_id="not-a-browser", scopes=frozenset(ASSISTANT_SCOPES))
    for principal in (assistant, world.owner):
        with pytest.raises(GluonsError) as error:
            exports.export_page(principal, "organization")
        assert error.value.status == 403


def test_all_datasets_are_exportable_and_preserve_cost_and_receipt_labels(world, browser):
    automation = world.order_automation()
    with tenant_tx(world.tenant_id) as session:
        session.add(ActionReceipt(id=new_id("rcp"), tenant_id=world.tenant_id, automation_id=automation["id"], action="test.export", mode="supervised",
                                  environment="simulator", idempotency_key=new_id("key"), payload_hash="a" * 64,
                                  status="succeeded", verified=True, verified_at=utcnow()))
    for dataset in exports.DATASETS:
        result = exports.export_page(browser, dataset)
        assert result["tenant_id"] == world.tenant_id
        assert result["dataset"] == dataset
        assert result["record_count"] == len(result["records"])
    receipt = exports.export_page(browser, "action_receipts")["records"][0]
    assert receipt["environment"] == "simulator" and receipt["verified"] is True
    assert receipt["verified_at"] is not None


def test_same_owner_multiple_businesses_are_never_mixed(world, browser):
    other_id = identity.create_organization(world.user_id, "Other owner business")
    own = exports.export_page(browser, "organization")
    assert [row["id"] for row in own["records"]] == [world.tenant_id]
    memberships = exports.export_page(browser, "memberships")
    assert len(memberships["records"]) == 1
    other = replace(identity.principal_for_user(world.user_id, other_id), session_id=browser.session_id)
    assert [row["id"] for row in exports.export_page(other, "organization")["records"]] == [other_id]


def test_vault_provider_metadata_and_runtime_sentinels_never_appear(world, browser):
    sentinel = "SECRET-MUST-NEVER-ENTER-EXPORT-0fa5f632"
    automation = lifecycle.create(world.owner, skill_id="multichannel_order_entry", config={"api_key": sentinel})
    with tenant_tx(world.tenant_id) as session:
        credential = vault.store(session, world.tenant_id, "gmail", {"access_token": sentinel, "refresh_token": sentinel})
        session.add(Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="gmail", external_account_id=new_id("account"), account_display="Safe account",
                               credential_ref=credential, metadata_json={"api_key": sentinel, "arbitrary": sentinel}, created_by=world.user_id))
        session.add(KnowledgeDocument(id=new_id("knw"), tenant_id=world.tenant_id, title="Business note", body=sentinel,
                                     source=f"https://example.test?token={sentinel}", created_by=world.user_id))
    for dataset in exports.DATASETS:
        output = canonical_json(exports.export_page(browser, dataset))
        assert sentinel not in output and credential not in output
    with tenant_tx(world.tenant_id) as session:
        events = session.execute(select(AuditEvent).where(AuditEvent.event_type == "data.export_page")).scalars().all()
        assert events
        assert all(set(row.data) == {"dataset", "record_count", "has_next_page"} for row in events)
        assert sentinel not in canonical_json([row.data for row in events])
        assert session.get(Automation, automation["id"]).config["api_key"] == sentinel, "export must never mutate source records"


def test_signed_keyset_cursor_is_stable_with_equal_timestamps(world, browser):
    rows = [world.order_automation() for _ in range(5)]
    fixed = utcnow() - timedelta(seconds=10)
    with tenant_tx(world.tenant_id) as session:
        for row in rows:
            session.get(Automation, row["id"]).created_at = fixed
    page = exports.export_page(browser, "automations", limit=2)
    received = [row["id"] for row in page["records"]]
    cutoff = page["as_of"]
    newer = world.order_automation()
    while page["next_cursor"]:
        page = exports.export_page(browser, "automations", limit=2, cursor=page["next_cursor"])
        assert page["as_of"] == cutoff
        received.extend(row["id"] for row in page["records"])
    assert received == sorted(row["id"] for row in rows)
    assert len(set(received)) == 5 and newer["id"] not in received


def test_cursor_rejects_tampering_expiry_wrong_dataset_tenant_and_owner(world, browser):
    for _ in range(3):
        world.order_automation()
    page = exports.export_page(browser, "automations", limit=1)
    cursor = page["next_cursor"]
    assert cursor
    second_owner_id, _ = make_user()
    second_owner = replace(add_member(world.tenant_id, second_owner_id, "owner"), session_id="second-browser")
    other = replace(World.create().owner, session_id="other-browser")
    for principal, dataset, value in [(browser, "automations", "x" + cursor), (browser, "orders", cursor),
                                      (second_owner, "automations", cursor), (other, "automations", cursor)]:
        with pytest.raises(GluonsError) as error:
            exports.export_page(principal, dataset, cursor=value)
        assert error.value.code == "EXPORT_CURSOR_INVALID"
    claims = exports._open_cursor(cursor, browser, "automations")
    expired = exports._seal_cursor({**claims, "expires_at": (utcnow() - timedelta(seconds=1)).isoformat()})
    with pytest.raises(GluonsError, match="expired"):
        exports.export_page(browser, "automations", cursor=expired)


@pytest.mark.parametrize("size", [0, 1001, -1, True])
def test_page_size_is_enforced(browser, size):
    with pytest.raises(GluonsError):
        exports.export_page(browser, "organization", limit=size)


def test_unknown_tables_and_individually_oversized_records_fail_explicitly(world, browser, monkeypatch):
    with pytest.raises(GluonsError):
        exports.export_page(browser, "vault_secrets")
    monkeypatch.setattr(exports, "MAX_RECORD_BYTES", 10)
    with pytest.raises(GluonsError) as error:
        exports.export_page(browser, "organization")
    assert error.value.code == "EXPORT_RECORD_TOO_LARGE"


def test_http_export_requires_csrf_and_sets_no_store_and_download_headers(world):
    with TestClient(create_app()) as client:
        login = client.post("/v1/auth/login", json={"email": world.email, "password": PASSWORD})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers).status_code == 200
        manifest = client.get("/v1/data-export")
        assert manifest.status_code == 200 and manifest.headers["cache-control"] == "no-store"
        assert client.post("/v1/data-export/organization", json={}).status_code == 403
        response = client.post("/v1/data-export/organization", headers=headers, json={"limit": 10})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-disposition"] == 'attachment; filename="gluons-organization.json"'
        assert response.json()["records"][0]["id"] == world.tenant_id
        assert client.post("/v1/data-export/organization", headers=headers, json={"tenant_id": "untrusted"}).status_code == 422
