"""Shared execution primitives. Every skill graph is composed from these.

A skill never talks to a provider directly: its only route to a side effect is the
`action` primitive, which goes through the gateway. Domain logic lives in `domain`
functions, which return typed outcomes.
"""
from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from gluons import approvals, audit
from gluons.core import fingerprint, new_id
from gluons.db.models import ActionReceipt, Approval, Incident
from gluons.db.session import tenant_tx
from gluons.policy import NON_LIVE_MODES
from gluons.runtime.step import StepContext, StepOutcome, outcome_from_action
from gluons.security import scan_untrusted
from gluons.skills.contract import Node

Primitive = Callable[[StepContext, Node], StepOutcome]
DomainFn = Callable[[StepContext, Node], StepOutcome]

PRIMITIVES: dict[str, Primitive] = {}
DOMAIN_FUNCTIONS: dict[str, DomainFn] = {}
_domain_loaded = False


def primitive(name: str) -> Callable[[Primitive], Primitive]:
    def register(fn: Primitive) -> Primitive:
        PRIMITIVES[name] = fn
        return fn

    return register


def domain(name: str) -> Callable[[DomainFn], DomainFn]:
    def register(fn: DomainFn) -> DomainFn:
        if name in DOMAIN_FUNCTIONS and DOMAIN_FUNCTIONS[name] is not fn:
            raise ValueError(f"domain function {name} defined twice")
        DOMAIN_FUNCTIONS[name] = fn
        return fn

    return register


def load_domain() -> None:
    global _domain_loaded
    if _domain_loaded:
        return
    package = importlib.import_module("gluons.skills.domain")
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"gluons.skills.domain.{module.name}")
    _domain_loaded = True


def resolve_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


@primitive("capture_event")
def capture_event(ctx: StepContext, node: Node) -> StepOutcome:
    """Pins what arrived. Scanning flags hostile text for a person; it grants nothing."""
    text_fields = node.params.get("text_fields", ["text", "subject", "body"])
    text = "\n".join(str(ctx.input.get(f, "")) for f in text_fields)
    findings = scan_untrusted(text)
    state: dict[str, Any] = {
        "source_hash": fingerprint(ctx.input),
        "source_event_id": ctx.source_event_id,
        "injection_flags": findings,
    }
    if findings:
        with tenant_tx(ctx.tenant_id) as session:
            session.add(
                Incident(
                    id=new_id("inc"),
                    tenant_id=ctx.tenant_id,
                    kind="prompt_injection_suspected",
                    severity="warning",
                    run_id=ctx.run_id,
                    automation_id=ctx.automation_id,
                    summary="Incoming content tried to give instructions. It was treated as data.",
                    diagnosis={"flags": findings, "source_hash": state["source_hash"]},
                )
            )
    return StepOutcome(output={"source_hash": state["source_hash"], "flags": findings}, state=state)


@primitive("domain")
def run_domain(ctx: StepContext, node: Node) -> StepOutcome:
    load_domain()
    name = node.params["fn"]
    fn = DOMAIN_FUNCTIONS.get(name)
    if fn is None:
        return StepOutcome(kind="fail", error=f"unknown domain function {name}")
    return fn(ctx, node)


