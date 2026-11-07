"""Sign-in, recovery and session management. Never a chat flow."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, EmailStr, Field

from gluons import identity
from gluons.api.deps import SessionDep, session_token
from gluons.config import get_settings
from gluons.ratelimit import limited

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=300)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class MfaBody(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class ResetRequestBody(BaseModel):
    email: EmailStr


class ResetConfirmBody(BaseModel):
    token: str = Field(min_length=10, max_length=300)
    new_password: str = Field(min_length=12, max_length=200)


def _set_cookies(response: Response, issued: identity.IssuedSession) -> None:
    settings = get_settings()
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        settings.session_cookie_name, issued.token, max_age=max_age, httponly=True,
        samesite="lax", secure=settings.hosted, path="/",
    )
    # Readable by the app, which echoes it in X-CSRF-Token. Another site cannot read it.
    response.set_cookie(
        "gluons_csrf", issued.csrf_token, max_age=max_age, httponly=False,
        samesite="lax", secure=settings.hosted, path="/",
    )


@router.post("/register", status_code=202)
def register(body: RegisterBody, _: None = limited("register")) -> dict[str, str]:
    identity.register(body.email, body.password, body.display_name)
    return {"status": "check_email"}


@router.post("/verify-email")
def verify_email(body: TokenBody, _: None = limited("verify_email")) -> dict[str, str]:
    identity.verify_email(body.token)
    return {"status": "verified"}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, _: None = limited("login")) -> dict[str, Any]:
    issued = identity.login(
        body.email,
        body.password,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_cookies(response, issued)
    return {"status": "mfa_required" if issued.mfa_required else "signed_in", "csrf_token": issued.csrf_token}


@router.post("/mfa/verify")
def mfa_verify(body: MfaBody, request: Request, _: None = limited("mfa")) -> dict[str, str]:
    identity.verify_mfa(session_token(request) or "", body.code)
    return {"status": "signed_in"}


@router.post("/logout")
def logout(request: Request, response: Response, view: SessionDep) -> dict[str, str]:
    identity.logout(session_token(request) or "")
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    response.delete_cookie("gluons_csrf", path="/")
    return {"status": "signed_out"}


@router.get("/session")
def session(view: SessionDep) -> dict[str, Any]:
    return {
        "user": {"id": view.user_id, "email": view.email, "display_name": view.display_name},
        "active_organization_id": view.active_tenant_id,
        "organizations": identity.list_organizations(view.user_id),
        "mfa_pending": view.mfa_pending,
        "environment": get_settings().env,
    }


@router.post("/password-reset/request", status_code=202)
def password_reset_request(body: ResetRequestBody, _: None = limited("password_reset")) -> dict[str, str]:
    identity.request_password_reset(body.email)
    return {"status": "check_email"}


@router.post("/password-reset/confirm")
def password_reset_confirm(body: ResetConfirmBody, _: None = limited("password_reset")) -> dict[str, str]:
    identity.reset_password(body.token, body.new_password)
    return {"status": "password_changed"}


@router.post("/mfa/enroll")
def mfa_enroll(view: SessionDep) -> dict[str, str]:
    return identity.begin_mfa_enrollment(view.user_id)


@router.post("/mfa/confirm")
def mfa_confirm(body: MfaBody, view: SessionDep) -> dict[str, list[str]]:
    return {"recovery_codes": identity.confirm_mfa_enrollment(view.user_id, body.code)}


@router.get("/sessions")
def sessions(view: SessionDep) -> dict[str, Any]:
    return {"sessions": identity.list_sessions(view.user_id), "current": view.session_id}


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, view: SessionDep) -> dict[str, str]:
    identity.revoke_session(view.user_id, session_id)
    return {"status": "revoked"}
