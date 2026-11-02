"""Identity, tenancy and API contract, through real HTTP handling and real Postgres."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers import PASSWORD, make_user, order_event
from sqlalchemy import inspect, text

from gluons import audit, identity, policy
from gluons.api.app import create_app
from gluons.core import GluonsError, new_id
from gluons.db.models import TENANT_TABLES, Automation, Base, Connection, Organization
from gluons.db.session import app_engine, tenant_tx
from gluons.runtime import lifecycle


@pytest.fixture
def client(database):
    return TestClient(create_app())


def sign_in(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def signed_in_org(client: TestClient, world) -> dict[str, str]:
    headers = sign_in(client, world.email)
    assert client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers).status_code == 200
    return headers


def test_every_tenant_table_has_rls_forced(database):
    expected = set(TENANT_TABLES) | {"organizations", "jobs", "outbox", "webhook_inbox"}
    with app_engine().connect() as conn:
        rows = conn.execute(
            text("select relname, relrowsecurity, relforcerowsecurity from pg_class where relkind = 'r' and relnamespace = 'public'::regnamespace")
        ).all()
    secured = {r.relname for r in rows if r.relrowsecurity and r.relforcerowsecurity}
    assert expected <= secured, expected - secured
    # Anything carrying a tenant_id column must be covered by the list above.
    for name, table in Base.metadata.tables.items():
        if "tenant_id" in table.columns and name not in ("oauth_authorization_requests",):
            assert name in expected, f"{name} has tenant_id but no row level security"


def test_app_role_cannot_bypass_rls(database):
    with app_engine().connect() as conn:
        row = conn.execute(text("select rolsuper, rolbypassrls from pg_roles where rolname = current_user")).one()
    assert row.rolsuper is False and row.rolbypassrls is False


def test_cross_tenant_rows_are_invisible_without_any_filter(database):
    from helpers import World

    a, b = World.create("A"), World.create("B")
    automation = a.order_automation()
    with tenant_tx(b.tenant_id, b.user_id) as session:
        assert session.get(Automation, automation["id"]) is None
        assert session.execute(text("select count(*) from automations")).scalar_one() == 0
        assert session.get(Organization, a.tenant_id) is None


def test_cross_tenant_insert_is_rejected_by_the_database(database):
    from helpers import World

    a, b = World.create("A"), World.create("B")
    with pytest.raises(Exception, match="row-level security"):  # noqa: B017
        with tenant_tx(a.tenant_id, a.user_id) as session:
            session.add(
                Connection(
                    id=new_id("con"), tenant_id=b.tenant_id, provider="gmail", external_account_id="x",
                    account_display="x", created_by=a.user_id,
                )
            )


def test_other_tenant_api_ids_return_not_found(client, world):
    from helpers import World

    other = World.create("Other")
    automation = other.order_automation()
    headers = signed_in_org(client, world)
    response = client.get(f"/v1/automations/{automation['id']}", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_write_without_csrf_header_is_rejected(client, world):
    signed_in_org(client, world)
    response = client.post("/v1/plans", json={"skill_id": "multichannel_order_entry"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


def test_unverified_email_cannot_sign_in(client, database):
    address = f"{new_id('u')}@example.com"
    identity.register(address, PASSWORD, "New")
    response = client.post("/v1/auth/login", json={"email": address, "password": PASSWORD})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


def test_registration_does_not_reveal_existing_accounts(client, database):
    _, email = make_user()
    existing = client.post("/v1/auth/register", json={"email": email, "password": PASSWORD, "display_name": "x"})
    fresh = client.post(
        "/v1/auth/register", json={"email": f"{new_id('u')}@example.com", "password": PASSWORD, "display_name": "x"}
    )
    assert existing.status_code == fresh.status_code == 202
    assert existing.json() == fresh.json()


def test_repeated_bad_passwords_lock_the_account(client, database):
    _, email = make_user()
    for _ in range(identity.MAX_FAILED_LOGINS):
        assert client.post("/v1/auth/login", json={"email": email, "password": "wrong-password-1"}).status_code == 401
    locked = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert locked.status_code == 429


def test_viewer_cannot_change_anything(client, world):
    from helpers import add_member

    user_id, email = make_user()
    add_member(world.tenant_id, user_id, "viewer")
    headers = sign_in(client, email)
    client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers)
    response = client.post("/v1/plans", json={"skill_id": "multichannel_order_entry"}, headers=headers)
    assert response.status_code == 403


def test_removed_member_loses_access_immediately(client, world):
    from helpers import add_member

    user_id, email = make_user()
    add_member(world.tenant_id, user_id, "operator")
    headers = sign_in(client, email)
    client.post(f"/v1/organizations/{world.tenant_id}/select", headers=headers)
    assert client.get("/v1/automations", headers=headers).status_code == 200
    member = next(m for m in identity.list_members(world.owner) if m["user_id"] == user_id)
    identity.remove_member(world.owner, member["id"])
    assert client.get("/v1/automations", headers=headers).status_code == 403


def test_last_owner_cannot_be_removed(world):
    owner_row = identity.list_members(world.owner)[0]
    with pytest.raises(GluonsError, match="at least one owner"):
        identity.remove_member(world.owner, owner_row["id"])


def test_list_members_does_not_leak_other_org_memberships(database):
    from helpers import World, add_member

    a, b = World.create("A"), World.create("B")
    add_member(b.tenant_id, a.user_id, "viewer")
    assert {m["user_id"] for m in identity.list_members(a.owner)} == {a.user_id}


def test_audit_chain_detects_nothing_when_untouched_and_blocks_rewrites(world):
    world.order_automation()
    with tenant_tx(world.tenant_id) as session:
        assert audit.verify_chain(session, world.tenant_id)["valid"] is True
    with pytest.raises(Exception, match="permission denied"):  # noqa: B017
        with tenant_tx(world.tenant_id) as session:
            session.execute(text("update audit_events set data = '{}'"))


def test_stale_version_is_refused(client, world):
    automation = world.order_automation()
    headers = signed_in_org(client, world)
    response = client.post(f"/v1/automations/{automation['id']}/pause", json={"expected_version": 999}, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT_VERSION"


def test_idempotency_key_replays_the_same_automation(client, world):
    headers = {**signed_in_org(client, world), "Idempotency-Key": "create-automation-1"}
    body = {"skill_id": "multichannel_order_entry"}
    first = client.post("/v1/automations", json=body, headers=headers).json()
    second = client.post("/v1/automations", json=body, headers=headers).json()
    assert first["id"] == second["id"]
    changed = client.post("/v1/automations", json={**body, "name": "Different"}, headers=headers)
    assert changed.status_code == 409


def test_activation_is_blocked_with_a_list_of_what_is_missing(client, world):
    automation = world.order_automation()
    headers = signed_in_org(client, world)
    response = client.post(
        f"/v1/automations/{automation['id']}/activate",
        json={"mode": "live", "expected_version": automation["version"]},
        headers=headers,
    )
    assert response.status_code == 409
    kinds = {m["kind"] for m in response.json()["error"]["details"]["missing"]}
    assert {"policy", "connection", "budget", "trial"} <= kinds


def test_policy_cannot_be_signed_by_a_non_owner(world):
    from helpers import add_member

    user_id, _ = make_user()
    admin = add_member(world.tenant_id, user_id, "admin")
    automation = world.order_automation()
    with pytest.raises(GluonsError, match="policies:sign"):
        with tenant_tx(world.tenant_id, user_id) as session:
            proposed = policy.propose(session, admin, automation["id"], {"mode": "supervised", "allowed_actions": []})
            policy.sign(session, admin, proposed.id)


def test_task_status_matches_across_rest_and_service(client, world, erp, sandbox_erp):
    from helpers import drain

    world.connect_erp(sandbox_erp)
    automation = world.supervised_automation()
    order_event(world, automation["id"], "status-1")
    drain()
    headers = signed_in_org(client, world)
    data = client.get(f"/v1/tasks/{automation['id']}", headers=headers).json()
    assert data["run_counts"] == {"waiting_approval": 1}
    assert len(data["pending_approvals"]) == 1
    current = lifecycle.get(world.owner, automation["id"])
    assert data["version"] == current["version"]


def test_health_and_readiness(client, database):
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] == "ok"


def test_schema_has_no_tenant_table_outside_the_inspector(database):
    names = set(inspect(app_engine()).get_table_names())
    assert set(TENANT_TABLES) <= names
