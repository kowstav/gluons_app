"""Standards-shaped public OAuth endpoints and CSRF-protected web consent."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from gluons.api.deps import PrincipalDep
from gluons.oauth import service
from gluons.ratelimit import limited


class OAuthRoute(APIRoute):
    def get_route_handler(self):  # type: ignore[no-untyped-def]
        original = super().get_route_handler()

        async def handler(request: Request):  # type: ignore[no-untyped-def]
            try:
                response = await original(request)
            except service.OAuthError as exc:
                response = JSONResponse({"error": exc.code, "error_description": exc.message}, status_code=exc.status)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            return response

        return handler


router = APIRouter(route_class=OAuthRoute, tags=["assistants"])


@router.get("/.well-known/oauth-authorization-server")
def authorization_metadata() -> dict[str, Any]:
    return service.metadata()


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_metadata() -> dict[str, Any]:
    return service.protected_metadata()


class Registration(BaseModel):
    client_name: str = Field(default="Assistant", max_length=200)
    redirect_uris: list[str] = Field(min_length=1, max_length=20)
    token_endpoint_auth_method: str = "none"  # noqa: S105 - OAuth public-client method
    grant_types: list[str] = Field(default_factory=lambda: ["authorization_code", "refresh_token"])
    response_types: list[str] = Field(default_factory=lambda: ["code"])
    scope: str | None = Field(default=None, max_length=1000)


@router.post("/oauth/register", status_code=201)
def register(body: Registration, _: None = limited("oauth_register")) -> dict[str, Any]:
    return service.register_client(body.model_dump())


@router.get("/oauth/authorize")
def authorize(request: Request, _: None = limited("oauth_authorize")) -> RedirectResponse:
    params = request.query_params
    if any(len(params.getlist(key)) != 1 for key in params):
        raise service.OAuthError("invalid_request", "Duplicate authorization parameters are not supported.")
    for key in ("client_id", "redirect_uri", "code_challenge", "resource", "response_type"):
        if not params.get(key):
            raise service.OAuthError("invalid_request", f"The {key} parameter is required.")
    result = service.begin_authorization(
        client_id=params["client_id"], redirect_uri=params["redirect_uri"],
        code_challenge=params["code_challenge"], resource_uri=params["resource"],
        response_type=params["response_type"], code_challenge_method=params.get("code_challenge_method", ""),
        scope=params.get("scope"), state=params.get("state"),
    )
    return RedirectResponse(result["consent_url"], status_code=302)


async def _form(request: Request) -> dict[str, str]:
    # Bound the read before multipart/form parsers allocate untrusted input.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            raise service.OAuthError("invalid_request", "The OAuth request is too large.")
    from urllib.parse import parse_qs

    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise service.OAuthError("invalid_request", "Send application/x-www-form-urlencoded.")
    try:
        values = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=20)
    except (ValueError, UnicodeError) as exc:
        raise service.OAuthError("invalid_request", "The OAuth form is invalid.") from exc
    if any(len(v) != 1 for v in values.values()):
        raise service.OAuthError("invalid_request", "Duplicate parameters are not supported.")
    return {k: v[0] for k, v in values.items()}


@router.post("/oauth/token")
async def token(request: Request, _: None = limited("oauth_token")) -> dict[str, Any]:
    form = await _form(request)
    if request.headers.get("authorization") or form.get("client_secret"):
        raise service.OAuthError("invalid_client", "Use a public PKCE client with client_id in the form.")
    if not form.get("client_id"):
        raise service.OAuthError("invalid_client", "The client_id is required.")
    if form.get("grant_type") == "authorization_code":
        return service.exchange_code(client_id=form["client_id"], code=form.get("code", ""),
                                     redirect_uri=form.get("redirect_uri", ""), code_verifier=form.get("code_verifier", ""),
                                     resource_uri=form.get("resource", ""))
    if form.get("grant_type") == "refresh_token":
        return service.refresh_tokens(client_id=form["client_id"], refresh_token=form.get("refresh_token", ""),
                                      resource_uri=form.get("resource", ""), scope=form.get("scope"))
    raise service.OAuthError("unsupported_grant_type", "Only authorization_code and refresh_token are supported.")


@router.post("/oauth/revoke")
async def revoke(request: Request) -> dict[str, str]:
    form = await _form(request)
    service.revoke_token(client_id=form.get("client_id", ""), token=form.get("token", ""))
    return {}


class Consent(BaseModel):
    approve: bool
    scopes: list[str] | None = None
    approval_max_tier: Literal["R0", "R1", "R2"] | None = None


@router.get("/v1/assistants/consent/{request_id}")
def preview(request_id: str, principal: PrincipalDep) -> dict[str, Any]:
    return service.consent_preview(principal, request_id)


@router.post("/v1/assistants/consent/{request_id}")
def consent(request_id: str, body: Consent, principal: PrincipalDep) -> dict[str, Any]:
    return service.consent(principal, request_id, **body.model_dump())


@router.get("/v1/assistants")
def list_assistants(principal: PrincipalDep) -> dict[str, Any]:
    return {"assistants": service.list_bindings(principal)}


@router.post("/v1/assistants/{binding_id}/revoke")
def revoke_binding(binding_id: str, principal: PrincipalDep) -> dict[str, Any]:
    return service.revoke_binding(principal, binding_id)
