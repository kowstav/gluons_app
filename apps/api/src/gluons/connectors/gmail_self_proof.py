"""A narrowly scoped, local-only synthetic self-send through the normal gateway."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from secrets import compare_digest

import httpx
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from gluons import approvals, policy, vault
from gluons.actions import ActionSpec, ConnectorContext, ProviderError, ProviderResult, register_action, register_connector
from gluons.config import REPO_ROOT, get_settings
from gluons.connections import google
from gluons.connectors.gmail import GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE, GmailSendConnector, SendEmail
from gluons.core import GluonsError, constant_time_equals, fingerprint, utcnow
from gluons.db.models import (
    ActionReceipt,
    Approval,
    ApprovalDecision,
    Automation,
    AutomationPolicy,
    Connection,
    Membership,
    Organization,
)
from gluons.db.session import tenant_tx

ACTION = "proof.gmail.self_send"
SUBJECT = "[Gluons self-test] Connection proof"
BODY = "Synthetic Gluons connector proof. No customer or order data."


def isolated_proof_enabled() -> bool:
    settings = get_settings()
    if settings.hosted or not settings.gmail_proof_enabled:
        return False
    assets = REPO_ROOT / "apps" / "api" / "tests" / "provider_proof_assets"
    if (Path(settings.skills_dir).resolve() != (assets / "skills").resolve()
            or Path(settings.blueprint_dir).resolve() != (assets / "blueprint").resolve()):
        return False
    def local_database(raw: str) -> bool:
        url = make_url(raw)
        # libpq's query parameters can override the authority host. Reject IP
        # overrides and mixed/multiple hosts; Unix sockets must be explicit paths.
        socket = url.query.get("host")
        if "hostaddr" in url.query:
            return False
        if socket is not None:
            return url.host is None and isinstance(socket, str) and socket.startswith("/") and "," not in socket
        return url.host in {"127.0.0.1", "localhost", "::1"}

    try:
        return all(local_database(url) for url in
                   (settings.database_url, settings.system_database_url, settings.migration_database_url))
    except (ArgumentError, ValueError, TypeError):
        return False


class GmailSelfProofConnector(GmailSendConnector):
    def execute(self, ctx: ConnectorContext, payload: SendEmail, idempotency_key: str) -> ProviderResult:
        email = ctx.metadata.get("account_email")
        if (not isolated_proof_enabled() or ctx.environment != "production" or not isinstance(email, str)
                or str(payload.to).casefold() != email.casefold() or payload.subject != SUBJECT
                or payload.body != BODY or payload.thread_id is not None or not ctx.external_account_id):
            raise ProviderError("permanent", "The isolated Gmail self-proof constraints were not met.")
        write_started = False
        try:
            # Do network identity work before taking authority locks, so an emergency stop
            # can win while Google is slow. The final transaction compares the token again.
            credential = ctx.credentials()
            token = credential.get("access_token")
            if not isinstance(token, str) or not token:
                raise ProviderError("auth", "Reconnect the confirmed Gmail account.")
            identity = google.identity(token)
            if (identity.get("sub") != ctx.external_account_id or not isinstance(identity.get("email"), str)
                    or identity["email"].casefold() != email.casefold()):
                raise ProviderError("auth", "The Gmail identity does not match the confirmed account.")
            # Lock relevant authority only for the final check and dispatch. A concurrent
            # revocation or downgrade must serialize before or after the provider request.
            with tenant_tx(ctx.tenant_id) as session:
                receipt = session.scalar(select(ActionReceipt).where(ActionReceipt.action == ACTION,
                    ActionReceipt.idempotency_key == idempotency_key).with_for_update())
                if receipt is None or receipt.status != "pending" or receipt.connection_id != ctx.connection_id:
                    raise ProviderError("permanent", "The proof receipt is unavailable.")
                automation = session.get(Automation, receipt.automation_id, with_for_update=True)
                grant = session.get(AutomationPolicy, receipt.policy_id, with_for_update=True)
                approval = session.get(Approval, receipt.approval_id, with_for_update=True)
                connection = session.get(Connection, receipt.connection_id, with_for_update=True)
                org = session.get(Organization, ctx.tenant_id, with_for_update=True)
                now = utcnow()
                expected_hash = fingerprint({"action": ACTION, "connection_id": ctx.connection_id,
                                             "input": payload.model_dump(mode="json")})
                if (automation is None or automation.skill_id != "gmail_self_proof" or automation.status != "active"
                        or automation.mode != "supervised" or grant is None or automation.current_policy_id != grant.id
                        or grant.status != "active" or grant.revoked_at is not None or grant.mode != "supervised"
                        or not policy.signature_valid(grant) or grant.allowed_actions != [ACTION]
                        or grant.connection_ids != [ctx.connection_id] or grant.expires_at is None
                        or not now < grant.expires_at <= now + timedelta(hours=24)
                        or grant.resource_filters != {ACTION: {"allowed_recipients": [str(payload.to)]}}
                        or receipt.payload_hash != expected_hash or receipt.mode != "supervised"
                        or receipt.skill_id != "gluons.gmail_self_proof" or receipt.environment != "production"
                        or approval is None or approval.status != "approved" or approval.required_role != "owner"
                        or approval.action != ACTION or approval.risk_tier != "R3" or approval.payload_hash != expected_hash
                        or approval.automation_id != automation.id
                        or approval.idempotency_key != idempotency_key or approval.run_id != receipt.run_id
                        or approval.connection_id != ctx.connection_id or approval.consumed_at is None
                        or approval.expires_at <= now or org is None or org.emergency_stop_at is not None
                        or "send" in org.paused_categories):
                    raise ProviderError("permanent", "The bounded owner grant is no longer valid.")
                decisions = session.scalars(select(ApprovalDecision).where(ApprovalDecision.approval_id == approval.id,
                    ApprovalDecision.decision == "approve", ApprovalDecision.role == "owner", ApprovalDecision.via == "web")).all()
                if len(decisions) != 1:
                    raise ProviderError("permanent", "A signed owner decision is required.")
                decision = decisions[0]
                member = session.scalar(select(Membership).where(Membership.user_id == decision.user_id).with_for_update())
                signer = session.scalar(select(Membership).where(Membership.user_id == grant.approved_by).with_for_update())
                if (member is None or member.role != "owner" or member.removed_at is not None
                        or signer is None or signer.role != "owner" or signer.removed_at is not None
                        or not constant_time_equals(decision.signature,
                            approvals._sign_decision(approval, decision.user_id, decision.decision, decision.decided_at))):
                    raise ProviderError("permanent", "The owner authority is no longer valid.")
                if (connection is None or connection.provider != "gmail" or connection.environment != "production" or connection.status != "connected"
                        or connection.disconnected_at is not None or connection.account_confirmed_at is None
                        or connection.external_account_id != ctx.external_account_id
                        or connection.account_display.casefold() != email.casefold()
                        or not isinstance(connection.metadata_json.get("account_email"), str)
                        or connection.metadata_json.get("account_email", "").casefold() != email.casefold()
                        or not {GMAIL_SEND_SCOPE, GMAIL_READ_SCOPE}.issubset(set(connection.granted_scopes))):
                    raise ProviderError("auth", "Reconnect the confirmed Gmail account.")
                if not connection.credential_ref:
                    raise ProviderError("auth", "Reconnect the confirmed Gmail account.")
                current_token = vault.read(session, ctx.tenant_id, connection.credential_ref).get("access_token")
                if (not isinstance(current_token, str) or not compare_digest(token, current_token)
                        or (connection.token_expires_at is not None and connection.token_expires_at <= utcnow())):
                    raise ProviderError("auth", "The Gmail connection changed before dispatch.")
                if grant.expires_at <= utcnow() or approval.expires_at <= utcnow():
                    raise ProviderError("permanent", "The bounded owner grant expired before dispatch.")
                # This exact checked token is used for the one gateway-authorized write.
                write_started = True
                return super().execute(replace(ctx, credentials=lambda: credential), payload, idempotency_key)
        except (GluonsError, httpx.HTTPError, ValueError, TypeError) as exc:
            if write_started:
                raise ProviderError("ambiguous", "The Gmail write could not be confirmed.") from exc
            raise ProviderError("auth", "The confirmed Gmail identity could not be checked.") from exc


register_action(ActionSpec(name=ACTION, permission="send", risk_tier="R3", capability="email", input_model=SendEmail,
                           description="Send one synthetic Gmail proof to the confirmed account itself",
                           required_scopes={"gmail": (GMAIL_SEND_SCOPE, GMAIL_READ_SCOPE)}, irreversible=True,
                           resources=lambda payload: {"recipient": str(payload.to)}))
register_connector("gmail", ACTION, GmailSelfProofConnector())
