"""Read-only integrity checks for an isolated PostgreSQL recovery target."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from gluons import audit
from gluons.db.models import TENANT_TABLES, ActionReceipt, AuditEvent, AutomationPolicy, Organization
from gluons.db.session import _bind_context

REQUIRED_RLS = frozenset(TENANT_TABLES) | {"organizations", "jobs", "outbox", "webhook_inbox"}


@dataclass(frozen=True)
class RestoreWitness:
    database: str
    tenant_id: str
    other_tenant_id: str
    audit_head: str
    receipt_id: str
    receipt_environment: str
    server_address: str | None = None


def verify_restore(url: str, witness: RestoreWitness) -> dict[str, bool]:
    """Check expected records using the same row security as the application."""
    if witness.tenant_id == witness.other_tenant_id:
        raise ValueError("The isolation check requires two distinct restored tenants.")
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 10})
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            session.execute(text("SET LOCAL statement_timeout = '60s'"))
            session.execute(text("SET LOCAL search_path TO public"))
            role = session.execute(text("""
                SELECT current_database() AS database, host(inet_server_addr()) AS server_address,
                       current_user AS role, rolsuper, rolbypassrls,
                       pg_has_role('gluons_system', 'MEMBER') AS system_role,
                       current_setting('transaction_read_only') = 'on' AS read_only
                FROM pg_catalog.pg_roles WHERE rolname = current_user
            """)).one()
            checks = {
                "expected_target": role.database == witness.database and (
                    witness.server_address is None or role.server_address == witness.server_address),
                "read_only_app_role": role.role == "gluons_app" and role.read_only
                and not (role.rolsuper or role.rolbypassrls or role.system_role),
            }
            if not all(checks.values()):
                return checks

            secured = session.scalars(text("""
                SELECT relname FROM pg_catalog.pg_class
                WHERE relnamespace = 'public'::regnamespace AND relrowsecurity AND relforcerowsecurity
            """)).all()
            checks["forced_tenant_rls"] = REQUIRED_RLS.issubset(secured)
            if not checks["forced_tenant_rls"]:
                return checks

            receipt_by_id = select(ActionReceipt.id).where(ActionReceipt.id == witness.receipt_id)
            _bind_context(session, None, None)
            checks["unscoped_receipt_hidden"] = session.scalar(receipt_by_id) is None
            _bind_context(session, witness.other_tenant_id, None)
            checks["cross_tenant_receipt_hidden"] = (
                session.scalar(select(Organization.id).where(Organization.id == witness.other_tenant_id)) is not None
                and session.scalar(receipt_by_id) is None
            )
            _bind_context(session, witness.tenant_id, None)
            head = session.scalar(select(AuditEvent.hash).where(AuditEvent.tenant_id == witness.tenant_id)
                                  .order_by(AuditEvent.seq.desc()).limit(1))
            chain = audit.verify_chain(session, witness.tenant_id)
            checks["audit_chain_matches"] = bool(chain["valid"] and chain["checked"] > 0 and head == witness.audit_head)
            receipt = session.get(ActionReceipt, witness.receipt_id)
            checks["verified_receipt_retained"] = bool(
                receipt and receipt.environment == witness.receipt_environment and receipt.verified
                and receipt.verified_at and receipt.status == "succeeded" and receipt.provider_object_id
                and receipt.readback is not None
            )
            policy = session.get(AutomationPolicy, receipt.policy_id) if receipt and receipt.policy_id else None
            checks["policy_record_retained"] = bool(policy and policy.signature and policy.automation_id == receipt.automation_id)
            return checks
    finally:
        engine.dispose()
