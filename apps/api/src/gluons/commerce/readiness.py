"""Read-only, fail-closed view of a launch plan's current evidence."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gluons.core import fingerprint, utcnow
from gluons.db.models import ActionReceipt, Connection, LaunchEvidence, LaunchPlan

CONNECTION_MAX_AGE = timedelta(hours=24)


def plan_action_context(plan: LaunchPlan, connection: Connection) -> str:
    """Bind evidence to stable business intent and the confirmed store identity."""
    return fingerprint({"plan_id": plan.id, "goal": plan.goal, "country": plan.country,
                        "product_category": plan.product_category, "budget_usd": str(plan.budget_usd) if plan.budget_usd is not None else None,
                        "fulfillment_model": plan.fulfillment_model, "connection_id": connection.id,
                        "provider": connection.provider, "external_account_id": connection.external_account_id,
                        "environment": connection.environment,
                        "account_confirmed_at": connection.account_confirmed_at.isoformat() if connection.account_confirmed_at else None})


def _individual_receipts(session: Session, plan: LaunchPlan, connection: Connection, now: datetime) -> dict[str, list[dict[str, Any]]]:
    """Show fresh individual readbacks without claiming manifest completeness."""
    actions = {"shopify.product.create_draft": ("catalog_drafts", {"read_products"}),
               "shopify.product.publish": ("publication", {"read_products", "read_publications"}),
               "shopify.fulfillment.create": ("operations_and_fulfillment", {"read_orders"})}
    found: dict[str, list[dict[str, Any]]] = {value[0]: [] for value in actions.values()}
    evidence = session.scalars(select(LaunchEvidence).where(LaunchEvidence.plan_id == plan.id,
        LaunchEvidence.kind == "provider_action", LaunchEvidence.source == "shopify").order_by(LaunchEvidence.recorded_at))
    for item in evidence:
        receipt = session.get(ActionReceipt, item.receipt_id) if item.receipt_id else None
        if receipt is None or receipt.action not in actions:
            continue
        category, scopes = actions[receipt.action]
        if (not scopes <= set(connection.granted_scopes) or receipt.connection_id != connection.id or receipt.provider != "shopify"
                or receipt.environment != connection.environment or item.is_test != (connection.environment != "production")
                or receipt.mode not in ("live", "supervised") or receipt.status != "succeeded" or not receipt.verified
                or receipt.verified_at is None or not now - CONNECTION_MAX_AGE <= receipt.verified_at <= now
                or item.recorded_at < receipt.verified_at
                or not receipt.provider_object_id or not receipt.readback or item.detail.get("action") != receipt.action
                or item.detail.get("provider_object_id") != receipt.provider_object_id):
            continue
        key_hash = fingerprint({"action": receipt.action, "idempotency_key": receipt.idempotency_key})
        intent = session.scalar(select(LaunchEvidence).where(LaunchEvidence.plan_id == plan.id,
            LaunchEvidence.kind == "provider_action_intent", LaunchEvidence.detail["key_hash"].as_string() == key_hash))
        if (intent is None or intent.source != "gluons" or intent.is_test != item.is_test or intent.recorded_at > receipt.verified_at
                or intent.detail.get("action") != receipt.action or intent.detail.get("payload_hash") != receipt.payload_hash
                or intent.detail.get("connection_id") != connection.id or intent.detail.get("automation_id") != receipt.automation_id
                or intent.detail.get("context") != plan_action_context(plan, connection)):
            continue
        found[category].append({"receipt_id": receipt.id, "evidence_id": item.id, "resource_id": receipt.provider_object_id,
                                "action": receipt.action, "source": "shopify", "environment": receipt.environment,
                                "observed_at": receipt.verified_at.astimezone(UTC).isoformat(),
                                "recheck_after_seconds": int(CONNECTION_MAX_AGE.total_seconds())})
    return found


def _payment_observation(session: Session, plan: LaunchPlan, connection: Connection, now: datetime) -> dict[str, Any] | None:
    scope = "read_shopify_payments_accounts"
    if scope not in connection.granted_scopes:
        return None
    evidence = session.scalars(select(LaunchEvidence).where(LaunchEvidence.plan_id == plan.id,
        LaunchEvidence.kind == "provider_readiness", LaunchEvidence.source == "shopify").order_by(LaunchEvidence.recorded_at.desc()))
    for item in evidence:
        detail = item.detail
        observed_scopes = detail.get("observed_scopes")
        if (not now - CONNECTION_MAX_AGE <= item.recorded_at <= now or item.is_test != (connection.environment != "production")
                or detail.get("connection_id") != connection.id or detail.get("store_id") != connection.external_account_id
                or detail.get("environment") != connection.environment or detail.get("plan_version") != plan.version
                or detail.get("plan_store_context") != plan_action_context(plan, connection)):
            continue
        payments = detail.get("payments")
        if (not isinstance(observed_scopes, list) or scope not in observed_scopes or not isinstance(payments, dict)
                or payments.get("status") not in ("setup_completed", "requires_owner_action")):
            return None
        if payments.get("setup_completed") is not (payments["status"] == "setup_completed"):
            return None
        return {"provider_status": payments["status"], "source": "shopify", "environment": connection.environment,
                "observed_at": item.recorded_at.astimezone(UTC).isoformat(), "evidence_id": item.id,
                "recheck_after_seconds": int(CONNECTION_MAX_AGE.total_seconds()), "kyc_verified": False,
                "live_payments_verified": False}
    return None


def project(session: Session, plan: LaunchPlan) -> dict[str, Any]:
    """Project only facts linked to this plan and its current, checked store.

    Older provider observations do not contain sufficient plan/version linkage to
    establish current readiness. They remain available in the evidence history.
    """
    now = utcnow()
    connection_id = plan.state.get("store_connection_id")
    connection = session.get(Connection, connection_id) if connection_id else None
    binding = None
    if connection is not None:
        bindings = session.scalars(select(LaunchEvidence).where(
            LaunchEvidence.plan_id == plan.id, LaunchEvidence.kind == "store_connection",
            LaunchEvidence.source == "shopify",
        ).order_by(LaunchEvidence.recorded_at.desc()))
        binding = next((row for row in bindings if row.detail.get("connection_id") == connection.id
                        and row.detail.get("environment") == connection.environment
                        and row.is_test == (connection.environment != "production")), None)
    status, reason = "blocked", "Connect and bind an owner-confirmed Shopify store to this plan."
    if connection_id and connection is None:
        status, reason = "needs_reauth", "Reconnect the store bound to this plan."
    elif connection is not None:
        if (connection.provider != "shopify" or connection.status != "connected" or connection.disconnected_at is not None
                or not connection.credential_ref or not connection.account_confirmed_at):
            status, reason = "needs_reauth", "Reconnect and confirm the Shopify store."
        elif (not connection.last_checked_at or connection.last_checked_at > now
              or connection.last_checked_at < now - CONNECTION_MAX_AGE):
            status, reason = "needs_reauth", "Recheck the Shopify store identity; the last check is missing or older than 24 hours."
        elif (binding is None or binding.recorded_at > now or connection.account_confirmed_at > now
              or binding.recorded_at < connection.account_confirmed_at):
            status, reason = "blocked", "Bind the currently confirmed store to this plan again."
        else:
            status, reason = "verified", "The bound store identity was checked and confirmed."

    checks: dict[str, dict[str, Any]] = {
        "internal_draft": {"status": "verified", "source": "gluons", "observed_at": plan.created_at.astimezone(UTC).isoformat(),
                           "environment": "internal", "plan_version": plan.version, "next_action": None},
        "store_connection": {"status": status, "source": "shopify" if connection else None,
                             "observed_at": connection.last_checked_at.astimezone(UTC).isoformat() if connection and connection.last_checked_at else None,
                             "environment": connection.environment if connection else None, "connection_id": connection.id if connection else None,
                             "evidence_id": binding.id if binding else None, "recheck_after_seconds": int(CONNECTION_MAX_AGE.total_seconds()),
                             "next_action": None if status == "verified" else reason},
    }
    missing = {
        "catalog_drafts": "Verify all required catalog drafts against the current plan and store.",
        "payment_eligibility": "Obtain current payment-provider eligibility and owner KYC status.",
        "tax_and_shipping": "Review tax and shipping setup with appropriate evidence.",
        "test_checkout": "Complete an authorized test checkout and record its environment and readback.",
        "publication_approval": "Obtain owner approval for the exact current publication manifest.",
        "publication": "Verify publication of the approved manifest in the intended environment.",
        "operations_and_fulfillment": "Verify operational and dispatch evidence for the selected fulfillment path.",
    }
    for key, action in missing.items():
        checks[key] = {"status": "blocked", "source": None, "observed_at": None, "environment": None, "next_action": action}
    if status == "verified" and connection is not None:
        for category, receipts in _individual_receipts(session, plan, connection, now).items():
            checks[category]["recent_individual_receipts"] = receipts
        observation = _payment_observation(session, plan, connection, now)
        if observation is not None:
            checks["payment_eligibility"]["provider_observation"] = observation
    return {"plan_id": plan.id, "plan_version": plan.version, "store_live": False, "payment_verified": False,
            "status": "blocked", "checks": checks,
            "limitations": ["Historical observations without current plan, resource and version linkage do not establish launch readiness.",
                            "A test checkout or a fulfillment record does not establish production payment or physical delivery."]}
