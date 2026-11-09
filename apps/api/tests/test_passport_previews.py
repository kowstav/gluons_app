"""Profile import and sharing previews use provider doubles and never transmit data."""
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from gluons import data_export, deletion, identity, passport, passport_previews, vault
from gluons.api.app import create_app
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import Connection, Organization
from gluons.db.session import tenant_tx
from helpers import PASSWORD


@pytest.fixture
def owner(world):
    return replace(world.owner, session_id="owner-browser")


@pytest.fixture
def shop(world):
    with tenant_tx(world.tenant_id, world.user_id) as session:
        ref = vault.store(session, world.tenant_id, "shopify", {"access_token": "synthetic-only"})
        row = Connection(id=new_id("con"), tenant_id=world.tenant_id, provider="shopify", environment="sandbox",
                         external_account_id="test-shop", account_display="Synthetic shop", created_by=world.user_id,
                         credential_ref=ref, status="connected", account_confirmed_at=utcnow(), last_checked_at=utcnow(),
                         metadata_json={"currency": "INR", "unrelated": "MUST_NOT_IMPORT"})
        session.add(row)
        return row.id


def test_import_preview_is_allowlisted_and_needs_confirmation(world, owner, shop):
    preview = passport_previews.preview_import(owner, shop)
    assert preview["fields"] == {"currency": "INR"} and "MUST_NOT_IMPORT" not in str(preview)
    assert passport.get(owner)["fields"] == {}
    result = passport_previews.confirm_import(owner, preview["preview_token"])
    assert result["fields"]["currency"]["source"] == "shopify"
    assert result["fields"]["currency"]["source_ref"] == shop
    assert result["fields"]["currency"]["confirmed_by"] == owner.user_id
    with pytest.raises(GluonsError):
        passport_previews.confirm_import(owner, preview["preview_token"])


@pytest.mark.parametrize("change", ["disconnect", "currency", "stale", "profile", "session"])
def test_changed_connection_or_profile_invalidates_import(world, owner, shop, change):
    token = passport_previews.preview_import(owner, shop)["preview_token"]
    if change in {"disconnect", "currency", "stale"}:
        with tenant_tx(world.tenant_id) as session:
            row = session.get(Connection, shop)
            if change == "disconnect":
                row.status = "disconnected"
            elif change == "currency":
                row.metadata_json = {"currency": "USD"}
            else:
                row.last_checked_at = utcnow() - timedelta(days=2)
    elif change == "profile":
        passport.update(owner, 1, {"brand_name": "New"})
    else:
        owner = replace(owner, session_id="different-session")
    with pytest.raises(GluonsError):
        passport_previews.confirm_import(owner, token)
    assert "currency" not in passport.get(owner)["fields"]


def test_share_consent_binds_version_recipient_and_fields_without_sending(world, owner):
    passport.update(owner, 1, {"brand_name": "Example", "currency": "INR"})
    preview = passport_previews.preview_share(owner, "shopify", ["currency"], "account_setup")
    assert preview["fields"] == {"currency": "INR"}
    assert "no data is sent" in preview["notice"]
    consent = passport_previews.confirm_share(owner, preview["preview_token"])
    assert consent["fields"] == ["currency"] and consent["provider"] == "shopify"
    assert passport_previews.confirm_share(owner, preview["preview_token"])["id"] == consent["id"]
    assert passport_previews.list_consents(owner)[0]["active"] is True
    exported = data_export.export_page(owner, "organization")
    assert "preview_hash" not in str(exported) and preview["preview_token"] not in str(exported)
    passport.update(owner, 2, {"brand_name": "Changed"})
    assert passport_previews.list_consents(owner)[0]["active"] is False
    with pytest.raises(GluonsError):
        passport_previews.confirm_share(owner, preview["preview_token"])
    assert passport_previews.revoke_consent(owner, consent["id"])["revoked"] is True


def test_preview_rejects_tamper_cross_org_and_unconfirmed_fields(world, owner):
    passport.update(owner, 1, {"brand_name": "Example"})
    with pytest.raises(GluonsError):
        passport_previews.preview_share(owner, "shopify", ["currency"], "account_setup")
    preview = passport_previews.preview_share(owner, "gmail", ["brand_name"], "account_setup")
    token = preview["preview_token"]
    with pytest.raises(GluonsError):
        passport_previews.confirm_share(owner, token + "x")
    other_org = identity.create_organization(world.user_id, "Another business")
    other = replace(identity.principal_for_user(world.user_id, other_org), session_id=owner.session_id)
    with pytest.raises(GluonsError):
        passport_previews.confirm_share(other, token)
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Organization, world.tenant_id).passport_consents == []


def test_expired_preview_cannot_create_consent(owner, monkeypatch):
    passport.update(owner, 1, {"currency": "INR"})
    token = passport_previews.preview_share(owner, "shopify", ["currency"], "account_setup")["preview_token"]
    future = utcnow() + timedelta(minutes=16)
    monkeypatch.setattr(passport_previews, "utcnow", lambda: future)
    with pytest.raises(GluonsError):
        passport_previews.confirm_share(owner, token)
    assert passport_previews.list_consents(owner) == []


def test_changed_organization_field_invalidates_preview_and_existing_consent(world, owner):
    with tenant_tx(world.tenant_id) as session:
        org = session.get(Organization, world.tenant_id)
        org.country = "IN"
        organization_version = org.version
    passport.confirm_organization_fields(owner, ["country"], 1, organization_version)
    token = passport_previews.preview_share(owner, "shopify", ["country"], "account_setup")["preview_token"]
    passport_previews.confirm_share(owner, token)
    assert passport_previews.list_consents(owner)[0]["active"] is True
    with tenant_tx(world.tenant_id) as session:
        org = session.get(Organization, world.tenant_id)
        org.country = "US"
        org.version += 1
    assert passport_previews.list_consents(owner)[0]["active"] is False
    with pytest.raises(GluonsError):
        passport_previews.confirm_share(owner, token)
    with pytest.raises(GluonsError):
        passport_previews.preview_share(owner, "shopify", ["country"], "account_setup")


def test_authenticated_http_preview_confirm_and_revoke(world, owner):
    passport.update(owner, 1, {"currency": "INR"})
    with TestClient(create_app()) as client:
        login = client.post("/v1/auth/login", json={"email": world.email, "password": PASSWORD})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers).status_code == 200
        preview = client.post("/v1/business-passport/sharing/preview", headers=headers,
                              json={"provider": "shopify", "purpose": "account_setup", "fields": ["currency"]})
        assert preview.status_code == 200 and preview.headers["cache-control"] == "no-store"
        token = preview.json()["preview_token"]
        consent = client.post("/v1/business-passport/sharing/confirm", headers=headers, json={"preview_token": token})
        assert consent.status_code == 200
        assert client.get("/v1/business-passport/sharing/consents").json()["consents"][0]["active"] is True
        assert client.post(f"/v1/business-passport/sharing/consents/{consent.json()['id']}/revoke", headers=headers).status_code == 200


def test_purge_erases_sharing_consent(world, owner):
    passport.update(owner, 1, {"currency": "INR"})
    token = passport_previews.preview_share(owner, "shopify", ["currency"], "account_setup")["preview_token"]
    passport_previews.confirm_share(owner, token)
    with tenant_tx(world.tenant_id) as session:
        session.get(Organization, world.tenant_id).deleted_at = utcnow()
    deletion.purge(world.tenant_id)
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Organization, world.tenant_id).passport_consents == []
