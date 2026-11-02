"""Request authentication. Browser sessions use an HttpOnly cookie plus a CSRF header."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy import select

from gluons import identity
from gluons.authz import Principal
from gluons.config import get_settings
from gluons.core import GluonsError, auth_required, conflict, constant_time_equals, fingerprint, new_id, token_hash
from gluons.db.models import ApiIdempotency
from gluons.db.session import tenant_tx
from gluons.identity import SessionView

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def session_token(request: Request) -> str | None:
    return request.cookies.get(get_settings().session_cookie_name)


def current_session(request: Request) -> SessionView:
    token = session_token(request)
    if not token:
        raise auth_required()
    view = identity.resolve_session(token)
    if request.method not in SAFE_METHODS:
        header = request.headers.get("x-csrf-token", "")
        if not header or not constant_time_equals(token_hash(header), view.csrf_token_hash):
            raise GluonsError("CSRF_FAILED", "Reload the page and try again.", status=403)
    return view


SessionDep = Annotated[SessionView, Depends(current_session)]


def current_principal(view: SessionDep) -> Principal:
    if view.mfa_pending:
        raise GluonsError("MFA_REQUIRED", "Enter your verification code.", status=401)
    if view.active_tenant_id is None:
        raise GluonsError("ORG_REQUIRED", "Choose a business first.", status=409)
    return identity.principal_for_user(view.user_id, view.active_tenant_id, view.session_id)


PrincipalDep = Annotated[Principal, Depends(current_principal)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]
_ = Cookie  # re-exported for route modules


def idempotent(principal: Principal, key: str, request: Any, fn: Callable[[], Any]) -> Any:
    """Replays the stored response for a retried write; a reused key with a new body fails."""
    request_hash = fingerprint(request)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        existing = session.execute(select(ApiIdempotency).where(ApiIdempotency.key == key)).scalar_one_or_none()
        if existing is not None:
            if existing.request_hash != request_hash:
                raise conflict("This Idempotency-Key was already used for a different request.")
            return existing.response
    response = fn()
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        session.add(
            ApiIdempotency(
                id=new_id("idm"),
                tenant_id=principal.tenant_id,
                key=key,
                request_hash=request_hash,
                status_code=200,
                response=response,
            )
        )
    return response
