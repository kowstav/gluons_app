"""Tenant-scoped business notes; retrieval is data and never grants tool authority."""
from __future__ import annotations

from sqlalchemy import or_, select

from gluons import audit
from gluons.authz import Principal
from gluons.core import new_id, validation_failed
from gluons.db.models import KnowledgeDocument
from gluons.db.session import tenant_tx


def _serialize(row: KnowledgeDocument) -> dict:
    return {"id": row.id, "title": row.title, "body": row.body, "kind": row.kind,
            "source": row.source, "version": row.version, "created_at": row.created_at.isoformat(),
            "trust": "untrusted_business_data"}


def add_document(principal: Principal, *, title: str, body: str, kind: str = "note", source: str | None = None) -> dict:
    principal.require("knowledge:write")
    if not title.strip() or not body.strip() or len(title) > 300 or len(body) > 100_000 or len(kind) > 40:
        raise validation_failed("Provide a title and document text within the size limits.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = KnowledgeDocument(id=new_id("knw"), tenant_id=principal.tenant_id, title=title.strip(),
                                body=body, kind=kind, source=source, created_by=principal.actor_id)
        session.add(row)
        session.flush()
        audit.record(session, tenant_id=principal.tenant_id, category="data", event_type="knowledge.created",
                     actor=principal, resource_type="knowledge_document", resource_id=row.id, data={"kind": kind})
        return _serialize(row)


def list_documents(principal: Principal) -> list[dict]:
    principal.require("knowledge:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return [_serialize(row) for row in session.execute(select(KnowledgeDocument).where(
            KnowledgeDocument.status == "active").order_by(KnowledgeDocument.created_at.desc()).limit(200)).scalars()]


def search(tenant_id: str, query: str, *, limit: int = 5) -> list[dict]:
    """Internal worker retrieval. Callers derive tenant_id from a verified task or principal."""
    terms = [t for t in query.split()[:8] if len(t) >= 2]
    if not terms:
        return []
    with tenant_tx(tenant_id) as session:
        matches = [KnowledgeDocument.body.icontains(t, autoescape=True) for t in terms]
        rows = session.execute(select(KnowledgeDocument).where(KnowledgeDocument.status == "active", or_(*matches))
                               .order_by(KnowledgeDocument.updated_at.desc()).limit(max(1, min(limit, 20)))).scalars()
        return [_serialize(row) for row in rows]
