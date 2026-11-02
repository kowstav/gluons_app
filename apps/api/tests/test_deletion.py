"""Export and deletion against real PostgreSQL, as the restricted role."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gluons import audit, deletion, identity, knowledge, vault
from gluons.api.app import create_app
from gluons.authz import ASSISTANT_SCOPES, Principal, Role
from gluons.core import GluonsError
from gluons.db.models import (
    AgentBinding,
    Automation,
    AutomationPolicy,
    Connection,
    Customer,
    IdentitySession,
    Invitation,
    KnowledgeDocument,
    Membership,
    OAuthToken,
    Organization,
    VaultSecret,
)
from gluons.db.session import public_tx, tenant_tx
from gluons.oauth import service as oauth
from gluons.runtime import lifecycle
from helpers import World, add_member, drain, make_user, order_event
from test_api_foundation import signed_in_org
from test_assistant_oauth import issue

SECRET_KEYS = {"token_hash", "credential_ref", "ciphertext", "wrapped_dek", "code_hash", "state_hash", "pkce_verifier_ref",
               "setup_handle_hash", "nonce_hash", "client_secret_hash", "access_token", "refresh_token", "password_hash"}


def populated(world, erp) -> dict:
    world.connect_erp(erp.base)
    automation = world.supervised_automation()
    order_event(world, automation["id"])
    drain()
    knowledge.add_document(world.owner, title="Hours", body="Open nine to five.")
    identity.invite_member(world.owner, "new-person@example.com", "viewer")
    with tenant_tx(world.tenant_id, world.user_id) as session:
        session.get(Connection, world.connections["erp"]).metadata_json = {"base_url": erp.base, "api_token": "nested-secret-value"}
    return automation


def keys_in(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for child in value.values() for k in keys_in(child)}
    if isinstance(value, list):
        return {k for child in value for k in keys_in(child)}
    return set()


def code_of(fn, *args, **kwargs) -> str:
    with pytest.raises(GluonsError) as error:
        fn(*args, **kwargs)
    return error.value.code


def assistant(world) -> Principal:
    return Principal(kind="assistant", tenant_id=world.tenant_id, user_id=world.user_id, role=Role.OWNER,
                     scopes=frozenset(ASSISTANT_SCOPES), binding_id="agb_test")


def test_assistant_token_is_refused_even_with_every_scope_and_an_owner_role(world):
    assert code_of(deletion.request_deletion, assistant(world), confirm_name="Acme Foods", export_acknowledged=True) == "FORBIDDEN"
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Organization, world.tenant_id).deleted_at is None


@pytest.mark.parametrize("role", ["admin", "approver", "operator", "viewer", "field_rep", "support"])
def test_only_the_owner_holds_data_rights(world, role):
    user_id, _ = make_user()
    member = add_member(world.tenant_id, user_id, role)
    assert code_of(deletion.request_deletion, member, confirm_name="Acme Foods", export_acknowledged=True) == "FORBIDDEN"


def test_deletion_needs_the_exact_name_and_an_export_acknowledgement(world):
    assert code_of(deletion.request_deletion, world.owner, confirm_name="Acme Foods", export_acknowledged=False) == "VALIDATION_FAILED"
    assert code_of(deletion.request_deletion, world.owner, confirm_name="acme", export_acknowledged=True) == "VALIDATION_FAILED"
    with tenant_tx(world.tenant_id) as session:
        org = session.get(Organization, world.tenant_id)
        assert org.deleted_at is None and org.emergency_stop_at is None


def test_deletion_disconnects_fences_marks_and_keeps_the_audit_trail(world, erp):
    automation = populated(world, erp)
    _, _, pair = issue(world)
    report = deletion.request_deletion(world.owner, confirm_name="Acme Foods", export_acknowledged=True)
    drain()

    assert report["status"] == "deletion_requested" and report["erasure_completed"] is False
    assert report["completed_now"]["emergency_stop_engaged"] is True
    assert any("still stored" in line for line in report["not_done"])
    assert report["needs_manual_action"][0]["provider"] == "erpnext"
    assert {row["table"] for row in report["retained"]} >= {"audit_events", "approval_decisions", "usage_events", "action_receipts"}

    with tenant_tx(world.tenant_id) as session:
        org = session.get(Organization, world.tenant_id)
        assert org.deleted_at is not None and org.emergency_stop_at is not None
        connection = session.get(Connection, world.connections["erp"])
        assert connection.disconnected_at is not None and connection.credential_ref is None
        assert all(not row.ciphertext and row.destroyed_at for row in session.execute(select(VaultSecret)).scalars())
        assert session.get(Automation, automation["id"]).status == "cancelled"
        assert all(p.status == "revoked" for p in session.execute(select(AutomationPolicy)).scalars())
        assert all(c.suppressed for c in session.execute(select(Customer)).scalars())
        assert all(d.status == "deletion_pending" for d in session.execute(select(KnowledgeDocument)).scalars())
        assert all(i.revoked_at for i in session.execute(select(Invitation)).scalars())
        assert all(b.status == "revoked" for b in session.execute(select(AgentBinding)).scalars())
        assert all(t.revoked_at for t in session.execute(select(OAuthToken)).scalars())
        assert all(m.removed_at for m in session.execute(select(Membership)).scalars())
        chain = audit.verify_chain(session, world.tenant_id)
    assert chain["valid"] is True and chain["checked"] > 5
    assert knowledge.search(world.tenant_id, "Open") == []


def test_deletion_closes_access_for_every_way_back_in(world, erp):
    populated(world, erp)
    _, _, pair = issue(world)
    client = TestClient(create_app())
    headers = signed_in_org(client, world)
    assert client.post("/v1/organizations/current/delete-request",
                       json={"confirm_name": "Acme Foods", "export_acknowledged": True}, headers=headers).status_code == 202
    assert identity.list_organizations(world.user_id) == []
    assert code_of(identity.principal_for_user, world.user_id, world.tenant_id) == "FORBIDDEN"
    assert client.get("/v1/organizations/current").status_code in (403, 409)
    assert code_of(oauth.resolve_access_token, pair["access_token"]) == "AUTH_REQUIRED"
    with public_tx() as session:
        assert session.execute(select(IdentitySession).where(IdentitySession.active_tenant_id == world.tenant_id)).first() is None


def test_deletion_runs_only_once(world):
    deletion.request_deletion(world.owner, confirm_name="Acme Foods", export_acknowledged=True)
    assert code_of(deletion.request_deletion, world.owner, confirm_name="Acme Foods", export_acknowledged=True) == "CONFLICT"


def test_interrupted_deletion_can_be_repeated(world, erp, monkeypatch):
    populated(world, erp)
    original = deletion._mark_data
    monkeypatch.setattr(deletion, "_mark_data", lambda p: (_ for _ in ()).throw(RuntimeError("worker lost")))
    with pytest.raises(RuntimeError):
        deletion.request_deletion(world.owner, confirm_name="Acme Foods", export_acknowledged=True)
    with tenant_tx(world.tenant_id) as session:
        org = session.get(Organization, world.tenant_id)
        assert org.emergency_stop_at is not None and org.deleted_at is None
    monkeypatch.setattr(deletion, "_mark_data", original)
    report = deletion.request_deletion(world.owner, confirm_name="Acme Foods", export_acknowledged=True)
    assert report["completed_now"]["automations_fenced"] == []
    assert report["status"] == "deletion_requested"


def test_deletion_leaves_other_businesses_alone(world, erp):
    other = World.create("Other Business")
    kept = other.order_automation()
    deletion.request_deletion(world.owner, confirm_name="Acme Foods", export_acknowledged=True)
    assert lifecycle.get(other.owner, kept["id"])["status"] == "ready"
    assert [o["id"] for o in identity.list_organizations(other.user_id)] == [other.tenant_id]


def test_vault_secret_is_truly_unreadable_after_deletion(world):
    with tenant_tx(world.tenant_id) as session:
        secret = vault.store(session, world.tenant_id, "other_provider", {"api_key": "x"})
    deletion.request_deletion(world.owner, confirm_name="Acme Foods", export_acknowledged=True)
    with tenant_tx(world.tenant_id) as session, pytest.raises(Exception):  # noqa: B017, PT011 - any refusal is acceptable
        vault.read(session, world.tenant_id, secret)

