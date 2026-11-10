"""Google OIDC protocol primitives; these do not create sessions or link accounts."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from pydantic import EmailStr, TypeAdapter, ValidationError

from gluons.core import GluonsError, constant_time_equals, token_hash
from gluons.net import GuardedClient, ResponseTooLarge, write_permit

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
EXCHANGE_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CALLBACK_PATH = "/v1/auth/google/callback"
_OPAQUE = re.compile(r"^[A-Za-z0-9_-]{32,200}$")
_SUBJECT = re.compile(r"^[\x21-\x7e]{1,255}$")


@dataclass(frozen=True, repr=False)
class FlowSecrets:
    state: str
    browser: str
    nonce: str
    verifier: str
    challenge: str


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    email: str
    display_name: str


def new_flow() -> FlowSecrets:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return FlowSecrets(secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32), verifier, challenge)


def _redirect(value: str, *, allow_loopback: bool) -> None:
    try:
        parts = urlsplit(value)
        valid_scheme = parts.scheme == "https" or (allow_loopback and parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"})
        valid = (valid_scheme and bool(parts.hostname) and parts.username is None and parts.password is None
                 and parts.path == CALLBACK_PATH and not parts.query and not parts.fragment and (parts.port is None or 1 <= parts.port <= 65535))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise GluonsError("GOOGLE_IDENTITY_CONFIGURATION", "Configure a valid Google sign-in callback.", status=503)


def authorization_url(*, client_id: str, redirect_uri: str, flow: FlowSecrets, allow_loopback: bool = False) -> str:
    _redirect(redirect_uri, allow_loopback=allow_loopback)
    if (not client_id or not all(_OPAQUE.fullmatch(value) for value in (flow.state, flow.browser, flow.nonce, flow.verifier))
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", flow.challenge)):
        raise GluonsError("GOOGLE_IDENTITY_CONFIGURATION", "Google sign-in is not configured.", status=503)
    expected = base64.urlsafe_b64encode(hashlib.sha256(flow.verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    if not constant_time_equals(expected, flow.challenge):
        raise GluonsError("GOOGLE_IDENTITY_CONFIGURATION", "Google sign-in is not configured.", status=503)
    return AUTH_URL + "?" + urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": "openid email profile", "state": flow.state, "nonce": flow.nonce,
        "code_challenge": flow.challenge, "code_challenge_method": "S256"})


def exchange_code(*, client_id: str, client_secret: str, redirect_uri: str, code: str, verifier: str,
                  allow_loopback: bool = False, transport: httpx.BaseTransport | None = None) -> str:
    """Exchange a browser-bound code; the ID token stays server-side."""
    _redirect(redirect_uri, allow_loopback=allow_loopback)
    if not client_id or not client_secret or not isinstance(code, str) or not 1 <= len(code) <= 4096 or not _OPAQUE.fullmatch(verifier):
        raise GluonsError("GOOGLE_IDENTITY_INVALID", "Google sign-in could not be verified. Start again.", status=401)
    client = GuardedClient(("oauth2.googleapis.com",), transport=transport, timeout=10)
    try:
        with write_permit("google_identity_code_exchange"):
            response = client.post(EXCHANGE_ENDPOINT, data={"grant_type": "authorization_code", "client_id": client_id,
                "client_secret": client_secret, "redirect_uri": redirect_uri, "code": code, "code_verifier": verifier})
        if response.status_code != 200 or len(response.content) > 65536:
            raise GluonsError("GOOGLE_IDENTITY_INVALID", "Google sign-in could not be verified. Start again.", status=401)
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("id_token"), str) or not 1 <= len(result["id_token"]) <= 16384:
            raise GluonsError("GOOGLE_IDENTITY_INVALID", "Google sign-in could not be verified. Start again.", status=401)
        return result["id_token"]
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise GluonsError("GOOGLE_IDENTITY_UNAVAILABLE", "Google sign-in is temporarily unavailable. Try again.", status=503) from exc
    finally:
        client.close()


def verify_id_token(token: str, *, client_id: str, nonce_hash: str,
                    transport: httpx.BaseTransport | None = None) -> VerifiedIdentity:
    """Verify a Google ID token against fixed-host keys and the consumed intent nonce."""
    invalid = GluonsError("GOOGLE_IDENTITY_INVALID", "Google sign-in could not be verified. Start again.", status=401)
    if (not isinstance(token, str) or not 1 <= len(token) <= 16384 or not client_id
            or not isinstance(nonce_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", nonce_hash)):
        raise invalid
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 200:
            raise invalid
        client = GuardedClient(("www.googleapis.com",), transport=transport, timeout=10)
        try:
            raw, _, status = client.get_bytes(JWKS_URL, max_bytes=65536)
        finally:
            client.close()
        if status != 200:
            raise GluonsError("GOOGLE_IDENTITY_UNAVAILABLE", "Google sign-in is temporarily unavailable. Try again.", status=503)
        jwks = json.loads(raw)
        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        if not isinstance(keys, list):
            raise invalid
        matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid and key.get("kty") == "RSA"
                   and key.get("use") in (None, "sig") and key.get("alg") in (None, "RS256")]
        if len(matches) != 1:
            raise invalid
        claims: dict[str, Any] = jwt.decode(token, jwt.PyJWK.from_dict(matches[0]).key, algorithms=["RS256"], audience=client_id,
            issuer=ISSUERS, options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce"]})
        audience = claims.get("aud")
        if ((isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != client_id)
                or (claims.get("azp") is not None and claims["azp"] != client_id)):
            raise invalid
        subject, nonce = claims.get("sub"), claims.get("nonce")
        if (not isinstance(subject, str) or not _SUBJECT.fullmatch(subject) or not isinstance(nonce, str)
                or not constant_time_equals(token_hash(nonce), nonce_hash) or claims.get("email_verified") is not True):
            raise invalid
        email = str(TypeAdapter(EmailStr).validate_python(claims.get("email"))).strip().lower()
        name = claims.get("name")
        return VerifiedIdentity("https://accounts.google.com", subject, email, name.strip()[:200] if isinstance(name, str) and name.strip() else email[:200])
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, ValidationError, ResponseTooLarge) as exc:
        raise invalid from exc
    except httpx.HTTPError as exc:
        raise GluonsError("GOOGLE_IDENTITY_UNAVAILABLE", "Google sign-in is temporarily unavailable. Try again.", status=503) from exc
