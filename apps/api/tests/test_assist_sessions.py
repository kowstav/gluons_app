"""Field representative sessions through the real role, PostgreSQL and HTTP stack."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from gluons import assist, vault
from gluons.api.app import create_app
from gluons.authz import ASSISTANT_SCOPES, Principal
from gluons.core import GluonsError, utcnow
from gluons.db.models import ActionReceipt, AssistSession, AuditEvent, Automation, Run, Trial
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle, worker
from helpers import add_member, make_user
from test_api_foundation import sign_in


@pytest.fixture
def rep(world):
    user_id, _ = make_user()
    return add_member(world.tenant_id, user_id, "field_rep")


def active(world, rep, **consent):
    row = assist.request_session(rep)
    return assist.consent(world.owner, row["id"], **consent)


def code_of(fn, *args, **kwargs) -> str:
    with pytest.raises(GluonsError) as error:
        fn(*args, **kwargs)
    return error.value.code


def age_session(world, session_id):
    with tenant_tx(world.tenant_id) as session:
        session.execute(update(AssistSession).where(AssistSession.id == session_id).values(expires_at=utcnow() - timedelta(seconds=1)))


def test_rep_role_cannot_reach_secrets_money_or_policy(rep):
    for permission in ("connections:manage", "policies:sign", "policies:write", "billing:read", "billing:manage", "data:export",
                       "automations:activate", "automations:write", "approvals:decide", "members:manage", "assist:consent"):
        assert not rep.can(permission), permission
    assert rep.can("assist:use")


def test_only_a_field_rep_can_request_and_only_the_owner_can_consent(world, rep):
    assert code_of(assist.request_session, world.owner) == "FORBIDDEN"
    row = assist.request_session(rep)
    assert row["status"] == "requested" and row["consent"] == {}
    assert assist.request_session(rep)["id"] == row["id"]
    assert code_of(assist.consent, rep, row["id"], allow_shadow_trial=True) == "FORBIDDEN"
    consented = assist.consent(world.owner, row["id"], allow_shadow_trial=True)
    assert consented["status"] == "active" and consented["owner_consent_by"] == world.owner.actor_id
    assert code_of(assist.consent, world.owner, row["id"]) == "CONFLICT"


def test_assistants_can_neither_use_nor_consent_to_assist(world, rep):
    assert all("assist:consent" not in p and "assist:use" not in p for p in ASSISTANT_SCOPES.values())
    row = assist.request_session(rep)
    assistant = Principal(kind="assistant", tenant_id=world.tenant_id, user_id=world.user_id, role=world.owner.role,
                          scopes=frozenset(ASSISTANT_SCOPES))
    assert code_of(assist.consent, assistant, row["id"]) == "FORBIDDEN"


def test_nothing_is_visible_before_consent(world, rep):
    row = assist.request_session(rep)
    assert code_of(assist.workspace, rep, row["id"]) == "ASSIST_CONSENT_REQUIRED"
    assert code_of(assist.setup_shadow_trial, rep, row["id"], "property_maintenance") == "ASSIST_CONSENT_REQUIRED"


def test_workspace_exposes_no_secret_or_account_identity(world, rep):
    connection_id = world.connect_erp("http://secret-erp.example/base")
    with tenant_tx(world.tenant_id) as session:
        from gluons.db.models import Connection, Organization
        session.get(Organization, world.tenant_id).business_facts = {"bank_account": "private-fact"}
        credential = session.get(Connection, connection_id).credential_ref
        assert vault.read(session, world.tenant_id, credential)["api_secret"] == "sandbox-secret"
    row = active(world, rep)
    view = assist.workspace(rep, row["id"])
    text = json.dumps(view)
    assert view["secrets_visible"] is False and view["connections"] == [{"provider": "erpnext", "status": "connected", "environment": "simulator"}]
    for leaked in ("sandbox-secret", "sandbox-key", "secret-erp.example", "private-fact", credential):
        assert leaked not in text


def test_shadow_trial_needs_its_own_consent_and_runs_with_zero_external_writes(world, rep):
    plain = active(world, rep)
    assert code_of(assist.setup_shadow_trial, rep, plain["id"], "property_maintenance") == "ASSIST_CONSENT_REQUIRED"
    assist.end_session(rep, plain["id"], {})

    row = active(world, rep, allow_shadow_trial=True)
    started = assist.setup_shadow_trial(rep, row["id"], "property_maintenance")
    assert started["data_basis"] == "synthetic" and started["external_writes_possible"] is False
    status = lifecycle.get(world.owner, started["automation_id"])
    assert status["status"] == "trial_shadow" and status["mode"] == "shadow"
    assert code_of(lifecycle.activate, rep, started["automation_id"], "live", status["version"]) == "FORBIDDEN"
    event = lifecycle.submit_event(world.tenant_id, started["automation_id"], source="synthetic", source_event_id="demo-1",
                                   payload={"facts": {}, "source_evidence": ["fixture:demo"]})
    worker.drain()
    with tenant_tx(world.tenant_id) as session:
        assert session.get(Run, event["run_id"]).status == "completed"
        assert session.execute(select(ActionReceipt).where(ActionReceipt.run_id == event["run_id"])).first() is None
        assert session.get(Trial, started["trial_id"]).comparator == {}


def test_sample_data_needs_separate_consent_and_never_regulated(world, rep):
    row = active(world, rep, allow_shadow_trial=True)
    assert code_of(assist.setup_shadow_trial, rep, row["id"], "property_maintenance", data_basis="owner_consented_sample") == "ASSIST_CONSENT_REQUIRED"
    assist.end_session(rep, row["id"], {})
    row = active(world, rep, allow_shadow_trial=True, allow_sample_data=True)
    assert assist.setup_shadow_trial(rep, row["id"], "property_maintenance", data_basis="owner_consented_sample")["data_basis"] == "owner_consented_sample"
    assert code_of(assist.setup_shadow_trial, rep, row["id"], "dental_reception_and_claims", data_basis="owner_consented_sample") == "ASSIST_CONSENT_REQUIRED"
    assert code_of(assist.setup_shadow_trial, rep, row["id"], "property_maintenance", data_basis="scraped") == "VALIDATION_FAILED"


def test_time_box_ends_the_session_durably(world, rep):
    row = active(world, rep, allow_shadow_trial=True)
    assert row["expires_at"] > utcnow().isoformat()
    age_session(world, row["id"])
    assert code_of(assist.workspace, rep, row["id"]) == "ASSIST_SESSION_EXPIRED"
    assert assist.get_session(rep, row["id"])["status"] == "expired"
    assert code_of(assist.setup_shadow_trial, rep, row["id"], "property_maintenance") == "CONFLICT"


def test_unconsented_request_expires_too(world, rep):
    row = assist.request_session(rep)
    age_session(world, row["id"])
    assert code_of(assist.consent, world.owner, row["id"]) == "ASSIST_SESSION_EXPIRED"


def test_owner_revocation_stops_the_rep_immediately(world, rep):
    row = active(world, rep, allow_shadow_trial=True)
    assert assist.revoke(world.owner, row["id"])["status"] == "revoked"
    assert code_of(assist.workspace, rep, row["id"]) == "CONFLICT"
    assert code_of(assist.revoke, rep, row["id"]) == "FORBIDDEN"


def test_completion_report_separates_records_from_rep_notes_and_refuses_credentials(world, rep):
    row = active(world, rep, allow_shadow_trial=True)
    started = assist.setup_shadow_trial(rep, row["id"], "property_maintenance")
    assert code_of(assist.end_session, rep, row["id"], {"notes": "owner password is hunter2"}) == "VALIDATION_FAILED"
    assert code_of(assist.end_session, rep, row["id"], {"savings_verified": True}) == "VALIDATION_FAILED"
    ended = assist.end_session(rep, row["id"], {"notes": "Walked through the shadow trial.", "owner_next_steps": ["Add pass criteria"]})
    report = ended["completion_report"]
    assert ended["status"] == "completed"
    assert report["recorded"]["trials_started"][0]["trial_id"] == started["trial_id"]
    assert report["rep_supplied_unverified"]["notes"] == "Walked through the shadow trial."
    assert code_of(assist.end_session, rep, row["id"], {}) == "CONFLICT"


def test_every_step_is_audited_against_the_real_actor(world, rep):
    row = active(world, rep, allow_shadow_trial=True)
    assist.setup_shadow_trial(rep, row["id"], "property_maintenance")
    assist.end_session(rep, row["id"], {})
    with tenant_tx(world.tenant_id) as session:
        rows = session.execute(select(AuditEvent).where(AuditEvent.resource_id == row["id"]).order_by(AuditEvent.seq)).scalars().all()
        created = session.execute(select(Automation)).scalars().first()
    assert [r.event_type for r in rows] == ["assist.requested", "assist.consented", "assist.shadow_trial_started", "assist.ended"]
    assert rows[1].actor_id == world.owner.actor_id and rows[2].actor_id == rep.actor_id
    assert created.created_by == rep.actor_id


def test_one_rep_cannot_see_another_reps_session(world, rep):
    row = assist.request_session(rep)
    other_id, _ = make_user()
    other = add_member(world.tenant_id, other_id, "field_rep")
    assert code_of(assist.get_session, other, row["id"]) == "NOT_FOUND"
    assert assist.list_sessions(other) == []
    assert [s["id"] for s in assist.list_sessions(world.owner)] == [row["id"]]


def test_assist_session_is_tenant_isolated(world, rep):
    from helpers import World

    row = assist.request_session(rep)
    assert code_of(assist.get_session, World.create("Other").owner, row["id"]) == "NOT_FOUND"


def test_assist_rest_flow_over_http(world):
    user_id, email = make_user()
    add_member(world.tenant_id, user_id, "field_rep")
    rep_client, owner_client = TestClient(create_app()), TestClient(create_app())
    rep_headers = sign_in(rep_client, email)
    assert rep_client.post(f"/v1/organizations/{world.tenant_id}/select", headers=rep_headers).status_code == 200
    owner_headers = sign_in(owner_client, world.email)
    assert owner_client.post(f"/v1/organizations/{world.tenant_id}/select", headers=owner_headers).status_code == 200

    created = rep_client.post("/v1/assist/sessions", headers=rep_headers)
    assert created.status_code == 201
    session_id = created.json()["id"]
    assert rep_client.post(f"/v1/assist/sessions/{session_id}/consent", json={}, headers=rep_headers).status_code == 403
    consented = owner_client.post(f"/v1/assist/sessions/{session_id}/consent", json={"allow_shadow_trial": True}, headers=owner_headers)
    assert consented.status_code == 200 and consented.json()["status"] == "active"
    assert rep_client.get(f"/v1/assist/sessions/{session_id}/workspace").status_code == 200
    trial = rep_client.post(f"/v1/assist/sessions/{session_id}/shadow-trial", json={"skill_id": "property_maintenance"}, headers=rep_headers)
    assert trial.status_code == 201
    ended = rep_client.post(f"/v1/assist/sessions/{session_id}/end", json={"report": {"notes": "Done."}}, headers=rep_headers)
    assert ended.status_code == 200 and ended.json()["status"] == "completed"
    assert len(owner_client.get("/v1/assist/sessions").json()["sessions"]) == 1
