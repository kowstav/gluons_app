"""Passport authorization and data lifecycle on real restricted-role PostgreSQL."""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gluons import data_export, deletion, identity, passport
from gluons.api.app import create_app
from gluons.authz import Principal, Role
from gluons.core import GluonsError, utcnow
from gluons.db.models import Organization
from gluons.db.session import tenant_tx
from helpers import PASSWORD, add_member, make_user


@pytest.fixture
def owner(world):
    return replace(world.owner, session_id="owner-web")


def test_profile_is_versioned_confirmed_and_exported(world, owner):
    assert passport.get(owner)["fields"] == {}
    result = passport.update(owner, 1, {"brand_name": "Example Shop", "selling_regions": ["IN"], "currency": "INR"})
    assert result["version"] == 2
    assert result["fields"]["brand_name"]["value"] == "Example Shop"
    assert result["fields"]["brand_name"]["confirmed_by"] == owner.user_id
    assert result["fields"]["brand_name"]["source"] == "owner"
    assert result["fields"]["brand_name"]["schema_version"] == result["schema_version"] == 1
    assert result["fields"]["brand_name"]["sensitivity"] == "business_profile"
    with pytest.raises(GluonsError) as exc:
        passport.update(owner, 1, {"brand_name": "Stale"})
    assert exc.value.status == 409
    exported = data_export.export_page(owner, "organization")["records"][0]
    assert exported["business_passport"] == result["fields"] and exported["passport_version"] == 2
    removed = passport.update(owner, 2, {"brand_name": None})
    assert "brand_name" not in removed["fields"]


def test_passport_view_matches_published_contract_example(owner):
    path = Path(__file__).resolve().parents[3] / "docs/execution/contracts/passport-view.example.json"
    example = json.loads(path.read_text(encoding="utf-8-sig"))
    actual = passport.update(owner, 1, {"currency": "INR"})
    assert set(actual) == set(example)
    assert set(actual["organization"]) == set(example["organization"])
    assert set(actual["fields"]["currency"]) == set(example["fields"]["currency"])
    assert actual["notice"] == example["notice"]


def test_concurrent_edits_cannot_overwrite_one_another(owner):
    def edit(name):
        try:
            return passport.update(owner, 1, {"brand_name": name})["version"]
        except GluonsError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ("First", "Second")))
    assert sorted(results) == [2, 409]


def test_owner_isolation_and_authorization(world, owner):
    passport.update(owner, 1, {"brand_name": "Only this business"})
    other_org = identity.create_organization(world.user_id, "Another business")
    other_owner = replace(identity.principal_for_user(world.user_id, other_org), session_id="owner-web")
    assert passport.get(other_owner)["fields"] == {}
    user_id, _ = make_user()
    other = replace(add_member(world.tenant_id, user_id, "admin"), session_id="admin-web")
    assistant = Principal(kind="assistant", tenant_id=world.tenant_id, user_id=world.user_id, role=Role.OWNER,
                          scopes=frozenset({"gluons.read"}), session_id="not-web")
    for principal in (other, assistant, world.owner):
        with pytest.raises(GluonsError) as exc:
            passport.get(principal)
        assert exc.value.status == 403
    with tenant_tx(world.tenant_id, world.user_id) as session:
        rows = session.scalars(select(Organization)).all()
        assert all(row.id == world.tenant_id or row.business_passport == {} for row in rows)


def test_purge_removes_passport_data(world, owner):
    passport.update(owner, 1, {"brand_name": "Erase this profile"})
    with tenant_tx(world.tenant_id) as session:
        session.get(Organization, world.tenant_id).deleted_at = utcnow()
    deletion.purge(world.tenant_id)
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Organization, world.tenant_id).business_passport == {}


def test_authenticated_api_rejects_extra_fields_and_stale_versions(world):
    with TestClient(create_app()) as client:
        login = client.post("/v1/auth/login", json={"email": world.email, "password": PASSWORD})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers).status_code == 200
        assert client.get("/v1/business-passport").json()["version"] == 1
        assert client.patch("/v1/business-passport", headers=headers, json={"expected_version": 1,
                            "fields": {"brand_name": "Example"}}).status_code == 200
        assert client.patch("/v1/business-passport", headers=headers, json={"expected_version": 1,
                            "fields": {"brand_name": "Stale"}}).status_code == 409
        assert client.patch("/v1/business-passport", headers=headers, json={"expected_version": 2,
                            "fields": {"password": "not-allowed"}}).status_code == 422


@pytest.mark.parametrize("fields", [
    {"access_token": "secret"}, {"currency": "usd"}, {"selling_regions": ["IN", "IN"]},
    {"support_email": "not-an-email"}, {"brand_name": ""},
    {"support_hours": "password=example"}, {"operational_address": "4111111111111111"},
])
def test_unknown_or_invalid_values_are_rejected(owner, fields):
    with pytest.raises(GluonsError) as exc:
        passport.update(owner, 1, fields)
    assert exc.value.status == 422
