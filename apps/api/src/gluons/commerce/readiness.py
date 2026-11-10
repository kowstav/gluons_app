"""Read-only, fail-closed view of a launch plan's current evidence."""
from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gluons.core import utcnow
from gluons.db.models import Connection, LaunchEvidence, LaunchPlan

CONNECTION_MAX_AGE = timedelta(hours=24)


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
    return {"plan_id": plan.id, "plan_version": plan.version, "store_live": False, "payment_verified": False,
            "status": "blocked", "checks": checks,
            "limitations": ["Historical observations without current plan, resource and version linkage do not establish launch readiness.",
                            "A test checkout or a fulfillment record does not establish production payment or physical delivery."]}
