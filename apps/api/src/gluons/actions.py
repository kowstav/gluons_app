"""Registry of every side effect Gluons can perform, and the connector contract.

Action names and schemas are an allowlist. A skill can only use actions it declares; a
model can only propose them; nothing supplies an arbitrary URL or shell command. Adding
an external capability means adding a typed entry here plus a connector, which is the
only way code reaches the gateway.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from gluons.net import GuardedClient


class ProviderError(Exception):
    """Classification decides what is safe to do next.

    retryable: the provider did not act (429, 503 before processing). Retry after backoff.
    ambiguous: the write may have landed (timeout, connection reset). Never reissue blindly;
               look it up first.
    auth:      credentials were rejected or a scope is gone. Stop and ask the owner.
    permanent: the provider refused the request. Retrying cannot help.
    """

    def __init__(self, kind: str, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after


def classify_http(status: int, body: str = "") -> ProviderError:
    if status in (401, 403):
        return ProviderError("auth", f"provider refused credentials ({status})", status=status)
    if status == 429:
        return ProviderError("retryable", "provider rate limited the request", status=status)
    if status in (502, 503):
        return ProviderError("retryable", f"provider unavailable ({status})", status=status)
    if status == 504 or status >= 500:
        return ProviderError("ambiguous", f"provider failed mid-request ({status})", status=status)
    return ProviderError("permanent", f"provider rejected the request ({status}): {body[:200]}", status=status)


@dataclass(frozen=True)
class ProviderResult:
    provider_object_id: str | None
    receipt: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    cost_usd: float | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Readback:
    """An independent read of the source of truth. Only this can set `verified`."""

    found: bool
    matches: bool
    record: dict[str, Any] = field(default_factory=dict)
    mismatches: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorContext:
    tenant_id: str
    connection_id: str | None
    provider: str | None
    environment: str
    external_account_id: str | None
    metadata: dict[str, Any]
    credentials: Callable[[], dict[str, Any]]
    # Set by tests to run against a local provider double.
    transport: Any = None


class ActionConnector(Protocol):
    def client(self, ctx: ConnectorContext) -> GuardedClient: ...

    def prepare(self, ctx: ConnectorContext, payload: BaseModel) -> dict[str, Any]:
        """Preview without side effects. Runs in shadow and replay, so it must only read."""

    def execute(self, ctx: ConnectorContext, payload: BaseModel, idempotency_key: str) -> ProviderResult: ...

    def lookup(self, ctx: ConnectorContext, payload: BaseModel, idempotency_key: str) -> ProviderResult | None:
        """Find an earlier attempt after an ambiguous failure. None means it did not land."""

    def verify(self, ctx: ConnectorContext, payload: BaseModel, result: ProviderResult) -> Readback: ...


class ActionSpec(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    permission: str  # read, draft, send, call, create_order, submit_order, refund, purchase, ...
    risk_tier: str = Field(pattern=r"^R[0-4]$")
    capability: str  # logical connector need: erp, email, messaging, storefront, ...
    input_model: type[BaseModel]
    description: str
    required_scopes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    irreversible: bool = False
    is_write: bool = True
    resources: Callable[[Any], dict[str, Any]] | None = None
    amount_minor: Callable[[Any], int | None] | None = None


_ACTIONS: dict[str, ActionSpec] = {}
_CONNECTORS: dict[tuple[str, str], ActionConnector] = {}


def register_action(spec: ActionSpec) -> ActionSpec:
    existing = _ACTIONS.get(spec.name)
    if existing is not None and existing.input_model is not spec.input_model:
        raise ValueError(f"action {spec.name} registered twice with different schemas")
    _ACTIONS[spec.name] = spec
    return spec


def get_action(name: str) -> ActionSpec | None:
    return _ACTIONS.get(name)


def all_actions() -> dict[str, ActionSpec]:
    return dict(_ACTIONS)


def register_connector(provider: str, action: str, connector: ActionConnector) -> None:
    _CONNECTORS[(provider, action)] = connector


def get_connector(provider: str, action: str) -> ActionConnector | None:
    return _CONNECTORS.get((provider, action))
