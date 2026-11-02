"""Organization data export and deletion requests. Both are owner actions in Gluons Web.

An export is complete for what it lists and says what it leaves out. A deletion request stops
everything that can act and destroys credentials straight away. It does not erase records:
no purge job exists yet, and the append-only history tables reject deletes by design. The
response says exactly that, so a reader is never told data is gone while it is still stored.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select, update

from gluons import audit, policy, vault
from gluons.authz import Principal
from gluons.connections import service as connections
from gluons.core import canonical_json, conflict, fingerprint, forbidden, not_found, utcnow, validation_failed
from gluons.db.models import (
    TENANT_TABLES,
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
    User,
    VaultSecret,
)
from gluons.db.session import tenant_tx
from gluons.oauth import service as oauth
from gluons.runtime import lifecycle

MAX_ROWS_PER_TABLE = 20_000
EXCLUDED_TABLES = {
    "vault_secrets": "Encrypted provider credentials. Never exported.",
    "oauth_tokens": "Assistant bearer tokens, stored only as hashes. Never exported.",
    "connection_sessions": "Short-lived onboarding state holding OAuth state and PKCE references.",
    "api_idempotency": "Cached API responses kept only to replay retried requests.",
}
CREDENTIAL_COLUMNS = frozenset({"token_hash", "credential_ref", "ciphertext", "wrapped_dek", "code_hash", "setup_handle_hash",
                                "state_hash", "pkce_verifier_ref", "nonce_hash", "client_secret_hash"})
NOT_INCLUDED = ("Artifact file contents: rows hold metadata and a storage reference only.",
                "Queue, outbox and webhook inbox records, which are operational rather than business data.",
                "Data held by connected providers, which stays with those providers.")
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


def _plain(row: Any, columns: list[Any]) -> dict[str, Any]:
    values = {column.name: getattr(row, column.name) for column in columns}
    return audit.redact(json.loads(canonical_json(values)))


def _export_table(session: Any, name: str) -> tuple[list[dict[str, Any]], int]:
    table = Base.metadata.tables[name]
    columns = [column for column in table.columns if column.name not in CREDENTIAL_COLUMNS]
    total = session.execute(select(func.count()).select_from(table)).scalar_one()
    order = [table.c[key] for key in ("created_at", "id") if key in table.c][:2]
    rows = session.execute(select(*columns).order_by(*order).limit(MAX_ROWS_PER_TABLE)).all()
    return [_plain(row, columns) for row in rows], total


def export_organization(principal: Principal) -> dict[str, Any]:
    """Row level security supplies the tenant filter, so this cannot read another business."""
    _owner_web(principal, "data:export")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        org = session.get(Organization, principal.tenant_id)
        if org is None:
            raise not_found("Organization")
        tables: dict[str, list[dict[str, Any]]] = {}
        counts: dict[str, int] = {}
        truncated: dict[str, int] = {}
        for name in TENANT_TABLES:
            if name in EXCLUDED_TABLES:
                continue
            tables[name], counts[name] = _export_table(session, name)
            if counts[name] > len(tables[name]):
                truncated[name] = counts[name]
        members = session.execute(select(Membership.user_id, User.email, User.display_name, Membership.role)
                                  .join(User, User.id == Membership.user_id).where(Membership.removed_at.is_(None))).all()
        chain = audit.verify_chain(session, principal.tenant_id)
        audit.record(session, tenant_id=principal.tenant_id, category="data", event_type="data.exported", actor=principal,
                     resource_type="organization", resource_id=principal.tenant_id,
                     data={"tables": len(tables), "rows": sum(counts.values()), "truncated": sorted(truncated)})
        organization = _plain(org, list(Organization.__table__.columns))
    return {"format": "gluons.organization_export.v1", "exported_at": utcnow().isoformat(), "exported_by": principal.actor_id,
            "organization": organization,
            "members": [{"user_id": m.user_id, "email": m.email, "display_name": m.display_name, "role": m.role} for m in members],
            "tables": tables, "row_counts": counts, "truncated": truncated, "excluded_tables": EXCLUDED_TABLES,
            "removed_columns": sorted(CREDENTIAL_COLUMNS), "not_included": list(NOT_INCLUDED), "audit_chain": chain,
            "content_sha256": fingerprint(tables)}


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
        "not_done": ["Business records are marked and access is closed, but the rows are still stored. No purge job exists yet.",
                     "There is no self-service way to reverse this request.",
                     "Actions already completed at providers are not undone, and sent messages cannot be recalled."],
        "retention_notice": "Audit and transaction history is kept as required for accountability. Retention periods need legal review per jurisdiction.",
    }
    with tenant_tx(principal.tenant_id) as session:
        audit.record(session, tenant_id=principal.tenant_id, category="data", event_type="organization.deletion_fenced", actor=principal,
                     resource_type="organization", resource_id=principal.tenant_id,
                     data={"completed_now": report["completed_now"], "needs_manual_action": manual, "erasure_completed": False})
    return report