@primitive("human_review")
def human_review(ctx: StepContext, node: Node) -> StepOutcome:
    """A durable wait for a person. In shadow and replay it only records what would happen."""
    subject = resolve_path(ctx.state, node.params.get("subject", "")) if node.params.get("subject") else None
    reasons = ctx.state.get(node.params.get("reasons_from", "review_reasons"), [])
    if ctx.mode in NON_LIVE_MODES:
        return StepOutcome(
            output={"would_request_review": True, "reasons": reasons},
            branch="shadow",
            state={"would_escalate": True},
        )
    payload_hash = fingerprint({"run": ctx.run_id, "node": node.id, "subject": subject, "reasons": reasons})
    with tenant_tx(ctx.tenant_id) as session:
        found = session.execute(
            select(Approval)
            .where(Approval.run_id == ctx.run_id, Approval.node_id == node.id, Approval.kind == "review")
            .order_by(Approval.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if found is not None and found.status == "approved":
            return StepOutcome(
                output={"review": "approved", "approval_id": found.id},
                branch="approved",
                state={f"{node.id}_resolution": found.resolution or {}},
            )
        if found is not None and found.status in ("denied", "expired", "cancelled"):
            return StepOutcome(output={"review": found.status, "approval_id": found.id}, branch="denied")
        if found is None:
            found = approvals.request_approval(
                session,
                tenant_id=ctx.tenant_id,
                kind="review",
                action=node.params.get("action", "human.review"),
                risk_tier=node.params.get("risk_tier", "R1"),
                title=node.params.get("title", "A person needs to check this"),
                summary="; ".join(str(r) for r in reasons),
                payload_hash=payload_hash,
                run_id=ctx.run_id,
                automation_id=ctx.automation_id,
                node_id=node.id,
                records_after_preview=subject if isinstance(subject, dict) else {"subject": subject},
                source_evidence=[{"source_hash": ctx.state.get("source_hash")}],
                required_role=node.params.get("required_role", "approver"),
            )
    return StepOutcome(kind="wait_approval", output={"approval_id": found.id, "reasons": reasons})


@primitive("action")
def run_action(ctx: StepContext, node: Node) -> StepOutcome:
    params = node.params
    name = params.get("action") or ctx.state.get(params.get("action_from", ""))
    if name is None:
        return StepOutcome(kind="fail", error=f"{node.id} has no action to run")
    if name not in ctx.skill_actions:
        return StepOutcome(kind="fail", error=f"{name} is not declared by this skill")
    payload = resolve_path(ctx.state, params["input_from"])
    if payload is None:
        return StepOutcome(kind="fail", error=f"{node.id} has no input at {params['input_from']}")
    resources = resolve_path(ctx.state, params["resources_from"]) if params.get("resources_from") else None
    result = ctx.action(
        name,
        payload,
        title=params.get("title"),
        summary=str(resolve_path(ctx.state, params["summary_from"]) or "") if params.get("summary_from") else "",
        evidence=({"source_hash": ctx.state.get("source_hash")},),
        resources=resources,
    )
    outcome = outcome_from_action(result)
    if result.status == "success":
        outcome.state = {
            params.get("store_as", node.id): {
                "provider_object_id": result.provider_object_id,
                "receipt_id": result.receipt_id,
                "verified": result.verified,
            }
        }
    if outcome.branch == "denied" and "denied" not in node.branches:
        # An unhandled denial must stop the run, never fall through to the next step.
        return StepOutcome(kind="fail", output=outcome.output, error=f"POLICY_DENIED: {','.join(result.reasons)}")
    return outcome


@primitive("complete")
def complete(ctx: StepContext, node: Node) -> StepOutcome:
    """Completion is earned: required writes must have a verified receipt."""
    simulated = ctx.mode in NON_LIVE_MODES
    output: dict[str, Any] = {
        "outcome": node.params.get("outcome", "completed"),
        "simulated": simulated,
        "mode": ctx.mode,
        "would_escalate": bool(ctx.state.get("would_escalate")),
    }
    required = node.params.get("requires_verified", [])
    if required and not simulated:
        with tenant_tx(ctx.tenant_id) as session:
            rows = session.execute(
                select(ActionReceipt).where(ActionReceipt.run_id == ctx.run_id, ActionReceipt.action.in_(required))
            ).scalars()
            verified = {r.action for r in rows if r.verified}
        unverified = [a for a in required if a not in verified]
        if unverified:
            return StepOutcome(kind="reconcile", output=output, error=f"unverified: {','.join(unverified)}")
    for key in node.params.get("report", []):
        output[key] = ctx.state.get(key)
    return StepOutcome(kind="complete", output=output)


@primitive("escalate")
def escalate(ctx: StepContext, node: Node) -> StepOutcome:
    """Ends the run with a person holding the case, and states that honestly."""
    reasons = ctx.state.get(node.params.get("reasons_from", "review_reasons"), [])
    if ctx.mode not in NON_LIVE_MODES:
        with tenant_tx(ctx.tenant_id) as session:
            session.add(
                Incident(
                    id=new_id("inc"),
                    tenant_id=ctx.tenant_id,
                    kind=node.params.get("kind", "human_escalation"),
                    severity=node.params.get("severity", "warning"),
                    run_id=ctx.run_id,
                    automation_id=ctx.automation_id,
                    summary=node.params.get("summary", "This case needs a person."),
                    diagnosis={"reasons": reasons},
                )
            )
            audit.record(
                session,
                tenant_id=ctx.tenant_id,
                category="run",
                event_type="run.escalated",
                actor="runtime",
                resource_type="run",
                resource_id=ctx.run_id,
                data={"reasons": reasons},
            )
    return StepOutcome(
        kind="complete",
        output={
            "outcome": "escalated_to_human",
            "reasons": reasons,
            "simulated": ctx.mode in NON_LIVE_MODES,
            "mode": ctx.mode,
        },
    )
