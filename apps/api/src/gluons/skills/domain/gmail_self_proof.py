"""Isolated proof graph: one synthetic self-send, with normal gateway authority."""
from __future__ import annotations

from datetime import timedelta

from pydantic import ValidationError

from gluons import gateway, policy
from gluons.connectors.gmail import GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE, SendEmail
from gluons.connectors.gmail_self_proof import ACTION, BODY, SUBJECT, isolated_proof_enabled
from gluons.core import utcnow
from gluons.db.models import Automation, AutomationPolicy, Connection
from gluons.db.session import tenant_tx
from gluons.runtime.step import StepContext, StepOutcome, outcome_from_action
from gluons.skills.contract import Node
from gluons.skills.primitives import domain


@domain("proof.gmail_self_send")
def self_send(ctx: StepContext, node: Node) -> StepOutcome:
    if (not isolated_proof_enabled() or ctx.skill_id != "gluons.gmail_self_proof"
            or ctx.mode != "supervised" or tuple(ctx.skill_actions) != (ACTION,)):
        return StepOutcome(kind="fail", error="ISOLATED_PROOF_DISABLED")
    connection_id = ctx.connections.get("email")
    if not connection_id or not ctx.policy_id:
        return StepOutcome(kind="fail", error="PROOF_GRANT_REQUIRED")
    with tenant_tx(ctx.tenant_id) as session:
        connection = session.get(Connection, connection_id)
        automation = session.get(Automation, ctx.automation_id)
        grant = session.get(AutomationPolicy, ctx.policy_id)
        if (connection is None or connection.provider != "gmail" or connection.environment != "production" or connection.status != "connected"
                or connection.disconnected_at or connection.account_confirmed_at is None
                or not {GMAIL_SEND_SCOPE, GMAIL_READ_SCOPE}.issubset(set(connection.granted_scopes))):
            return StepOutcome(kind="fail", error="CONFIRMED_GMAIL_REQUIRED")
        email = connection.metadata_json.get("account_email")
        if not isinstance(email, str) or email.casefold() != connection.account_display.casefold():
            return StepOutcome(kind="fail", error="CONFIRMED_GMAIL_REQUIRED")
        try:
            payload = SendEmail(to=email, subject=SUBJECT, body=BODY).model_dump(mode="json")
        except ValidationError:
            return StepOutcome(kind="fail", error="CONFIRMED_GMAIL_REQUIRED")
        now = utcnow()
        if (automation is None or automation.skill_id != "gmail_self_proof" or automation.current_policy_id != ctx.policy_id
                or grant is None or grant.automation_id != ctx.automation_id or grant.status != "active"
                or grant.revoked_at is not None or grant.mode != "supervised" or not policy.signature_valid(grant)
                or grant.allowed_actions != [ACTION] or grant.connection_ids != [connection_id]
                or grant.expires_at is None or not now < grant.expires_at <= now + timedelta(hours=24)
                or grant.resource_filters != {ACTION: {"allowed_recipients": [payload["to"]]}}):
            return StepOutcome(kind="fail", error="BOUNDED_OWNER_POLICY_REQUIRED")

    # A stable key for the entire automation makes all duplicate events one effect.
    result = gateway.execute(gateway.ActionRequest(
        tenant_id=ctx.tenant_id, automation_id=ctx.automation_id, run_id=ctx.run_id, step_id=ctx.step_id,
        node_id=ctx.node_id, skill_id=ctx.skill_id, skill_actions=ctx.skill_actions, mode=ctx.mode,
        action=ACTION, connection_id=connection_id, pinned_policy_id=ctx.policy_id,
        idempotency_key=f"gmail-self-proof:{ctx.tenant_id}:{ctx.automation_id}", input=payload,
        title="Approve one synthetic Gmail self-test", summary="Send only to the confirmed Gmail account itself.",
        source_evidence=({"kind": "synthetic_self_test"},), transport=ctx.transport,
    ))
    outcome = outcome_from_action(result)
    if result.status == "success":
        outcome.state = {"proof_receipt": {"receipt_id": result.receipt_id, "verified": result.verified}}
    return outcome
