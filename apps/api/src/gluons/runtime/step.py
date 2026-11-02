"""What a step sees and returns. Primitives and skill domain code depend only on this."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gluons import gateway
from gluons.gateway import ActionRequest, ActionResult


@dataclass
class StepOutcome:
    # next | wait_approval | wait_external | retry | reconcile | complete | fail
    kind: str = "next"
    output: dict[str, Any] = field(default_factory=dict)
    branch: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    retry_after: float | None = None
    model_identifier: str | None = None


@dataclass
class StepContext:
    tenant_id: str
    run_id: str
    automation_id: str
    skill_id: str
    skill_version: str
    skill_actions: tuple[str, ...]
    mode: str
    node_id: str
    step_id: str
    policy_id: str | None
    source_event_id: str
    input: dict[str, Any]
    state: dict[str, Any]
    config: dict[str, Any]
    connections: dict[str, str]
    actor: str = "runtime"
    transport: Any = None

    def idempotency_key(self, suffix: str | None = None) -> str:
        return f"{self.tenant_id}:{self.source_event_id}:{suffix or self.node_id}"

    def action(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        capability: str | None = None,
        suffix: str | None = None,
        title: str | None = None,
        summary: str = "",
        evidence: tuple[Any, ...] = (),
        resources: dict[str, Any] | None = None,
    ) -> ActionResult:
        spec_capability = capability
        if spec_capability is None:
            from gluons.actions import get_action

            spec = get_action(name)
            spec_capability = spec.capability if spec else None
        return gateway.execute(
            ActionRequest(
                tenant_id=self.tenant_id,
                action=name,
                input=payload,
                idempotency_key=self.idempotency_key(suffix),
                mode=self.mode,
                actor=self.actor,
                automation_id=self.automation_id,
                run_id=self.run_id,
                step_id=self.step_id,
                node_id=self.node_id,
                skill_id=self.skill_id,
                connection_id=self.connections.get(spec_capability or ""),
                pinned_policy_id=self.policy_id,
                skill_actions=self.skill_actions,
                title=title,
                summary=summary,
                source_evidence=evidence,
                resources=resources or {},
                transport=self.transport,
            )
        )


def outcome_from_action(result: ActionResult, *, on_success_branch: str | None = None) -> StepOutcome:
    """Maps a gateway result onto engine behaviour, so every skill reacts the same way."""
    data = {
        "action_status": result.status,
        "verified": result.verified,
        "provider_object_id": result.provider_object_id,
        "receipt_id": result.receipt_id,
        "approval_id": result.approval_id,
        "reasons": list(result.reasons),
    }
    if result.status == "success":
        return StepOutcome(output=data, branch=on_success_branch)
    if result.status == "preview":
        return StepOutcome(output={**data, "preview": result.preview}, branch="previewed")
    if result.status == "waiting_approval":
        return StepOutcome(kind="wait_approval", output=data)
    if result.status == "retry":
        return StepOutcome(kind="retry", output=data, retry_after=result.retry_after, error=",".join(result.reasons))
    if result.status == "waiting_external":
        return StepOutcome(kind="wait_external", output=data, error=",".join(result.reasons))
    if result.status == "denied":
        return StepOutcome(output=data, branch="denied")
    if result.status in ("mismatch", "uncertain"):
        return StepOutcome(kind="reconcile", output=data, error=",".join(result.reasons) or result.status)
    return StepOutcome(kind="fail", output=data, error=",".join(result.reasons) or "action failed")
