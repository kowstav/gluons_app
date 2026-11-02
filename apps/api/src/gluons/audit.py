"""Immutable, hash-chained audit trail. One chain per tenant.

`record` must be called inside the transaction whose change it describes, so a state
change and its audit entry commit or fail together.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from gluons.authz import Principal
from gluons.core import canonical_json, new_id, sha256_hex, utcnow
from gluons.db.models import AuditEvent

GENESIS = "0" * 64
_SECRET_MARKERS = ("token", "secret", "password", "authorization", "api_key", "apikey", "cookie", "verifier")


def redact(value: Any) -> Any:
    """Audit payloads must never carry credentials, whatever a caller passes in."""
    if isinstance(value, dict):
        return {
            k: "[redacted]" if any(m in str(k).lower() for m in _SECRET_MARKERS) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def record(
    session: Session,
    *,
    tenant_id: str,
    category: str,
    event_type: str,
    actor: Principal | str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    decision: str | None = None,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    # Serialises this tenant's chain for the rest of the transaction.
    session.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"audit:{tenant_id}"})
    last = session.execute(
        select(AuditEvent.seq, AuditEvent.hash)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    ).first()
    seq = (last.seq + 1) if last else 1
    prev_hash = last.hash if last else GENESIS
    if isinstance(actor, Principal):
        actor_kind, actor_id, binding_id = actor.kind, actor.actor_id, actor.binding_id
    else:
        actor_kind, actor_id, binding_id = "system", actor, None
    created_at = utcnow()
    clean = redact(data or {})
    body = canonical_json(
        {
            "tenant_id": tenant_id,
            "seq": seq,
            "category": category,
            "event_type": event_type,
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "binding_id": binding_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "decision": decision,
            "data": clean,
            "created_at": created_at,
        }
    )
    event = AuditEvent(
        id=new_id("aud"),
        tenant_id=tenant_id,
        seq=seq,
        category=category,
        event_type=event_type,
        actor_kind=actor_kind,
        actor_id=actor_id,
        binding_id=binding_id,
        resource_type=resource_type,
        resource_id=resource_id,
        decision=decision,
        data=clean,
        prev_hash=prev_hash,
        hash=sha256_hex(prev_hash + body),
        created_at=created_at,
    )
    session.add(event)
    session.flush()
    return event


def verify_chain(session: Session, tenant_id: str) -> dict[str, Any]:
    """Recomputes the chain. Reports the first entry that does not match."""
    events = session.execute(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
    ).scalars()
    prev = GENESIS
    count = 0
    for event in events:
        body = canonical_json(
            {
                "tenant_id": event.tenant_id,
                "seq": event.seq,
                "category": event.category,
                "event_type": event.event_type,
                "actor_kind": event.actor_kind,
                "actor_id": event.actor_id,
                "binding_id": event.binding_id,
                "resource_type": event.resource_type,
                "resource_id": event.resource_id,
                "decision": event.decision,
                "data": event.data,
                "created_at": event.created_at,
            }
        )
        if event.prev_hash != prev or event.hash != sha256_hex(prev + body):
            return {"valid": False, "broken_at_seq": event.seq, "checked": count}
        prev = event.hash
        count += 1
    return {"valid": True, "broken_at_seq": None, "checked": count}
