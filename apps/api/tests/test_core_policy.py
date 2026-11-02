"""Every way an action might slip past the gate. Uses a labelled test double."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select, text

import doubles
from gluons import approvals, gateway, policy, vault
from gluons.actions import ProviderError
from gluons.core import new_id, utcnow
from gluons.db.models import (
    ActionAuthorization,
    ActionReceipt,
    Approval,
    AutomationPolicy,
    Connection,
    Run,
)
from gluons.db.session import system_tx, tenant_tx
from gluons.gateway import ActionRequest
from gluons.net import GuardedClient, SideEffectBlocked, write_permit
from gluons.runtime import lifecycle
from helpers import World


@pytest.fixture
def connector():
    return doubles.install()


@pytest.fixture
def setup(world, connector):
    return Harness(world)


class Harness:
    def __init__(self, world: World) -> None:
        self.world = world
        self.connection_id = self._connection()
        self.automation = world.order_automation()
        self.automation_id = self.automation["id"]
        self.run_id = self._run()

    def _connection(self, *, scopes=("write",), confirmed=True, status="connected") -> str:
        world = self.world
        with tenant_tx(world.tenant_id, world.user_id) as session:
            connection = Connection(
                id=new_id("con"), tenant_id=world.tenant_id, provider="testprov", environment="simulator",
                external_account_id=new_id("acct"), account_display="Test account",
                account_confirmed_at=utcnow() if confirmed else None, granted_scopes=list(scopes),
                status=status, created_by=world.user_id,
            )
            session.add(connection)
        return connection.id

    def _run(self, mode: str = "live") -> str:
        run_id = new_id("run")
        world = self.world
        with tenant_tx(world.tenant_id) as session:
            session.add(
                Run(
                    id=run_id, tenant_id=world.tenant_id, automation_id=self.automation_id,
                    skill_id="multichannel_order_entry", skill_version="1.0.0", mode=mode,
                    source_event_id=run_id, status="running",
                )
            )
        return run_id

    def grant(self, **spec) -> str:
        world = self.world
        body = {"mode": "live", "allowed_actions": ["test.write", "test.spend", "test.regulated"],
                "connection_ids": [self.connection_id], **spec}
        with tenant_tx(world.tenant_id, world.user_id) as session:
            proposed = policy.propose(session, world.owner, self.automation_id, body)
            policy.sign(session, world.owner, proposed.id)
            return proposed.id

    def activate(self) -> None:
        world = self.world
        with tenant_tx(world.tenant_id) as session:
            session.execute(text("update automations set status = 'active', mode = 'live' where id = :i"), {"i": self.automation_id})

    def request(self, action="test.write", key=None, mode="live", connection_id=None, **payload) -> ActionRequest:
        return ActionRequest(
            tenant_id=self.world.tenant_id,
            action=action,
            input={"value": "x", **payload},
            idempotency_key=key or new_id("key"),
            mode=mode,
            automation_id=self.automation_id,
            run_id=self.run_id,
            connection_id=connection_id or self.connection_id,
        )

    def execute(self, **kwargs):
        return gateway.execute(self.request(**kwargs))


def test_connecting_an_account_grants_nothing(setup, connector):
    setup.activate()
    result = setup.execute()
    assert result.status == "denied" and result.reasons == ("NO_ACTIVE_POLICY",)
    assert connector.writes == []


def test_granted_action_runs_once_and_is_verified(setup, connector):
    setup.grant()
    setup.activate()
    result = setup.execute()
    assert result.status == "success" and result.verified is True
    assert len(connector.writes) == 1


def test_action_outside_the_grant_is_denied(setup, connector):
    setup.grant(allowed_actions=["test.spend"])
    setup.activate()
    assert setup.execute().reasons == ("ACTION_NOT_GRANTED",)
    assert connector.writes == []


def test_provider_scope_is_a_hard_ceiling(setup, connector):
    weak = setup._connection(scopes=("read",))
    setup.grant(connection_ids=[weak])
    setup.activate()
    assert setup.execute(connection_id=weak).reasons == ("PROVIDER_SCOPE_MISSING",)
    assert connector.writes == []


def test_unconfirmed_account_blocks_writes(setup, connector):
    unconfirmed = setup._connection(confirmed=False)
    setup.grant(connection_ids=[unconfirmed])
    setup.activate()
    assert setup.execute(connection_id=unconfirmed).reasons == ("ACCOUNT_IDENTITY_UNCONFIRMED",)


def test_unhealthy_connection_blocks_writes(setup, connector):
    stale = setup._connection(status="needs_reauth")
    setup.grant(connection_ids=[stale])
    setup.activate()
    assert setup.execute(connection_id=stale).reasons == ("CONNECTION_NOT_HEALTHY",)


def test_connection_outside_the_policy_is_denied(setup, connector):
    other = setup._connection()
    setup.grant()
    setup.activate()
    assert setup.execute(connection_id=other).reasons == ("CONNECTION_NOT_IN_POLICY",)


def test_another_tenants_connection_cannot_be_used(setup, connector):
    other = World.create("Other")
    foreign = Harness(other).connection_id
    setup.grant()
    setup.activate()
    assert setup.execute(connection_id=foreign).status == "denied"
    assert connector.writes == []


def test_revocation_fences_queued_work(setup, connector):
    policy_id = setup.grant()
    setup.activate()
    with tenant_tx(setup.world.tenant_id, setup.world.user_id) as session:
        policy.revoke(session, setup.world.owner, policy_id)
    assert setup.execute().status == "denied"
    assert connector.writes == []


def test_expired_policy_is_denied(setup, connector):
    setup.grant(expires_at=utcnow() + timedelta(milliseconds=1))
    setup.activate()
    # Tamper-proof expiry: signed into the document, so it cannot be edited to extend it.
    with tenant_tx(setup.world.tenant_id) as session:
        session.execute(text("select pg_sleep(0.01)"))
    assert setup.execute().reasons == ("POLICY_EXPIRED",)


def test_tampered_policy_row_fails_signature_check(setup, connector):
    setup.grant(allowed_actions=["test.spend"])
    setup.activate()
    with system_tx() as session:
        session.execute(text("update automation_policies set allowed_actions = '{test.write}'"))
    assert setup.execute().reasons == ("POLICY_SIGNATURE_INVALID",)
    assert connector.writes == []


def test_policy_for_another_mode_does_not_apply(setup, connector):
    setup.grant(mode="supervised")
    setup.activate()
    assert setup.execute(mode="live").reasons == ("POLICY_MODE_MISMATCH",)


def test_r4_action_is_never_allowed(setup, connector):
    setup.grant()
    setup.activate()
    assert setup.execute(action="test.regulated").reasons == ("R4_HUMAN_ONLY",)


def test_unknown_action_is_refused(setup, connector):
    assert setup.execute(action="shell.exec").reasons == ("ACTION_NOT_ALLOWLISTED",)


def test_invalid_payload_never_reaches_the_connector(setup, connector):
    setup.grant()
    setup.activate()
    assert setup.execute(extra_field="drop tables").reasons == ("INVALID_ACTION_INPUT",)
    assert connector.writes == []


def test_undeclared_action_for_a_skill_is_denied(setup, connector):
    setup.grant()
    setup.activate()
    base = setup.request()
    request = ActionRequest(**{**base.__dict__, "skill_actions": ("erp.order.create",)})
    assert gateway.execute(request).reasons == ("ACTION_NOT_DECLARED_BY_SKILL",)


def test_emergency_stop_blocks_everything_and_release_restores(setup, connector):
    setup.grant()
    setup.activate()
    lifecycle.set_emergency_stop(setup.world.owner, True)
    assert setup.execute().reasons == ("EMERGENCY_STOP_ACTIVE",)
    lifecycle.set_emergency_stop(setup.world.owner, False)
    assert setup.execute().status == "success"


def test_pausing_outgoing_messages_leaves_other_work_running(setup, connector):
    setup.grant(auto_limits={"test.spend": {"max_amount_minor": 1000}})
    setup.activate()
    lifecycle.set_paused_categories(setup.world.owner, ["send"])
    assert setup.execute().reasons == ("CATEGORY_PAUSED",)
    assert setup.execute(action="test.spend", amount_minor=500).status == "success"


def test_recipient_rules_are_enforced_outside_the_model(setup, connector):
    setup.grant(resource_filters={"test.write": {"allowed_recipient_domains": ["harbor-bistro.com"]}})
    setup.activate()
    assert setup.execute(recipient="attacker@evil.example").reasons == ("RECIPIENT_DOMAIN_NOT_ALLOWED",)
    assert setup.execute(recipient="orders@harbor-bistro.com").status == "success"


def test_transactional_send_needs_a_verified_order(setup, connector):
    setup.grant(resource_filters={"test.write": {"requires_verified_order": True}})
    setup.activate()
    assert setup.execute().reasons == ("ORDER_NOT_VERIFIED",)


def test_marketing_needs_consent(setup, connector):
    setup.grant(resource_filters={"test.write": {"requires_opt_in": True}})
    setup.activate()
    assert setup.execute().reasons == ("CONSENT_MISSING",)


def test_per_action_limit(setup, connector):
    setup.grant(per_action_limit_minor=1000, auto_limits={"test.spend": {"max_amount_minor": 5000}})
    setup.activate()
    assert setup.execute(action="test.spend", amount_minor=1001).reasons == ("PER_ACTION_LIMIT_EXCEEDED",)


def test_r3_above_the_narrow_grant_needs_approval(setup, connector):
    setup.grant(auto_limits={"test.spend": {"max_amount_minor": 1000}})
    setup.activate()
    assert setup.execute(action="test.spend", amount_minor=999).status == "success"
    assert setup.execute(action="test.spend", amount_minor=1001).status == "waiting_approval"
    assert len(connector.writes) == 1


def test_monthly_spend_limit_holds_under_real_concurrency(setup, connector):
    setup.grant(spend_limit_minor_per_month=5000, auto_limits={"test.spend": {"max_amount_minor": 1000}})
    setup.activate()

    def attempt(_):
        return setup.execute(action="test.spend", amount_minor=1000).status

    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(attempt, range(20)))
    assert statuses.count("success") == 5
    assert statuses.count("denied") == 15
    assert len(connector.writes) == 5


def test_same_key_from_many_threads_writes_once(setup, connector):
    setup.grant()
    setup.activate()
    request = setup.request(key="same-key")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: gateway.execute(request), range(8)))
    assert len(connector.writes) == 1
    assert {r.status for r in results} <= {"success", "retry"}


def test_shadow_never_calls_execute_and_labels_nothing_as_a_receipt(setup, connector):
    result = setup.execute(mode="shadow")
    assert result.status == "preview" and result.verified is False
    assert connector.writes == [] and connector.previews == 1


def test_shadow_preview_that_tries_to_write_is_blocked_at_the_http_layer(setup, connector):
    connector.unsafe_preview = True
    with pytest.raises(SideEffectBlocked):
        setup.execute(mode="shadow")
    assert connector.writes == []


def test_guarded_client_blocks_writes_without_a_permit_and_unknown_hosts():
    client = GuardedClient(("provider.test",))
    with pytest.raises(SideEffectBlocked):
        client.post("https://provider.test/x")
    from gluons.net import HostNotAllowed

    with pytest.raises(HostNotAllowed), write_permit("test"):
        client.post("https://evil.example/x")


def test_retryable_failure_does_not_consume_a_second_write(setup, connector):
    setup.grant()
    setup.activate()
    connector.fail_with = ProviderError("retryable", "503", status=503)
    first = setup.execute(key="retry-key")
    assert first.status == "retry"
    second = setup.execute(key="retry-key")
    assert second.status == "success"
    assert len(connector.writes) == 1


def test_auth_failure_marks_connection_and_stops(setup, connector):
    setup.grant()
    setup.activate()
    connector.fail_with = ProviderError("auth", "token revoked", status=401)
    assert setup.execute().status == "waiting_external"
    with tenant_tx(setup.world.tenant_id) as session:
        assert session.get(Connection, setup.connection_id).status == "needs_reauth"
    assert setup.execute().reasons == ("CONNECTION_NOT_HEALTHY",)


def test_ambiguous_timeout_after_policy_revoked_still_reports_the_truth(setup, connector):
    """The write may have landed. Revocation refuses a retry but must not hide what happened."""
    policy_id = setup.grant()
    setup.activate()
    connector.objects["ambig"] = "ext_landed"
    connector.fail_with = ProviderError("ambiguous", "timeout")
    assert setup.execute(key="ambig").reasons == ("OUTCOME_UNCERTAIN",)
    with tenant_tx(setup.world.tenant_id, setup.world.user_id) as session:
        policy.revoke(session, setup.world.owner, policy_id)
    result = setup.execute(key="ambig")
    assert result.status == "success" and result.verified is True
    assert result.provider_object_id == "ext_landed"
    assert connector.writes == []


def test_readback_mismatch_is_never_verified(setup, connector):
    setup.grant()
    setup.activate()
    connector.misreport = True
    result = setup.execute()
    assert result.status == "mismatch" and result.verified is False
    with tenant_tx(setup.world.tenant_id) as session:
        assert session.execute(select(ActionReceipt)).scalar_one().status == "mismatch"


def test_every_decision_is_logged_including_denials(setup, connector):
    setup.activate()
    setup.execute()
    with tenant_tx(setup.world.tenant_id) as session:
        row = session.execute(select(ActionAuthorization)).scalar_one()
    assert row.outcome == "deny" and row.reasons == ["NO_ACTIVE_POLICY"]
    assert row.payload_hash and row.idempotency_key


# --- Approval bypass attempts ------------------------------------------------------------


def waiting(setup, **kwargs) -> Approval:
    setup.grant(mode="supervised")
    with tenant_tx(setup.world.tenant_id) as session:
        session.execute(text("update automations set status = 'active', mode = 'supervised' where id = :i"), {"i": setup.automation_id})
    result = setup.execute(mode="supervised", key="approve-key", **kwargs)
    assert result.status == "waiting_approval"
    with tenant_tx(setup.world.tenant_id) as session:
        return session.get(Approval, result.approval_id)


def decide(setup, approval_id, principal=None, decision="approve"):
    principal = principal or setup.world.owner
    with tenant_tx(setup.world.tenant_id, principal.user_id) as session:
        return approvals.decide(session, principal, approval_id, decision)


def test_changed_payload_cannot_use_an_earlier_approval(setup, connector):
    approval = waiting(setup)
    decide(setup, approval.id)
    # The same key with a different body is a different action, so it waits again.
    tampered = setup.execute(mode="supervised", key="approve-key", value="changed")
    assert tampered.status == "waiting_approval"
    assert tampered.approval_id != approval.id
    assert connector.writes == []


def test_approval_covers_one_write_only(setup, connector):
    approval = waiting(setup)
    decide(setup, approval.id)
    assert setup.execute(mode="supervised", key="approve-key").status == "success"
    second = setup.execute(mode="supervised", key="a-different-key")
    assert second.reasons == ("APPROVAL_ALREADY_USED",)
    assert len(connector.writes) == 1


def test_denied_approval_blocks_the_action(setup, connector):
    approval = waiting(setup)
    decide(setup, approval.id, decision="deny")
    assert setup.execute(mode="supervised", key="approve-key").reasons == ("APPROVAL_DENIED",)
    assert connector.writes == []


def test_expired_approval_cannot_be_decided_or_used(setup, connector):
    from gluons.core import GluonsError

    approval = waiting(setup)
    with tenant_tx(setup.world.tenant_id) as session:
        session.execute(text("update approvals set expires_at = now() - interval '1 minute' where id = :i"), {"i": approval.id})
    with pytest.raises(GluonsError, match="expired"):
        decide(setup, approval.id)
    assert setup.execute(mode="supervised", key="approve-key").status == "denied"
    assert connector.writes == []


def test_policy_row_shape_matches_blueprint_schema(setup):
    setup.grant()
    with tenant_tx(setup.world.tenant_id) as session:
        row = session.execute(select(AutomationPolicy)).scalar_one()
    assert row.signature and row.approved_by and row.approved_at


def test_credentials_are_never_in_receipts_or_audit(setup, connector):
    with tenant_tx(setup.world.tenant_id) as session:
        ref = vault.store(session, setup.world.tenant_id, "test", {"access_token": "super-secret-token"})
        session.execute(text("update connections set credential_ref = :r"), {"r": ref})
    setup.grant()
    setup.activate()
    setup.execute()
    with system_tx() as session:
        for table in ("action_receipts", "audit_events", "action_authorizations", "run_events"):
            dump = session.execute(text(f"select coalesce(json_agg(t)::text, '') from {table} t")).scalar_one()  # noqa: S608
            assert "super-secret-token" not in dump
