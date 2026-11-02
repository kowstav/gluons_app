"""Small fixed-host Graph API boundary. Credentials never appear in request URLs."""
from __future__ import annotations

import re
from typing import Any

import httpx

from gluons.actions import ProviderError, classify_http
from gluons.config import get_settings
from gluons.net import GuardedClient

ID = re.compile(r"[0-9]{3,40}")


def identifier(value: str) -> str:
    if not ID.fullmatch(value):
        raise ProviderError("permanent", "A verified numeric Meta resource identifier is required.")
    return value


class MetaClient:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None):
        version = get_settings().meta_graph_version
        if not re.fullmatch(r"v[0-9]{1,2}\.0", version):
            raise ProviderError("permanent", "Configure a supported Meta Graph API version.")
        self.base = f"https://graph.facebook.com/{version}"
        self.client = GuardedClient(("graph.facebook.com",), headers={"Authorization": f"Bearer {token}"}, transport=transport)

    def call(self, method: str, resource: str, **kwargs: Any) -> dict:
        try:
            response = self.client.request(method, f"{self.base}/{resource}", **kwargs)
        except httpx.TransportError as exc:
            raise ProviderError("ambiguous" if method == "POST" else "retryable", "Meta could not confirm the request.") from exc
        if response.status_code >= 400:
            raise classify_http(response.status_code)
        try:
            value = response.json()
        except ValueError as exc:
            raise ProviderError("ambiguous" if method == "POST" else "retryable", "Meta returned an unreadable response.") from exc
        if not isinstance(value, dict) or "error" in value:
            raise ProviderError("permanent", "Meta refused or could not verify the requested resource.")
        return value

    def close(self) -> None:
        self.client.close()
