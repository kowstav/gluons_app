"""Per-address limits for endpoints anyone can call without signing in.

Cloud Armor limits the same routes at the edge. These hold without it: locally, before the edge
exists, and if it is misconfigured (ADR-026). Account lockout still applies on top.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Depends, Request
from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert

from gluons.config import get_settings
from gluons.core import GluonsError, utcnow
from gluons.db.models import RateLimit
from gluons.db.session import public_tx, system_tx

# name: (requests, window in seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "login": (20, 60),
    "mfa": (30, 60),
    "register": (10, 3600),
    "password_reset": (10, 3600),
    "verify_email": (30, 3600),
    "oauth_register": (30, 3600),
    "oauth_authorize": (60, 60),
    "oauth_token": (120, 60),
    "shopify_install": (30, 60),
}


def client_address(request: Request) -> str:
    """The proxies in front of the API each append to X-Forwarded-For, so only the entry they
    wrote can be trusted. Anything to its left was supplied by the client."""
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
        if len(forwarded) >= hops:
            return forwarded[-hops][:64]
    return (request.client.host if request.client else "unknown")[:64]


def hit(name: str, request: Request) -> None:
    if not get_settings().rate_limits_enabled:
        return
    limit, window = LIMITS[name]
    now = utcnow()
    start = datetime.fromtimestamp(int(now.timestamp()) // window * window, UTC)
    table = RateLimit.__table__
    with public_tx() as session:
        count = session.execute(insert(RateLimit).values(key=f"{name}:{client_address(request)}", window_start=start, count=1)
                                .on_conflict_do_update(index_elements=[table.c.key], set_={
                                    "count": case((table.c.window_start == start, table.c.count + 1), else_=1),
                                    "window_start": start}).returning(table.c.count)).scalar_one()
    if count > limit:
        retry = int((start + timedelta(seconds=window) - now).total_seconds()) + 1
        raise GluonsError("RATE_LIMITED", "Too many attempts from this network. Try again shortly.", status=429,
                          details={"retry_after_seconds": retry})


def limited(name: str) -> object:
    """A route dependency: `_: None = limited("login")`."""
    if name not in LIMITS:
        raise ValueError(f"unknown rate limit {name}")

    def dependency(request: Request) -> None:
        hit(name, request)

    return Depends(dependency)


def purge() -> int:
    with system_tx() as session:
        return session.execute(delete(RateLimit).where(RateLimit.window_start < utcnow() - timedelta(days=1))).rowcount
