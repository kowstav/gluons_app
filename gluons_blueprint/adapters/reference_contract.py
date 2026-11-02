"""Reference contract for all future GitHub-wrapped Gluons adapters.

Not a vendor integration. This reference deliberately refuses external writes.
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Mode = Literal["historical_replay", "shadow", "supervised", "live"]

@dataclass(frozen=True)
class ActionRequest:
    tenant_id: str
    task_id: str
    skill_id: str
    action: str
    mode: Mode
    idempotency_key: str
    policy_version: int
    connection_id: str
    payload: dict[str, Any]
    approved_action_id: str | None = None

@dataclass(frozen=True)
class ActionResult:
    status: Literal["preview", "waiting_approval", "success", "failed", "uncertain"]
    verified: bool = False
    provider_object_id: str | None = None
    receipt: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

class Adapter(Protocol):
    async def prepare(self, request: ActionRequest) -> ActionResult: ...
    async def execute(self, request: ActionRequest) -> ActionResult: ...
    async def verify(self, request: ActionRequest, provider_object_id: str) -> ActionResult: ...

class ReferenceNoWriteAdapter:
    """Safe sample for API-contract tests; not a production implementation."""
    async def prepare(self, request: ActionRequest) -> ActionResult:
        if not request.tenant_id or not request.idempotency_key or request.policy_version < 1:
            return ActionResult(status="failed", warnings=("invalid request",))
        return ActionResult(status="preview", receipt={"action": request.action, "preview": True})

    async def execute(self, request: ActionRequest) -> ActionResult:
        if request.mode in ("historical_replay", "shadow"):
            return await self.prepare(request)
        if not request.approved_action_id:
            return ActionResult(status="waiting_approval", warnings=("no recorded approval",))
        return ActionResult(status="failed", warnings=("reference adapter has no vendor integration",))

    async def verify(self, request: ActionRequest, provider_object_id: str) -> ActionResult:
        return ActionResult(status="uncertain", warnings=("reference adapter cannot verify upstream state",))
