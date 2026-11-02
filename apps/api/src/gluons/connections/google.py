"""Google's server authorization flow. No provider credential leaves this module's callers."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from gluons.config import get_settings
from gluons.core import GluonsError
from gluons.net import GuardedClient, write_permit

READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
CAPABILITIES = {
    "read_order_messages": READ_SCOPE,
    "read_messages": READ_SCOPE,
    "send_order_confirmations": SEND_SCOPE,
    "send_messages": SEND_SCOPE,
}


def configured() -> bool:
    settings = get_settings()
    return bool(settings.google_client_id and settings.google_client_secret)


def callback_url() -> str:
    return f"{get_settings().public_api_url.rstrip('/')}/v1/oauth/gmail/callback"


def scopes_for(capabilities: list[str]) -> list[str]:
    if not capabilities or any(c not in CAPABILITIES for c in capabilities):
        raise GluonsError("CAPABILITY_UNSUPPORTED", "Choose Gmail message reading or sending capabilities.", status=422)
    return sorted({"openid", EMAIL_SCOPE, *(CAPABILITIES[c] for c in capabilities)})


def authorization_url(scopes: list[str], state: str, challenge: str) -> str:
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": get_settings().google_client_id,
        "redirect_uri": callback_url(), "response_type": "code", "scope": " ".join(scopes),
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
        # Do not combine a previously broader grant without showing the owner its scope.
        "include_granted_scopes": "false",
    })


def _json(response: Any) -> dict:
    if response.status_code >= 400:
        code = "CONNECTION_REAUTH_REQUIRED" if response.status_code in (400, 401, 403) else "EXTERNAL_UNAVAILABLE"
        raise GluonsError(code, "Google could not verify this connection. Reconnect in Gluons.", status=409)
    result = response.json()
    if not isinstance(result, dict):
        raise GluonsError("EXTERNAL_UNAVAILABLE", "Google returned an invalid response.", status=502)
    return result


def exchange(code: str, verifier: str) -> dict:
    settings = get_settings()
    client = GuardedClient(("oauth2.googleapis.com",))
    try:
        # This permit covers explicit owner account consent, not business automation.
        with write_permit("owner_google_authorization"):
            result = _json(client.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
                "redirect_uri": callback_url(),
            }))
        if not result.get("access_token") or str(result.get("token_type", "")).lower() != "bearer":
            raise GluonsError("EXTERNAL_UNAVAILABLE", "Google did not issue a usable token.", status=502)
        return result
    finally:
        client.close()


def refresh(credential: dict) -> dict:
    if not credential.get("refresh_token"):
        raise GluonsError("CONNECTION_REAUTH_REQUIRED", "Reconnect Google to renew mailbox access.", status=409)
    settings = get_settings()
    client = GuardedClient(("oauth2.googleapis.com",))
    try:
        with write_permit("google_credential_refresh"):
            result = _json(client.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "refresh_token", "refresh_token": credential["refresh_token"],
                "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
            }))
        if not result.get("access_token"):
            raise GluonsError("CONNECTION_REAUTH_REQUIRED", "Reconnect Google to renew mailbox access.", status=409)
        return {**credential, **result}
    finally:
        client.close()


def identity(access_token: str) -> dict:
    client = GuardedClient(("www.googleapis.com",), headers={"Authorization": f"Bearer {access_token}"})
    try:
        result = _json(client.get("https://www.googleapis.com/oauth2/v3/userinfo"))
        if not result.get("sub") or not result.get("email") or result.get("email_verified") is not True:
            raise GluonsError("PROVIDER_IDENTITY_UNVERIFIED", "Google did not confirm a verified email identity.", status=409)
        return result
    finally:
        client.close()


def revoke(credential: dict) -> None:
    client = GuardedClient(("oauth2.googleapis.com",))
    try:
        with write_permit("owner_google_revocation"):
            response = client.post("https://oauth2.googleapis.com/revoke", data={
                "token": credential.get("refresh_token") or credential["access_token"],
            })
        if response.status_code != 200:
            raise GluonsError("PROVIDER_REVOCATION_PENDING", "Remove Gluons access from Google Account permissions.", status=409)
    finally:
        client.close()
