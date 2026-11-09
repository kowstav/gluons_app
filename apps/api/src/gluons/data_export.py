"""Owner-only structured exports using explicit column maps and tenant RLS.

This is deliberately not ORM object dumping. Provider credentials, OAuth/session
state and arbitrary connector/runtime JSON never belong in a downloadable export.
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, tuple_

from gluons import audit
from gluons.authz import Principal, Role
from gluons.config import get_settings
from gluons.core import GluonsError, canonical_json, constant_time_equals, forbidden, hmac_sha256_hex, utcnow, validation_failed
from gluons.db import models as m
from gluons.db.session import tenant_tx

# Dataset -> (model, ordered timestamp, explicitly exported columns). No global
# identity table, credential/session table, or arbitrary metadata is reachable.
DATASETS: dict[str, tuple[type, str | None, tuple[str, ...]]] = {
    "organization": (m.Organization, "created_at", ("id", "name", "legal_name", "industry", "country", "timezone", "business_passport", "passport_version", "passport_consents", "monthly_budget_usd", "created_at", "version")),
    "setup_sessions": (m.Organization, "created_at", ("id", "setup_sessions")),
    "memberships": (m.Membership, "created_at", ("id", "user_id", "role", "approval_tier_limit", "created_at", "removed_at")),
    "connections": (m.Connection, "created_at", ("id", "provider", "environment", "account_display", "status", "account_confirmed_at", "last_checked_at", "created_at", "disconnected_at", "version")),
    "assistants": (m.AgentBinding, "created_at", ("id", "user_id", "display_name", "host_kind", "status", "created_at", "last_used_at", "revoked_at")),
    "automations": (m.Automation, "created_at", ("id", "plan_id", "skill_id", "skill_version", "name", "mode", "status", "risk_tier", "monthly_budget_usd", "per_action_limit_usd", "current_policy_id", "created_by", "created_via", "created_at", "updated_at", "version")),
    "runs": (m.Run, "created_at", ("id", "automation_id", "skill_id", "skill_version", "policy_id", "mode", "status", "source", "cost_usd", "created_at", "started_at", "finished_at", "version")),
    "steps": (m.StepRun, "started_at", ("id", "run_id", "node_id", "primitive", "status", "attempts", "model_identifier", "started_at", "finished_at")),
    "approvals": (m.Approval, "created_at", ("id", "kind", "run_id", "automation_id", "action", "risk_tier", "payload_hash", "estimated_cost_usd", "amount_minor", "required_role", "required_approvals", "status", "expires_at", "decided_at", "consumed_at", "created_at", "version")),
    "approval_decisions": (m.ApprovalDecision, "decided_at", ("id", "approval_id", "user_id", "role", "decision", "via", "decided_at")),
    "action_authorizations": (m.ActionAuthorization, "decided_at", ("id", "run_id", "automation_id", "action", "mode", "outcome", "policy_id", "policy_version", "approval_id", "payload_hash", "connection_id", "amount_minor", "decided_at")),
    "action_receipts": (m.ActionReceipt, "created_at", ("id", "run_id", "automation_id", "skill_id", "action", "mode", "environment", "provider", "status", "verified", "verified_at", "payload_hash", "approval_id", "policy_id", "amount_minor", "irreversible", "cost_usd", "latency_ms", "attempts", "created_at", "updated_at")),
    "trials": (m.Trial, "created_at", ("id", "automation_id", "mode", "status", "budget_usd", "expires_at", "created_at", "completed_at")),
    "trial_measurements": (m.TrialMeasurement, "recorded_at", ("id", "trial_id", "run_id", "metric", "value", "unit", "basis", "recorded_at")),
    "usage": (m.UsageEvent, "occurred_at", ("id", "automation_id", "run_id", "skill_id", "category", "provider", "meter", "quantity", "unit_price_usd", "cost_usd", "cost_basis", "price_version", "latency_ms", "occurred_at")),
    "artifacts": (m.Artifact, "created_at", ("id", "run_id", "kind", "filename", "content_type", "size_bytes", "sha256", "retention_class", "created_at")),
    "knowledge": (m.KnowledgeDocument, "created_at", ("id", "title", "kind", "status", "created_by", "created_at", "updated_at", "version")),
    "products": (m.CatalogItem, None, ("id", "sku", "name", "unit", "unit_price_minor", "currency", "in_stock", "source", "last_synced_at")),
    "customers": (m.Customer, "created_at", ("id", "name", "emails", "phones", "suppressed", "source", "created_at")),
    "orders": (m.Order, "created_at", ("id", "run_id", "channel", "customer_id", "status", "total_minor", "currency", "receipt_id", "is_test", "created_at", "updated_at")),
    "messages": (m.ChannelMessage, "created_at", ("id", "thread_id", "channel", "direction", "sender", "recipient", "status", "classification", "created_at")),
    "calls": (m.Call, "started_at", ("id", "phone_route_id", "provider", "conversation_id", "caller", "status", "duration_seconds", "cost_usd", "run_id", "is_test", "started_at", "ended_at")),
    "launch_plans": (m.LaunchPlan, "created_at", ("id", "country", "product_category", "budget_usd", "fulfillment_model", "stage", "branch_status", "created_by", "created_via", "created_at", "updated_at", "version")),
    "suppliers": (m.Supplier, "created_at", ("id", "plan_id", "name", "contact_email", "status", "evidence_date", "first_contact_approved_at", "created_at")),
    "supplier_quotes": (m.SupplierQuote, "created_at", ("id", "supplier_id", "product", "unit_cost_minor", "currency", "moq", "lead_time_days", "shipping_minor", "duties_minor", "status", "created_at")),
    "audit": (m.AuditEvent, "created_at", ("id", "seq", "category", "event_type", "actor_kind", "actor_id", "resource_type", "resource_id", "decision", "prev_hash", "hash", "created_at")),
}
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 512 * 1024
LIMITATIONS = [
    "Structured allowlisted records only; credentials, authentication sessions, OAuth state, signatures, internal storage locators and arbitrary metadata are excluded. Setup sessions are exported as sanitized summaries.",
    "Raw message/document/transcript bodies, order-line JSON, runtime inputs/outputs and provider responses are excluded; retrieve separately through authorized retention-aware workflows.",
    "The cursor fixes a creation-time cutoff where recorded, not a cross-request database snapshot; records may change during pagination. Catalog rows have no creation timestamp and use stable ID ordering.",
    "No data or account is deleted by an export.",
]


def _owner(principal: Principal) -> None:
    principal.require("data:export")
    if principal.kind != "user" or principal.role != Role.OWNER or not principal.session_id or not principal.user_id:
        raise forbidden("A business owner must request data exports from an authenticated Gluons Web session.")


def manifest(principal: Principal) -> dict:
    _owner(principal)
    return {"format": "gluons.structured-export.v1", "tenant_id": principal.tenant_id, "max_page_size": 1000,
            "datasets": [{"name": name, "columns": list(columns)} for name, (_, _, columns) in DATASETS.items()],
            "limitations": LIMITATIONS}


def _seal_cursor(claims: dict) -> str:
    body = base64.urlsafe_b64encode(canonical_json(claims).encode()).decode().rstrip("=")
    settings = get_settings()
    signature = hmac_sha256_hex(settings.key_bytes(settings.signing_key_b64), "data-export-v1:" + body)
    return f"{body}.{signature}"


def _open_cursor(raw: str, principal: Principal, dataset: str) -> dict:
    try:
        if not isinstance(raw, str) or len(raw) > 2048:
            raise ValueError
        body, signature = raw.split(".")
        settings = get_settings()
        expected = hmac_sha256_hex(settings.key_bytes(settings.signing_key_b64), "data-export-v1:" + body)
        if not constant_time_equals(expected, signature):
            raise ValueError
        claims = json.loads(base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True))
        if (claims.get("tenant_id") != principal.tenant_id or claims.get("user_id") != principal.user_id or claims.get("dataset") != dataset
                or datetime.fromisoformat(claims["expires_at"]) <= utcnow()):
            raise ValueError
        if not isinstance(claims.get("last_id"), str) or len(claims["last_id"]) > 40:
            raise ValueError
        for key in ("as_of", "after"):
            parsed = datetime.fromisoformat(claims[key])
            if parsed.tzinfo is None:
                raise ValueError
        return claims
    except (ValueError, TypeError, KeyError, AttributeError, binascii.Error):
        raise GluonsError("EXPORT_CURSOR_INVALID", "This export cursor is invalid, expired, or belongs to another owner, business or dataset.", status=400) from None


def _value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_value(item) for item in value]
    return value


def export_page(principal: Principal, dataset: str, *, cursor: str | None = None, limit: int = 200) -> dict:
    _owner(principal)
    if dataset not in DATASETS:
        raise validation_failed("Choose a dataset from the export manifest.")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise validation_failed("Export page size must be between 1 and 1000.")
    model, timestamp_name, columns = DATASETS[dataset]
    now = utcnow()
    claims = _open_cursor(cursor, principal, dataset) if cursor else {
        "tenant_id": principal.tenant_id, "user_id": principal.user_id, "dataset": dataset,
        "as_of": now.isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    # Product catalog records may have never been synchronized. A fixed epoch
    # sorts these manual records deterministically before timed syncs.
    from sqlalchemy import func, literal
    from sqlalchemy.types import DateTime
    epoch = literal(datetime(1970, 1, 1, tzinfo=UTC), type_=DateTime(timezone=True))
    timestamp = func.coalesce(getattr(model, timestamp_name), epoch) if timestamp_name else epoch
    query = select(*(getattr(model, column) for column in columns), timestamp.label("_sort_timestamp"))
    # RLS is still mandatory, but organizations/memberships also have deliberate
    # self-membership visibility for business selection. Explicitly narrow those
    # authorized views so a selected-business export never mixes owner businesses.
    tenant_column = model.id if model is m.Organization else model.tenant_id
    query = query.where(tenant_column == principal.tenant_id)
    query = query.where(timestamp <= datetime.fromisoformat(claims["as_of"]))
    if cursor:
        query = query.where(tuple_(timestamp, model.id) > tuple_(datetime.fromisoformat(claims["after"]), claims["last_id"]))
    query = query.order_by(timestamp, model.id).limit(limit + 1)
    records, emitted, page_bytes = [], [], 0
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        rows = session.execute(query).mappings().all()
        for row in rows[:limit]:
            record = {column: _value(row[column]) for column in columns}
            if dataset == "organization":
                record["passport_consents"] = [{k: v for k, v in consent.items() if k != "preview_hash"}
                                               for consent in row["passport_consents"]]
            if dataset == "setup_sessions":
                keys = ("id", "intent", "launch_plan_id", "status", "version", "created_at", "expires_at", "created_by", "connection_session_id")
                record["setup_sessions"] = [{key: item.get(key) for key in keys} for item in row["setup_sessions"].values()]
            record_bytes = len(canonical_json(record).encode())
            if record_bytes > MAX_RECORD_BYTES:
                raise GluonsError("EXPORT_RECORD_TOO_LARGE", "A structured record exceeds the export safety limit; request administrator-assisted review.", status=422,
                                  details={"dataset": dataset, "record_id": row["id"]})
            if records and page_bytes + record_bytes > MAX_PAGE_BYTES:
                break
            records.append(record)
            emitted.append(row)
            page_bytes += record_bytes
        next_cursor = None
        if emitted and len(rows) > len(emitted):
            last = emitted[-1]
            next_cursor = _seal_cursor({**claims, "after": _value(last["_sort_timestamp"]), "last_id": last["id"]})
        anchor = session.execute(select(m.AuditEvent.seq, m.AuditEvent.hash, m.AuditEvent.created_at)
                                 .where(m.AuditEvent.tenant_id == principal.tenant_id, m.AuditEvent.created_at <= datetime.fromisoformat(claims["as_of"]))
                                 .order_by(m.AuditEvent.seq.desc()).limit(1)).mappings().first()
        audit.record(session, tenant_id=principal.tenant_id, category="data", event_type="data.export_page",
                     actor=principal, resource_type="data_export", resource_id=dataset,
                     data={"dataset": dataset, "record_count": len(records), "has_next_page": next_cursor is not None})
    return {"format": "gluons.structured-export.v1", "tenant_id": principal.tenant_id, "dataset": dataset,
            "exported_at": now.isoformat(), "as_of": claims["as_of"], "record_count": len(records), "records": records,
            "next_cursor": next_cursor, "audit_anchor": {key: _value(value) for key, value in anchor.items()} if anchor else None,
            "evidence_notice": "Receipt environment, verification timestamps and cost/measurement bases retain their stored labels; exports create no new verification evidence.",
            "limitations": LIMITATIONS}
