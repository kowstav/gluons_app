"""Time-limited, owner-consented representative assistance with no elevated authority."""
from datetime import timedelta

from sqlalchemy import select

from gluons import audit
from gluons.authz import Principal, Role
from gluons.core import GluonsError, conflict, forbidden, new_id, not_found, utcnow, validation_failed
from gluons.db.models import AssistSession
from gluons.db.session import tenant_tx


def _view(row: AssistSession) -> dict:
    return {"id": row.id, "rep_user_id": row.rep_user_id, "owner_consent_by": row.owner_consent_by,
            "status": "expired" if row.expires_at <= utcnow() and row.status in ("requested", "active") else row.status,
            "checklist": row.checklist, "completion_report": row.completion_report,
            "expires_at": row.expires_at.isoformat(), "created_at": row.created_at.isoformat()}


def request_session(principal: Principal) -> dict:
    principal.require("assist:use")
    if principal.kind != "user" or principal.role != Role.FIELD_REP:
        raise forbidden("Only a signed-in field representative can request assistance.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = AssistSession(id=new_id("ast"), tenant_id=principal.tenant_id, rep_user_id=principal.user_id,
                            expires_at=utcnow() + timedelta(hours=1),
                            checklist={"diagnosis": False, "permissions_explained": False, "shadow_trial": False, "handoff": False})
        session.add(row)
        session.flush()
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="assist.requested", actor=principal,
                     resource_type="assist_session", resource_id=row.id)
        return _view(row)


def list_sessions(principal: Principal) -> list[dict]:
    principal.require("org:read")
    if principal.role not in (Role.OWNER, Role.FIELD_REP):
        raise forbidden("Assistance sessions are visible to the owner and assigned representative.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        query = select(AssistSession).order_by(AssistSession.created_at.desc()).limit(100)
        if principal.role == Role.FIELD_REP:
            query = query.where(AssistSession.rep_user_id == principal.user_id)
        return [_view(row) for row in session.scalars(query)]


def consent(principal: Principal, session_id: str) -> dict:
    principal.require("assist:consent")
    if principal.kind != "user" or not principal.session_id or principal.role != Role.OWNER:
        raise forbidden("The business owner must consent in Gluons Web.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(AssistSession, session_id, with_for_update=True)
        if row is None:
            raise not_found("Assistance session")
        if row.status != "requested" or row.expires_at <= utcnow():
            raise conflict("This assistance request is no longer available.")
        row.owner_consent_by, row.owner_consent_at, row.status = principal.user_id, utcnow(), "active"
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="assist.consented", actor=principal,
                     resource_type="assist_session", resource_id=row.id, data={"rep_user_id": row.rep_user_id})
        return _view(row)


def require_active(principal: Principal) -> None:
    if principal.role != Role.FIELD_REP:
        return
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.scalar(select(AssistSession.id).where(AssistSession.rep_user_id == principal.user_id,
                             AssistSession.status == "active", AssistSession.owner_consent_by.is_not(None), AssistSession.expires_at > utcnow()).limit(1))
        if row is None:
            raise GluonsError("ASSISTANCE_CONSENT_REQUIRED", "A business owner must consent to an active assistance session first.", status=403)


def end_session(principal: Principal, session_id: str, report: dict) -> dict:
    if set(report) - {"diagnosis", "permissions_explained", "shadow_trial", "handoff"} or any(type(v) is not bool for v in report.values()):
        raise validation_failed("Use boolean completion checks; credentials and customer records do not belong in this report.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(AssistSession, session_id, with_for_update=True)
        if row is None:
            raise not_found("Assistance session")
        if principal.kind != "user" or not (principal.role == Role.OWNER or (principal.role == Role.FIELD_REP and row.rep_user_id == principal.user_id)):
            raise forbidden("Only the owner or assigned representative can end this session.")
        if row.status not in ("requested", "active"):
            raise conflict("This assistance session already ended.")
        row.status, row.ended_at, row.completion_report = "ended", utcnow(), report
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="assist.ended", actor=principal,
                     resource_type="assist_session", resource_id=row.id, data={"checks": report})
        return _view(row)
