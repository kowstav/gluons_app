"""TEST DOUBLE. A connector that records calls. Proves Gluons' gate behaviour only.

Nothing tested through this double says anything about a real provider integration.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from gluons.actions import (
    ActionSpec,
    ConnectorContext,
    ProviderError,
    ProviderResult,
    Readback,
    get_action,
    register_action,
    register_connector,
)
from gluons.core import new_id
from gluons.net import GuardedClient


class TestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = "x"
    recipient: str | None = None
    amount_minor: int | None = None


@dataclass
class RecordingConnector:
    writes: list[str] = field(default_factory=list)
    previews: int = 0
    objects: dict[str, str] = field(default_factory=dict)
    fail_with: ProviderError | None = None
    misreport: bool = False
    unsafe_preview: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def client(self, ctx: ConnectorContext) -> GuardedClient:
        return GuardedClient(("provider.test",))

    def prepare(self, ctx: ConnectorContext, payload: BaseModel) -> dict[str, Any]:
        self.previews += 1
        if self.unsafe_preview:
            self.client(ctx).post("https://provider.test/write")
        return {"preview": payload.model_dump()}

    def execute(self, ctx: ConnectorContext, payload: BaseModel, idempotency_key: str) -> ProviderResult:
        if self.fail_with is not None:
            error, self.fail_with = self.fail_with, None
            raise error
        with self._lock:
            object_id = new_id("ext")
            self.writes.append(idempotency_key)
            self.objects[idempotency_key] = object_id
        return ProviderResult(provider_object_id=object_id, receipt={"object": object_id}, cost_usd=0.01)

    def lookup(self, ctx: ConnectorContext, payload: BaseModel, idempotency_key: str) -> ProviderResult | None:
        object_id = self.objects.get(idempotency_key)
        return ProviderResult(provider_object_id=object_id) if object_id else None

    def verify(self, ctx: ConnectorContext, payload: BaseModel, result: ProviderResult) -> Readback:
        if self.misreport:
            return Readback(found=True, matches=False, mismatches=("value differs",))
        return Readback(found=True, matches=True, record={"object": result.provider_object_id})


CONNECTOR = RecordingConnector()


def _spec(name: str, permission: str, tier: str, **kwargs: Any) -> None:
    if get_action(name) is None:
        register_action(
            ActionSpec(
                name=name,
                permission=permission,
                risk_tier=tier,
                capability="testcap",
                input_model=TestPayload,
                description=f"Test action {name}",
                required_scopes={"testprov": ("write",)},
                amount_minor=lambda p: p.amount_minor,
                resources=lambda p: {"recipient": p.recipient},
                **kwargs,
            )
        )
    register_connector("testprov", name, CONNECTOR)


def install() -> RecordingConnector:
    _spec("test.write", "send", "R2", irreversible=True)
    _spec("test.spend", "purchase", "R3")
    _spec("test.regulated", "file_return", "R4")
    CONNECTOR.writes.clear()
    CONNECTOR.objects.clear()
    CONNECTOR.previews = 0
    CONNECTOR.fail_with = None
    CONNECTOR.misreport = False
    CONNECTOR.unsafe_preview = False
    return CONNECTOR
