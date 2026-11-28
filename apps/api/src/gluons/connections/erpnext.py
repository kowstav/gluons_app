"""Read-only verification for the operator-configured ERPNext pilot site."""
from __future__ import annotations

import json
import re
from contextlib import closing
from urllib.parse import urlsplit

import httpx

from gluons.config import get_settings
from gluons.core import GluonsError, validation_failed
from gluons.net import GuardedClient, ResponseTooLarge


def target() -> tuple[str, str] | None:
    settings = get_settings()
    raw_origin = settings.erpnext_base_url
    if not raw_origin or not settings.erpnext_environment:
        return None
    try:
        parts = urlsplit(raw_origin)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.path not in ("", "/") or parts.query or parts.fragment or parts.port == 0):
            return None
    except ValueError:
        return None
    return raw_origin.rstrip("/"), settings.erpnext_environment


def identity(origin: str, credentials: dict[str, str]) -> str:
    """Check the authenticated user and Sales Order read access without creating a record."""
    key, secret = credentials["api_key"], credentials["api_secret"]
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value) for value in (key, secret)):
        raise validation_failed("Enter the ERPNext service account API key and secret.")
    client = GuardedClient((urlsplit(origin).hostname or "",), timeout=8,
                           headers={"Authorization": f"token {key}:{secret}"})
    with closing(client):
        account = _read(client, origin + "/api/method/frappe.auth.get_logged_user").get("message")
        if (not isinstance(account, str) or not account or account.strip() != account
                or len(account) > 200 or account.casefold() in ("guest", "administrator")):
            raise GluonsError("PROVIDER_IDENTITY_UNVERIFIED", "Use a restricted ERPNext service account.", status=409)
        sales_orders = _read(client, origin + "/api/resource/Sales%20Order",
                             params={"limit_page_length": 1, "fields": '["name"]'}).get("data")
        if not isinstance(sales_orders, list):
            raise GluonsError("PROVIDER_IDENTITY_UNVERIFIED", "ERPNext did not confirm Sales Order read access.", status=409)
    return account


def _read(client: GuardedClient, url: str, **kwargs) -> dict:
    try:
        raw, _, status = client.get_bytes(url, max_bytes=65536, **kwargs)
        if status in (401, 403):
            raise GluonsError("CONNECTION_REAUTH_REQUIRED", "ERPNext refused the credentials or Sales Order read access.", status=409)
        if status != 200:
            raise GluonsError("EXTERNAL_UNAVAILABLE", "The configured ERPNext site did not respond successfully.", status=502)
        result = json.loads(raw)
    except (httpx.HTTPError, ResponseTooLarge, ValueError) as exc:
        raise GluonsError("EXTERNAL_UNAVAILABLE", "The configured ERPNext site could not be verified.", status=502) from exc
    if not isinstance(result, dict) or result.get("simulator"):
        raise GluonsError("PROVIDER_IDENTITY_UNVERIFIED", "Use a real ERPNext site for this connection.", status=409)
    return result
