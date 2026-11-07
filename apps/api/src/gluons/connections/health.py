"""Finds lapsing account access before a customer-facing automation does.

Google can revoke a refresh token at any time, and expires them after 7 days while a consent
screen is still in testing. A Shopify refresh token lapses 90 days after issue. Meta's business
token may expire, and no API renews it. Each case becomes a status the owner sees in Gluons and
one email, rather than a failed order later.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from gluons import audit, events
from gluons.actions import ProviderError
from gluons.connections import google
from gluons.connections.service import get_credential
from gluons.core import GluonsError, utcnow
from gluons.db.models import Connection
from gluons.db.session import system_tx, tenant_tx

logger = logging.getLogger("gluons.connections.health")
SHOPIFY_REFRESH_MARGIN = timedelta(days=14)
WHATSAPP_WARNING = timedelta(days=7)
REAUTH_CODES = ("CONNECTION_REAUTH_REQUIRED", "PROVIDER_IDENTITY_MISMATCH", "PROVIDER_IDENTITY_UNVERIFIED", "CONNECTION_REQUIRED")


def check_all() -> dict[str, int]:
    with system_tx() as session:
        rows = session.execute(select(Connection.id, Connection.tenant_id, Connection.provider).where(
            Connection.status == "connected", Connection.disconnected_at.is_(None))).all()
    counts: dict[str, int] = {}
    for connection_id, tenant_id, provider in rows:
        try:
            outcome = check(tenant_id, connection_id, provider)
        except Exception:  # noqa: BLE001 - one account must not stop the others being checked
            logger.exception("connection health check failed", extra={"connection_id": connection_id})
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def check(tenant_id: str, connection_id: str, provider: str) -> str:
    from gluons.commerce import shopify

    with tenant_tx(tenant_id) as session:
        row = session.get(Connection, connection_id, with_for_update=True)
        if row is None or row.status != "connected" or row.disconnected_at:
            return "skipped"
        try:
            if provider == "gmail":
                credential = get_credential(session, tenant_id, row)
                if google.identity(credential["access_token"])["sub"] != row.external_account_id:
                    raise GluonsError("PROVIDER_IDENTITY_MISMATCH", "This Google account changed. Reconnect it in Gluons.", status=409)
            elif provider == "shopify":
                shopify.get_credential(session, tenant_id, row, force_refresh=_refresh_due(row))
            elif provider == "whatsapp_business":
                if row.token_expires_at and row.token_expires_at <= utcnow():
                    raise GluonsError("CONNECTION_REAUTH_REQUIRED", "Meta's access for this number has expired. Reconnect WhatsApp in Gluons.", status=409)
                if row.token_expires_at and row.token_expires_at <= utcnow() + WHATSAPP_WARNING:
                    _notify_once(session, row, "token_expiring", "Your WhatsApp connection expires soon",
                                 f"Meta's access for {row.account_display} ends on {row.token_expires_at:%d %b %Y}. Reconnect before then so messages keep flowing.")
                    return "warned"
            else:
                return "skipped"
        except GluonsError as exc:
            if exc.code in REAUTH_CODES:
                return _needs_reauth(session, row, exc.message)
            return "unavailable"
        except ProviderError as exc:
            if exc.kind == "auth":
                return _needs_reauth(session, row, str(exc))
            return "unavailable"
        except httpx.HTTPError:
            return "unavailable"
        row.last_checked_at = utcnow()
        row.metadata_json = {key: value for key, value in row.metadata_json.items() if key != "health_notice"}
        return "healthy"


def _refresh_due(row: Connection) -> bool:
    raw = row.metadata_json.get("refresh_token_expires_at")
    try:
        return bool(raw) and datetime.fromisoformat(raw) <= utcnow() + SHOPIFY_REFRESH_MARGIN
    except (TypeError, ValueError):
        return False


def _needs_reauth(session, row: Connection, detail: str) -> str:  # type: ignore[no-untyped-def]
    row.status, row.status_detail = "needs_reauth", detail
    audit.record(session, tenant_id=row.tenant_id, category="permission", event_type="connection.health_failed", actor="connection_health",
                 resource_type="connection", resource_id=row.id, data={"provider": row.provider})
    _notify_once(session, row, "needs_reauth", f"Reconnect {row.account_display}", f"{detail} Automations that use it wait until you do.")
    return "needs_reauth"


def _notify_once(session, row: Connection, notice: str, title: str, body: str) -> None:  # type: ignore[no-untyped-def]
    if row.metadata_json.get("health_notice") == notice:
        return
    row.metadata_json = {**row.metadata_json, "health_notice": notice}
    events.notify(session, row.tenant_id, kind=f"connection_{notice}", severity="action", title=title[:300], body=body,
                  link="/connections", resource_type="connection", resource_id=row.id)
