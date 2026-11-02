"""Replaceable execution boundary (blueprint doc 12 section 2).

The policy gate, approvals, receipts and readback all live in the gateway, outside any
backend. A backend only performs an already-authorized action, so swapping or losing an
engine never changes what the owner approved or what Gluons knows happened.
"""
from __future__ import annotations

from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from gluons.actions import ActionConnector, ConnectorContext, ProviderError, ProviderResult, classify_http
from gluons.config import get_settings
from gluons.core import GluonsError


class BackendAdapter(Protocol):
    name: str
    version: str

    def submit(
        self, connector: ActionConnector, ctx: ConnectorContext, action: str, payload: BaseModel, idempotency_key: str
    ) -> ProviderResult: ...

    def status(self, external_run_id: str) -> dict[str, Any]: ...
    def cancel(self, external_run_id: str) -> dict[str, Any]: ...
    def receipts(self, external_run_id: str) -> list[dict[str, Any]]: ...


class PythonActionsAdapter:
    """Default: runs the connector in-process. Synchronous, so there is no external run."""

    name = "python-actions"
    version = "1"

    def submit(
        self, connector: ActionConnector, ctx: ConnectorContext, action: str, payload: BaseModel, idempotency_key: str
    ) -> ProviderResult:
        return connector.execute(ctx, payload, idempotency_key)

    def status(self, external_run_id: str) -> dict[str, Any]:
        return {"external_run_id": external_run_id, "status": "synchronous"}

    def cancel(self, external_run_id: str) -> dict[str, Any]:
        return {"external_run_id": external_run_id, "cancelled": False, "reason": "synchronous backend"}

    def receipts(self, external_run_id: str) -> list[dict[str, Any]]:
        return []


class ActivepiecesAdapter:
    """Hands an authorized action to a self-hosted Activepieces flow over its webhook.

    Requires GLUONS_ACTIVEPIECES_FLOW_URL. Results still pass Gluons' own readback.
    """

    name = "activepieces"
    version = "community"

    def __init__(self, flow_url: str | None) -> None:
        self._flow_url = flow_url

    def submit(
        self, connector: ActionConnector, ctx: ConnectorContext, action: str, payload: BaseModel, idempotency_key: str
    ) -> ProviderResult:
        if not self._flow_url:
            raise GluonsError(
                "EXTERNAL_UNAVAILABLE", "The Activepieces backend is not configured.", status=503
            )
        try:
            response = httpx.post(
                self._flow_url,
                json={"action": action, "idempotency_key": idempotency_key, "input": payload.model_dump(mode="json")},
                timeout=30,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("ambiguous", "Activepieces timed out") from exc
        if response.status_code >= 400:
            raise classify_http(response.status_code, response.text)
        body = response.json()
        return ProviderResult(provider_object_id=body.get("object_id"), receipt=body, request_id=body.get("run_id"))

    def status(self, external_run_id: str) -> dict[str, Any]:
        return {"external_run_id": external_run_id, "status": "unknown"}

    def cancel(self, external_run_id: str) -> dict[str, Any]:
        return {"external_run_id": external_run_id, "cancelled": False, "reason": "not supported by webhook flows"}

    def receipts(self, external_run_id: str) -> list[dict[str, Any]]:
        return []


def get_backend(name: str) -> BackendAdapter:
    if name == PythonActionsAdapter.name:
        return PythonActionsAdapter()
    if name == ActivepiecesAdapter.name:
        return ActivepiecesAdapter(getattr(get_settings(), "activepieces_flow_url", None))
    raise GluonsError("VALIDATION_FAILED", f"Unknown execution backend: {name}", status=422)
