"""Organization deletion requests, an owner action in Gluons Web, and the purge that follows.

A request stops everything that can act and destroys credentials straight away. After the grace
period the purge erases business records. Signed history stays: the append-only tables reject
deletes by design, and the rows they point to keep their identity with their content cleared.
Each response says exactly what is gone and what is kept. Owners export first through
`data_export`.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, literal_column, select, update

from gluons import audit, policy, vault
from gluons.authz import Principal
from gluons.config import get_settings
from gluons.connections import service as connections
from gluons.core import conflict, forbidden, not_found, utcnow, validation_failed
from gluons.db.models import (
    AgentBinding,
    Approval,
    Artifact,
    Automation,
    AutomationPolicy,
    Base,
    Connection,
    Customer,
    IdentitySession,
    Invitation,
    KnowledgeDocument,
    Membership,
    Organization,
    VaultSecret,
)
from gluons.db.session import tenant_tx
from gluons.oauth import service as oauth
from gluons.runtime import lifecycle

RETAINED_TABLES = {
    "audit_events": "Immutable audit trail, kept as the record of who did what.",
    "approval_decisions": "Signed human decisions on consequential actions.",
    "run_events": "Ordered history of each run.",
    "usage_events": "Usage ledger behind charges.",
    "action_receipts": "Evidence of external side effects that already happened.",
}
TERMINAL_AUTOMATIONS = ("cancelled", "cancel_requested", "completed")


def _owner_web(principal: Principal, permission: str) -> None:
    principal.require(permission)
    # WEB_ONLY_PERMISSIONS already strip these from assistants; this stays true if that list changes.
    if principal.kind != "user":
        raise forbidden("This is available only to the owner in Gluons Web.")


def _begin_deletion(principal: Principal, confirm_name: str) -> None:
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id, with_for_update=True)
        if org is None:
            raise not_found("Organization")
        if org.deleted_at is not None:
            raise conflict("Deletion has already been requested for this business.")
        if confirm_name != org.name:
            raise validation_failed("Type the business name exactly to confirm.")
        org.emergency_stop_at = org.emergency_stop_at or utcnow()
        org.emergency_stop_by = org.emergency_stop_by or principal.actor_id
        org.version += 1
        audit.record(session, tenant_id=principal.tenant_id, category="data", event_type="organization.deletion_requested",
                     actor=principal, resource_type="organization", resource_id=principal.tenant_id)


def _disconnect_all(principal: Principal) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        rows = session.execute(select(Connection.id, Connection.provider).where(Connection.disconnected_at.is_(None))).all()
    done, manual = [], []
    for connection_id, provider in rows:
        result = connections.disconnect(principal, connection_id)
        revocation = result.get("provider_revocation", "not_supported")
        done.append({"connection_id": connection_id, "provider": provider, "provider_revocation": revocation})
        if revocation != "revoked":
            manual.append({"connection_id": connection_id, "provider": provider,
                           "action": "Remove Gluons access in the provider's own settings. Gluons no longer holds this credential."})
    return done, manual


def _revoke_assistants(principal: Principal) -> int:
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        bindings = session.execute(select(AgentBinding.id).where(AgentBinding.status == "active")).scalars().all()
    for binding_id in bindings:
        oauth.revoke_binding(principal, binding_id)
    return len(bindings)


def _fence_automations(principal: Principal) -> tuple[int, list[str]]:
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        policies = session.execute(select(AutomationPolicy).where(AutomationPolicy.status.in_(("active", "proposed")))).scalars().all()
        for row in policies:
            policy.revoke(session, principal, row.id)
        automations = session.execute(select(Automation.id, Automation.status, Automation.version)
                                      .where(Automation.status.not_in(TERMINAL_AUTOMATIONS))).all()
    fenced = []
    for automation_id, _, version in automations:
        lifecycle.cancel(principal, automation_id, version)
        fenced.append(automation_id)
    return len(policies), fenced


def _mark_data(principal: Principal) -> dict[str, int]:
    """Marks rather than erases. Final: closes access last, so a failed request can be retried."""
    now = utcnow()
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        approvals = session.execute(update(Approval).where(Approval.status == "pending").values(status="cancelled", version=Approval.version + 1)).rowcount
        customers = session.execute(update(Customer).where(Customer.suppressed.is_(False)).values(suppressed=True)).rowcount
        documents = session.execute(update(KnowledgeDocument).where(KnowledgeDocument.status != "deletion_pending").values(status="deletion_pending")).rowcount
        artifacts = session.execute(update(Artifact).values(retention_class="deletion_pending")).rowcount
        invitations = session.execute(update(Invitation).where(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None)).values(revoked_at=now)).rowcount
        secrets = session.execute(select(VaultSecret.id).where(VaultSecret.destroyed_at.is_(None))).scalars().all()
        for secret_id in secrets:
            vault.destroy(session, principal.tenant_id, secret_id)
        members = session.execute(update(Membership).where(Membership.removed_at.is_(None)).values(removed_at=now)).rowcount
        session.execute(update(IdentitySession).where(IdentitySession.active_tenant_id == principal.tenant_id).values(active_tenant_id=None))
        session.get(Organization, principal.tenant_id, with_for_update=True).deleted_at = now
    return {"approvals_cancelled": approvals, "customers_suppressed": customers, "documents_marked": documents,
            "artifacts_marked": artifacts, "invitations_revoked": invitations, "credentials_destroyed": len(secrets),
            "members_removed": members}


def _retained_counts(tenant_id: str) -> list[dict[str, Any]]:
    with tenant_tx(tenant_id) as session:
        return [{"table": name, "rows": session.execute(select(func.count()).select_from(Base.metadata.tables[name])).scalar_one(),
                 "reason": reason} for name, reason in RETAINED_TABLES.items()]


def request_deletion(principal: Principal, *, confirm_name: str, export_acknowledged: bool) -> dict[str, Any]:
    """Each step is safe to repeat, so a failure part way can be retried from the start."""
    _owner_web(principal, "org:delete")
    if not export_acknowledged:
        raise validation_failed("Export your data first, then acknowledge that deletion closes access to this business.")
    _begin_deletion(principal, confirm_name)
    disconnected, manual = _disconnect_all(principal)
    assistants = _revoke_assistants(principal)
    policies, automations = _fence_automations(principal)
    marked = _mark_data(principal)
    retained = _retained_counts(principal.tenant_id)
    report = {
        "status": "deletion_requested", "organization_id": principal.tenant_id, "requested_at": utcnow().isoformat(),
        "erasure_completed": False,
        "completed_now": {"emergency_stop_engaged": True, "connections_disconnected": disconnected, "assistants_revoked": assistants,
                          "policies_revoked": policies, "automations_fenced": automations, **marked},
        "needs_manual_action": manual, "retained": retained,
        "not_done": [f"Business records are marked and access is closed now. They are erased {get_settings().deletion_grace_days} days after this request.",
                     "There is no self-service way to reverse this request.",
                     "Actions already completed at providers are not undone, and sent messages cannot be recalled."],
        "retention_notice": "Audit and transaction history is kept as required for accountability. Retention periods need legal review per jurisdiction.",
    }
    with tenant_tx(principal.tenant_id) as session:
        audit.record(session, tenant_id=principal.tenant_id, category="data", event_type="organization.deletion_fenced", actor=principal,
                     resource_type="organization", resource_id=principal.tenant_id,
                     data={"completed_now": report["completed_now"], "needs_manual_action": manual, "erasure_completed": False})
    return report


# Leaf business content, deleted children first. Nothing kept points at these rows.
PURGED = ("channel_messages", "channel_threads", "calls", "orders", "customers", "catalog_items", "knowledge_documents", "artifacts",
          "supplier_quotes", "suppliers", "launch_evidence", "launch_plans", "trial_measurements", "trials", "step_runs",
          "notifications", "incidents", "budget_holds", "api_idempotency", "provider_event_subscriptions", "connection_sessions",
          "phone_routes", "connections", "oauth_tokens", "agent_bindings", "vault_secrets", "invitations", "assist_sessions")
# Kept history points at these rows, so they stay with their content cleared.
SCRUBBED = {"runs": ("input", "state", "outcome", "error"),
            "approvals": ("summary", "records_before", "records_after_preview", "source_evidence", "resolution"),
            "automations": ("config", "trigger", "model_policy"),
            "plans": ("goal", "business_context", "graph", "required_connections", "side_effects", "cost_estimate"),
            "organizations": ("business_facts", "business_passport")}
# Workers may carry rows for any tenant; the app role cannot delete from them.
SYSTEM_PURGED = ("jobs", "outbox", "webhook_inbox")


def _cleared(column: Any) -> Any:
    if column.server_default is not None:
        return literal_column("DEFAULT")
    return None if column.nullable else "[erased]"


def purge(tenant_id: str) -> dict[str, int]:
    """Erases one deleted business. Safe to repeat: a finished purge returns nothing."""
    from gluons.db.session import system_tx
    tables = Base.metadata.tables
    counts: dict[str, int] = {}
    with tenant_tx(tenant_id) as session:
        organization = session.get(Organization, tenant_id, with_for_update=True)
        if organization is None or organization.deleted_at is None or organization.purged_at is not None:
            return {}
        for name in PURGED:
            table = tables[name]
            counts[name] = session.execute(table.delete().where(table.c.tenant_id == tenant_id)).rowcount
        for name, columns in SCRUBBED.items():
            table = tables[name]
            key = table.c.id if name == "organizations" else table.c.tenant_id
            counts[f"{name}_cleared"] = session.execute(table.update().where(key == tenant_id).values(
                {column: _cleared(table.c[column]) for column in columns})).rowcount
        organization.purged_at = utcnow()
    with system_tx() as session:
        for name in SYSTEM_PURGED:
            table = tables[name]
            counts[name] = session.execute(table.delete().where(table.c.tenant_id == tenant_id)).rowcount
    with tenant_tx(tenant_id) as session:
        audit.record(session, tenant_id=tenant_id, category="data", event_type="organization.purged", actor="deletion_purge",
                     resource_type="organization", resource_id=tenant_id, data={"counts": counts, "kept": sorted(RETAINED_TABLES)})
    return counts


def purge_due() -> int:
    from gluons.db.session import system_tx
    cutoff = utcnow() - timedelta(days=get_settings().deletion_grace_days)
    with system_tx() as session:
        due = session.scalars(select(Organization.id).where(Organization.deleted_at.is_not(None), Organization.deleted_at < cutoff,
                                                            Organization.purged_at.is_(None))).all()
    for tenant_id in due:
        purge(tenant_id)
    return len(due)
