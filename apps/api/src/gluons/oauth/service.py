"""OAuth 2.1 public clients, explicit web consent and independently revocable bindings.

Opaque credentials are hashed at rest. System access only routes an unknown bearer
to its tenant; every subsequent operation uses the restricted application role.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import re
import socket
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from gluons import audit, identity
from gluons.authz import ASSISTANT_SCOPES, DEFAULT_ASSISTANT_SCOPES, Principal
from gluons.config import get_settings
from gluons.core import (
    GluonsError,
    auth_required,
    constant_time_equals,
    forbidden,
    new_id,
    new_secret_token,
    not_found,
    token_hash,
    utcnow,
)
from gluons.db.models import AgentBinding, OAuthAuthorizationRequest, OAuthClient, OAuthToken, User
from gluons.db.session import public_tx, system_tx, tenant_tx


class OAuthError(GluonsError):
    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(error, description, status=status)


def issuer() -> str:
    return get_settings().public_api_url.rstrip("/")


def resource() -> str:
    return f"{issuer()}/mcp"


def metadata() -> dict[str, Any]:
    return {
        "issuer": issuer(),
        "authorization_endpoint": f"{issuer()}/oauth/authorize",
        "token_endpoint": f"{issuer()}/oauth/token",
        "registration_endpoint": f"{issuer()}/oauth/register",
        "revocation_endpoint": f"{issuer()}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": list(ASSISTANT_SCOPES),
        "authorization_response_iss_parameter_supported": True,
        "client_id_metadata_document_supported": bool(_cimd_hosts()),
    }


def protected_metadata() -> dict[str, Any]:
    return {
        "resource": resource(),
        "authorization_servers": [issuer()],
        "scopes_supported": list(ASSISTANT_SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "Gluons business automations",
    }


def _valid_redirect(uri: str) -> bool:
    try:
        parts = urlsplit(uri)
        # Exact registered redirect matching; wildcards, credentials and fragments
        # would let a client exfiltrate another user's authorization code.
        return bool(
            len(uri) <= 2048 and parts.hostname and not parts.username and not parts.password
            and not parts.fragment and "*" not in uri
            and (parts.scheme == "https" or (
                parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")
            ))
        )
    except ValueError:
        return False


def _scope_list(scope: str | None) -> list[str]:
    if scope is not None and not isinstance(scope, str):
        raise OAuthError("invalid_scope", "Scopes must be a space-delimited string.")
    scopes = list(dict.fromkeys(scope.split() if scope else DEFAULT_ASSISTANT_SCOPES))
    if not scopes or set(scopes) - set(ASSISTANT_SCOPES):
        raise OAuthError("invalid_scope", "Request only documented Gluons scopes.")
    return scopes


def register_client(data: dict[str, Any], *, client_id: str | None = None) -> dict[str, Any]:
    redirects = data.get("redirect_uris")
    if not isinstance(redirects, list) or not 1 <= len(redirects) <= 20 or any(
        not isinstance(uri, str) or not _valid_redirect(uri) for uri in redirects
    ):
        raise OAuthError("invalid_redirect_uri", "Register exact HTTPS or loopback redirect URIs.")
    name = data.get("client_name", "Assistant")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise OAuthError("invalid_client_metadata", "Provide a client name of at most 200 characters.")
    if data.get("token_endpoint_auth_method", "none") != "none":
        raise OAuthError("invalid_client_metadata", "Only public clients using PKCE are supported.")
    grants = data.get("grant_types", ["authorization_code", "refresh_token"])
    if (not isinstance(grants, list) or not grants or any(not isinstance(g, str) for g in grants)
            or set(grants) - {"authorization_code", "refresh_token"}):
        raise OAuthError("invalid_client_metadata", "Only authorization_code and refresh_token grants are supported.")
    if data.get("response_types", ["code"]) != ["code"]:
        raise OAuthError("invalid_client_metadata", "Only the code response type is supported.")
    client_id = client_id or new_id("cli")
    if len(client_id) > 300:
        raise OAuthError("invalid_client_metadata", "The client identifier is too long.")
    with public_tx() as session:
        row = session.get(OAuthClient, client_id)
        if row is None:
            row = OAuthClient(
                id=client_id, registration="cimd" if client_id.startswith("https://") else "dcr",
                client_name=name.strip(), redirect_uris=redirects, token_endpoint_auth_method="none",  # noqa: S106 - OAuth public-client method
                # These are presentation hints, never verified identities or authority.
                host_kind="other", metadata_json={"scope": " ".join(_scope_list(data.get("scope")))},
            )
            session.add(row)
        elif row.registration != "cimd":
            raise OAuthError("invalid_client_metadata", "This client is already registered.")
        else:
            row.client_name, row.redirect_uris = name.strip(), redirects
        session.flush()
        return {
            "client_id": row.id, "client_name": row.client_name, "redirect_uris": row.redirect_uris,
            "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "scope": row.metadata_json.get("scope"),
        }


def _cimd_hosts() -> set[str]:
    # Operator-reviewed hosts only. Arbitrary client IDs must not become an SSRF
    # primitive. An empty allowlist truthfully disables CIMD advertisement.
    return {h.strip().lower() for h in os.environ.get("GLUONS_OAUTH_CIMD_HOSTS", "").split(",") if h.strip()}


def _fetch_metadata(client_id: str) -> None:
    try:
        parts = urlsplit(client_id)
        port = parts.port
    except ValueError as exc:
        raise OAuthError("invalid_client", "The metadata URL is invalid.") from exc
    if (parts.scheme != "https" or not parts.hostname or parts.hostname.lower() not in _cimd_hosts()
            or port not in (None, 443) or parts.username or parts.password or parts.fragment
            or not parts.path or parts.path == "/" or len(client_id) > 300):
        raise OAuthError("invalid_client", "This client metadata origin is not approved.")
    try:
        addresses = list({i[4][0] for i in socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)})
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise OAuthError("invalid_client", "Client metadata must resolve to a public address.")
        # Pin the validated address so DNS cannot rebind between validation and
        # connection. Preserve TLS hostname verification and the HTTP Host header.
        address = addresses[0]
        authority = f"[{address}]" if ":" in address else address
        pinned = urlunsplit(("https", authority, parts.path, parts.query, ""))
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", pinned, headers={"Host": parts.hostname, "Accept": "application/json"},
                               extensions={"sni_hostname": parts.hostname}) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise OAuthError("invalid_client_metadata", "Client metadata exceeds the size limit.")
                import json

                data = json.loads(body)
        if not isinstance(data, dict) or data.get("client_id") != client_id:
            raise OAuthError("invalid_client_metadata", "Metadata client_id must exactly match its URL.")
        register_client(data, client_id=client_id)
    except OAuthError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, httpx.HTTPError) as exc:
        raise OAuthError("invalid_client", "Client metadata could not be verified.") from exc


def begin_authorization(*, client_id: str, redirect_uri: str, code_challenge: str, resource_uri: str,
                        scope: str | None = None, state: str | None = None,
                        response_type: str = "code", code_challenge_method: str = "S256") -> dict[str, Any]:
    if len(client_id) > 300:
        raise OAuthError("invalid_client", "The client identifier is too long.")
    if response_type != "code":
        raise OAuthError("unsupported_response_type", "Use the authorization code flow.")
    if code_challenge_method != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code_challenge):
        raise OAuthError("invalid_request", "PKCE S256 with a SHA-256 challenge is required.")
    if resource_uri != resource():
        raise OAuthError("invalid_target", "Request the exact Gluons MCP resource URL.")
    if state is not None and len(state) > 2048:
        raise OAuthError("invalid_request", "State exceeds the size limit.")
    scopes = _scope_list(scope)
    # CIMD is deliberately refreshed at authorization, so changed metadata does
    # not retain old redirect destinations forever.
    if client_id.startswith("https://"):
        _fetch_metadata(client_id)
    with public_tx() as session:
        client = session.get(OAuthClient, client_id)
        if client is None or client.disabled_at is not None:
            raise OAuthError("invalid_client", "Register this assistant before authorization.")
        if redirect_uri not in client.redirect_uris:
            raise OAuthError("invalid_request", "The redirect URI is not registered for this client.")
        row = OAuthAuthorizationRequest(
            id=new_id("oar"), client_id=client_id, redirect_uri=redirect_uri, state=state, scopes=scopes,
            resource=resource_uri, code_challenge=code_challenge, code_challenge_method="S256",
            expires_at=utcnow() + timedelta(minutes=10),
        )
        session.add(row)
        session.flush()
        return {"request_id": row.id, "consent_url": f"{get_settings().public_web_url}/assistant-consent?request_id={row.id}"}


def consent_preview(principal: Principal, request_id: str) -> dict[str, Any]:
    principal.require("org:read")
    with public_tx() as session:
        row = _pending(session, request_id)
        client = session.get(OAuthClient, row.client_id)
        return {"id": row.id, "client_id": row.client_id, "client_name": client.client_name if client else "Assistant",
                "scopes": row.scopes, "resource": row.resource, "tenant_id": principal.tenant_id,
                "expires_at": row.expires_at.isoformat(), "redirect_uri": row.redirect_uri,
                "notice": "This grants assistant access to the selected business only. Provider access and action policies remain separate."}


def _pending(session: Session, request_id: str) -> OAuthAuthorizationRequest:
    row = session.execute(select(OAuthAuthorizationRequest).where(
        OAuthAuthorizationRequest.id == request_id).with_for_update()).scalar_one_or_none()
    if row is None or row.status != "pending" or row.expires_at <= utcnow():
        raise OAuthError("invalid_request", "This authorization request is invalid, used, or expired.")
    return row


def consent(principal: Principal, request_id: str, *, approve: bool, scopes: list[str] | None = None,
            approval_max_tier: str | None = None) -> dict[str, Any]:
    if principal.kind != "user" or not principal.user_id:
        raise forbidden("Sign in to Gluons Web to authorize an assistant.")
    principal.require("org:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        user = session.get(User, principal.user_id)
        if user is None or not user.email_verified_at or user.disabled_at:
            raise forbidden("Verify your Gluons email before connecting an assistant.")
        row = _pending(session, request_id)
        client = session.get(OAuthClient, row.client_id)
        if client is None or client.disabled_at:
            raise OAuthError("invalid_client", "This assistant is disabled.")
        # Even if an operator changes a CIMD redirect while consent is open,
        # never issue a code to a URI no longer registered for that client.
        if row.redirect_uri not in client.redirect_uris:
            raise OAuthError("invalid_request", "The client's redirect configuration changed. Start again.")
        params: dict[str, str] = {"iss": issuer()}
        if row.state is not None:
            params["state"] = row.state
        if not approve:
            row.status = "denied"
            params["error"] = "access_denied"
        else:
            granted = row.scopes if scopes is None else list(dict.fromkeys(scopes))
            if not granted or set(granted) - set(row.scopes):
                raise OAuthError("invalid_scope", "Consent may only narrow the requested scopes.")
            if approval_max_tier not in (None, "R0", "R1", "R2"):
                raise OAuthError("invalid_request", "High-risk approvals must stay on Gluons Web.")
            binding = AgentBinding(
                id=new_id("agb"), tenant_id=principal.tenant_id, user_id=principal.user_id,
                client_id=client.id, display_name=client.client_name, scopes=granted, audience=row.resource,
                assistant_approval_max_tier=approval_max_tier or "R1",
            )
            session.add(binding)
            session.flush()
            raw_code = new_secret_token("gc_")
            row.status, row.user_id, row.tenant_id = "approved", principal.user_id, principal.tenant_id
            row.binding_id, row.scopes, row.code_hash = binding.id, granted, token_hash(raw_code)
            row.code_expires_at = utcnow() + timedelta(minutes=2)
            params["code"] = raw_code
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="assistant.consent",
                     actor=principal, resource_type="oauth_request", resource_id=row.id,
                     decision="approved" if approve else "denied", data={"client_id": client.id, "scopes": scopes or row.scopes})
        parts = urlsplit(row.redirect_uri)
        query = "&".join(p for p in (parts.query, urlencode(params)) if p)
        return {"redirect_url": urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))}


def _issue_pair(session: Session, binding: AgentBinding, scopes: list[str], family: str | None = None) -> dict[str, Any]:
    settings, now = get_settings(), utcnow()
    family = family or new_id("fam")
    access, refresh = new_secret_token("ga_"), new_secret_token("gr_")
    for raw, kind, ttl in ((access, "access", settings.oauth_access_token_ttl_seconds),
                           (refresh, "refresh", settings.oauth_refresh_token_ttl_seconds)):
        session.add(OAuthToken(id=new_id("oat"), tenant_id=binding.tenant_id, binding_id=binding.id,
                               kind=kind, token_hash=token_hash(raw), family_id=family, scopes=scopes,
                               audience=binding.audience, expires_at=now + timedelta(seconds=ttl)))
    return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
            "expires_in": settings.oauth_access_token_ttl_seconds, "scope": " ".join(scopes)}


def exchange_code(*, client_id: str, code: str, redirect_uri: str, code_verifier: str,
                  resource_uri: str) -> dict[str, Any]:
    if resource_uri != resource():
        raise OAuthError("invalid_target", "The token must target the Gluons MCP resource.")
    if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", code_verifier):
        raise OAuthError("invalid_grant", "The PKCE verifier is invalid.")
    with public_tx() as session:
        tenant_id = session.execute(select(OAuthAuthorizationRequest.tenant_id).where(
            OAuthAuthorizationRequest.code_hash == token_hash(code))).scalar_one_or_none()
    if not tenant_id:
        raise OAuthError("invalid_grant", "This code is invalid, used, or expired.")
    with tenant_tx(tenant_id) as session:
        row = session.execute(select(OAuthAuthorizationRequest).where(
            OAuthAuthorizationRequest.code_hash == token_hash(code)).with_for_update()).scalar_one_or_none()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode().rstrip("=")
        if (row is None or row.status != "approved" or row.code_used_at is not None
                or row.code_expires_at is None or row.code_expires_at <= utcnow()
                or row.client_id != client_id or row.redirect_uri != redirect_uri or row.resource != resource_uri
                or not constant_time_equals(row.code_challenge, challenge)):
            raise OAuthError("invalid_grant", "This code, redirect URI, or verifier is invalid.")
        binding = session.get(AgentBinding, row.binding_id, with_for_update=True)
        _check_binding(session, binding, client_id)
        row.code_used_at, row.status = utcnow(), "exchanged"
        result = _issue_pair(session, binding, row.scopes)
        audit.record(session, tenant_id=tenant_id, category="permission", event_type="assistant.connected",
                     actor=binding.user_id, resource_type="agent_binding", resource_id=binding.id,
                     data={"client_id": client_id, "scopes": row.scopes})
        return result


def _check_binding(session: Session, binding: AgentBinding | None, client_id: str | None = None) -> None:
    if binding is None or binding.status != "active" or binding.revoked_at or (client_id and binding.client_id != client_id):
        raise OAuthError("invalid_grant", "This assistant authorization is no longer valid.")
    client, user = session.get(OAuthClient, binding.client_id), session.get(User, binding.user_id)
    if client is None or client.disabled_at or user is None or user.disabled_at or not user.email_verified_at:
        raise OAuthError("invalid_grant", "This assistant authorization is no longer valid.")
    try:
        identity.principal_for_user(binding.user_id, binding.tenant_id)
    except GluonsError as exc:
        raise OAuthError("invalid_grant", "The business membership is no longer valid.") from exc


def _token_tenant(raw: str) -> str | None:
    if len(raw) > 300:
        return None
    with system_tx() as session:
        return session.execute(select(OAuthToken.tenant_id).where(OAuthToken.token_hash == token_hash(raw))).scalar_one_or_none()


def refresh_tokens(*, client_id: str, refresh_token: str, resource_uri: str, scope: str | None = None) -> dict[str, Any]:
    if resource_uri != resource():
        raise OAuthError("invalid_target", "The token must target the Gluons MCP resource.")
    tenant_id = _token_tenant(refresh_token)
    if not tenant_id:
        raise OAuthError("invalid_grant", "The refresh credential is invalid.")
    reused = False
    with tenant_tx(tenant_id) as session:
        row = session.execute(select(OAuthToken).where(
            OAuthToken.token_hash == token_hash(refresh_token)).with_for_update()).scalar_one()
        binding = session.get(AgentBinding, row.binding_id, with_for_update=True)
        _check_binding(session, binding, client_id)
        if row.kind != "refresh" or row.audience != resource_uri or row.expires_at <= utcnow() or row.revoked_at:
            raise OAuthError("invalid_grant", "The refresh credential is invalid or expired.")
        if row.used_at is not None:
            # Commit the revocation before returning invalid_grant; raising inside
            # the transaction would silently roll the security response back.
            session.execute(update(OAuthToken).where(OAuthToken.family_id == row.family_id).values(revoked_at=utcnow()))
            audit.record(session, tenant_id=tenant_id, category="permission", event_type="assistant.refresh_reuse_detected",
                         actor=binding.user_id, resource_type="agent_binding", resource_id=binding.id)
            reused = True
        else:
            scopes = row.scopes if scope is None else _scope_list(scope)
            if set(scopes) - set(row.scopes):
                raise OAuthError("invalid_scope", "Refresh cannot increase the granted scopes.")
            row.used_at = utcnow()
            result = _issue_pair(session, binding, scopes, row.family_id)
    if reused:
        raise OAuthError("invalid_grant", "Refresh reuse was detected. Reconnect this assistant.")
    return result


def resolve_access_token(raw: str) -> Principal:
    tenant_id = _token_tenant(raw)
    if not tenant_id:
        raise auth_required("The assistant access credential is invalid.")
    with tenant_tx(tenant_id) as session:
        row = session.execute(select(OAuthToken).where(OAuthToken.token_hash == token_hash(raw))).scalar_one_or_none()
        if row is None or row.kind != "access" or row.revoked_at or row.expires_at <= utcnow() or row.audience != resource():
            raise auth_required("The assistant access credential is invalid or expired.")
        binding = session.get(AgentBinding, row.binding_id)
        try:
            _check_binding(session, binding)
        except GluonsError as exc:
            raise auth_required("The assistant authorization was revoked.") from exc
        member = identity.principal_for_user(binding.user_id, tenant_id)
        binding.last_used_at = utcnow()
        # The role is read anew for every request. Consent cannot preserve rights
        # after a member is demoted or removed from the business.
        limits = [binding.assistant_approval_max_tier or "R1", member.approval_tier_limit or "R4"]
        return Principal(kind="assistant", tenant_id=tenant_id, user_id=binding.user_id, role=member.role,
                         scopes=frozenset(row.scopes) & frozenset(binding.scopes), binding_id=binding.id,
                         approval_tier_limit=min(limits))


def list_bindings(principal: Principal) -> list[dict[str, Any]]:
    principal.require("assistants:manage")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        rows = session.execute(select(AgentBinding).order_by(AgentBinding.created_at.desc())).scalars()
        return [{"id": row.id, "client_id": row.client_id, "display_name": row.display_name, "host_kind": row.host_kind,
                 "user_id": row.user_id, "scopes": row.scopes, "status": row.status,
                 "created_at": row.created_at.isoformat(),
                 "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None} for row in rows]


def revoke_binding(principal: Principal, binding_id: str) -> dict[str, Any]:
    principal.require("assistants:manage")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(AgentBinding, binding_id, with_for_update=True)
        if row is None:
            raise not_found("Assistant")
        if row.status != "revoked":
            row.status, row.revoked_at, row.revoked_by = "revoked", utcnow(), principal.actor_id
            session.execute(update(OAuthToken).where(OAuthToken.binding_id == row.id).values(revoked_at=utcnow()))
            audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="assistant.revoked",
                         actor=principal, resource_type="agent_binding", resource_id=row.id)
    return {"id": binding_id, "status": "revoked", "effects": "This assistant's tokens are revoked. Existing automation policies and other assistants remain independent."}


def revoke_token(*, client_id: str, token: str) -> None:
    tenant_id = _token_tenant(token)
    if not tenant_id:
        return
    with tenant_tx(tenant_id) as session:
        row = session.execute(select(OAuthToken).where(OAuthToken.token_hash == token_hash(token)).with_for_update()).scalar_one_or_none()
        binding = session.get(AgentBinding, row.binding_id) if row else None
        # RFC 7009: unknown/wrong-client tokens receive the same successful reply.
        if row is not None and binding is not None and binding.client_id == client_id:
            session.execute(update(OAuthToken).where(OAuthToken.family_id == row.family_id).values(revoked_at=utcnow()))
            audit.record(session, tenant_id=tenant_id, category="permission", event_type="assistant.credentials_revoked",
                         actor=binding.user_id, resource_type="agent_binding", resource_id=binding.id)
