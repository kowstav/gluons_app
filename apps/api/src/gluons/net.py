"""Outbound HTTP for connectors.

This is the second lock on shadow mode. The action gateway decides whether a write may
happen; this client independently refuses any state-changing request unless the gateway
has opened a write permit for the current call. A connector bug in `prepare`, or a new
code path that forgets the gateway, fails loudly instead of touching a live system.

Hosts are allowlisted per client, so an LLM or an inbound document can never point a
connector at an arbitrary URL.
"""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

import httpx

from gluons.config import get_settings

_write_permit: ContextVar[str | None] = ContextVar("gluons_write_permit", default=None)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SideEffectBlocked(RuntimeError):
    """A state-changing request was attempted without a gateway write permit."""


class HostNotAllowed(RuntimeError):
    """A connector tried to reach a host outside its allowlist."""


@contextmanager
def write_permit(reason: str) -> Iterator[None]:
    token = _write_permit.set(reason)
    try:
        yield
    finally:
        _write_permit.reset(token)


def writes_permitted() -> bool:
    return _write_permit.get() is not None


def is_private_address(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return True
    return False


def assert_safe_customer_url(url: str) -> None:
    """Customer-entered base URLs (a self-hosted ERP) must not reach internal services."""
    parts = urlsplit(url)
    settings = get_settings()
    if settings.hosted:
        if parts.scheme != "https":
            raise HostNotAllowed("Only https endpoints can be connected.")
        if not parts.hostname or is_private_address(parts.hostname):
            raise HostNotAllowed("That address is not reachable from Gluons.")


class GuardedClient:
    def __init__(
        self,
        allowed_hosts: tuple[str, ...],
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._allowed = {h.lower() for h in allowed_hosts}
        self._client = httpx.Client(timeout=timeout, transport=transport, headers=headers, follow_redirects=False)

    def _check_host(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        if host not in self._allowed:
            raise HostNotAllowed(f"{host or url} is not an allowed host for this connector.")

    def request(self, method: str, url: str, *, read_only: bool = False, **kwargs: Any) -> httpx.Response:
        """`read_only=True` is for APIs that read over POST, such as GraphQL queries."""
        self._check_host(url)
        if method.upper() not in SAFE_METHODS and not read_only and not writes_permitted():
            raise SideEffectBlocked(f"{method} {urlsplit(url).path} was blocked: no write permit is open.")
        return self._client.request(method, url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, *, read_only: bool = False, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, read_only=read_only, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def close(self) -> None:
        self._client.close()
