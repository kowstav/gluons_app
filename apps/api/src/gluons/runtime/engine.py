"""Advances one run through its skill graph, one checkpointed step at a time.

A run holds a lease while a worker owns it. Every step commits its output and the next
cursor before the following step starts, so a worker can die at any point and another
resumes from the last checkpoint. Duplicate writes are prevented by the gateway's
write-ahead receipts, not by hoping a step ran only once. No business process waits
inside an HTTP handler or depends on an open chat.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from gluons import audit, events, queue, run_log
from gluons.config import get_settings
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import Automation, Connection, Incident, Organization, Run, StepRun
from gluons.db.session import tenant_tx
from gluons.net import SideEffectBlocked
from gluons.runtime.state import TERMINAL_RUN_STATES, transition_run
from gluons.runtime.step import StepContext, StepOutcome
from gluons.skills.contract import Node, SkillContract
from gluons.skills.registry import get_registry

MAX_STEP_ATTEMPTS = 6
MAX_STEPS_PER_SLICE = 100


@dataclass(frozen=True)
class AdvanceResult:
    status: str
    run_id: str


def _acquire_lease(tenant_id: str, run_id: str, worker_id: str) -> bool:
    now = utcnow()
    lease = now + timedelta(seconds=get_settings().worker_lease_seconds)
    with tenant_tx(tenant_id) as session:
        claimed = session.execute(
            update(Run)
            .where(
                Run.id == run_id,
                Run.status.not_in(tuple(TERMINAL_RUN_STATES)),
                (Run.lease_until.is_(None)) | (Run.lease_until < now) | (Run.leased_by == worker_id),
            )
            .values(leased_by=worker_id, lease_until=lease)
        )
        return claimed.rowcount == 1


def _release_lease(tenant_id: str, run_id: str, worker_id: str) -> None:
    with tenant_tx(tenant_id) as session:
        session.execute(
            update(Run).where(Run.id == run_id, Run.leased_by == worker_id).values(leased_by=None, lease_until=None)
        )


def _open_incident(session, run: Run, kind: str, summary: str, diagnosis: dict[str, Any], severity: str = "warning") -> None:
    session.add(
        Incident(
            id=new_id("inc"),
            tenant_id=run.tenant_id,
            kind=kind,
            severity=severity,
            run_id=run.id,
            automation_id=run.automation_id,
            summary=summary,
            diagnosis=diagnosis,
        )
    )
    events.notify(
        session,
        run.tenant_id,
        kind=kind,
        title=summary,
        severity="warning" if severity == "warning" else "critical",
        link=f"/tasks/{run.automation_id}/runs/{run.id}",
        resource_type="run",
        resource_id=run.id,
    )


def _control_gate(session, run: Run, automation: Automation, org: Organization) -> str | None:
    """Pause, cancel and the emergency stop are checked before every step."""
    if automation.status in ("cancel_requested", "cancelled") or run.status == "cancel_requested":
        transition_run(run, "cancelled")
        run.finished_at = utcnow()
        run_log.append(session, run.tenant_id, run.id, "run.cancelled", {"reason": "automation_cancelled"})
        return "cancelled"
    if automation.status == "paused" or org.emergency_stop_at is not None:
        if run.status != "paused":
            transition_run(run, "paused")
            run_log.append(
                session,
                run.tenant_id,
                run.id,
                "run.paused",
                {"reason": "emergency_stop" if org.emergency_stop_at else "automation_paused"},
            )
        return "paused"
    if automation.status == "waiting_external":
        if run.status != "waiting_external":
            transition_run(run, "waiting_external")
        return "waiting_external"
    return None


def _next_node(skill: SkillContract, node: Node, outcome: StepOutcome) -> str | None:
    if outcome.branch is not None and outcome.branch in node.branches:
        return node.branches[outcome.branch]
    return node.next


def _apply_outcome(session, run: Run, step: StepRun, node: Node, skill: SkillContract, outcome: StepOutcome) -> str | None:
    """Commits one step. Returns a stop reason, or None to continue with the next node."""
    now = utcnow()
    step.output = outcome.output
    step.model_identifier = outcome.model_identifier
    if outcome.state:
        run.state = {**run.state, **outcome.state}

    def finish_step(status: str) -> None:
        step.status = status
        step.finished_at = now

    if outcome.kind == "next":
        finish_step("completed")
        run_log.append(
            session,
            run.tenant_id,
            run.id,
            "step.completed",
            {"node": node.id, "primitive": node.primitive, "branch": outcome.branch},
        )
        target = _next_node(skill, node, outcome)
        if target is None:
            return _finish_run(session, run, {"outcome": "completed"})
        run.cursor = target
        return None

    if outcome.kind == "complete":
        finish_step("completed")
        run_log.append(session, run.tenant_id, run.id, "step.completed", {"node": node.id, "primitive": node.primitive})
        return _finish_run(session, run, outcome.output)

    if outcome.kind == "wait_approval":
        step.status = "waiting_approval"
        transition_run(run, "waiting_approval")
        return "waiting_approval"

    if outcome.kind == "wait_external":
        step.status = "waiting_external"
        step.error = outcome.error
        transition_run(run, "waiting_external")
        return "waiting_external"

    if outcome.kind == "reconcile":
        step.status = "reconciliation_required"
        step.error = outcome.error
        transition_run(run, "reconciliation_required")
        run.error = outcome.error
        _open_incident(
            session,
            run,
            "reconciliation_required",
            "A write needs a person to check it against the source system.",
            {"node": node.id, "error": outcome.error, "output": outcome.output},
            severity="critical",
        )
        return "reconciliation_required"

    if outcome.kind == "retry":
        step.attempts += 1
        step.error = outcome.error
        if step.attempts > MAX_STEP_ATTEMPTS:
            finish_step("failed")
            return _fail_run(session, run, f"{node.id} kept failing: {outcome.error}", node, outcome)
        transition_run(run, "retrying")
        delay = outcome.retry_after or min(5 * 2 ** (step.attempts - 2), 900)
        queue.enqueue(
            session,
            "run.advance",
            {"run_id": run.id},
            tenant_id=run.tenant_id,
            run_at=now + timedelta(seconds=delay),
            dedupe_key=f"run.advance:{run.id}",
        )
        run_log.append(
            session,
            run.tenant_id,
            run.id,
            "step.retry_scheduled",
            {"node": node.id, "attempt": step.attempts, "error": outcome.error, "delay_seconds": delay},
        )
        return "retrying"

    finish_step("failed")
    step.error = outcome.error
    return _fail_run(session, run, outcome.error or "step failed", node, outcome)


def _finish_run(session, run: Run, outcome: dict[str, Any]) -> str:
    transition_run(run, "completed")
    run.outcome = outcome
    run.finished_at = utcnow()
    run.cursor = None
    run_log.append(session, run.tenant_id, run.id, "run.completed", outcome)
    events.emit(session, run.tenant_id, "run.completed", {"run_id": run.id, "automation_id": run.automation_id})
    return "completed"


def _fail_run(session, run: Run, error: str, node: Node, outcome: StepOutcome) -> str:
    transition_run(run, "failed")
    run.error = error
    run.finished_at = utcnow()
    run_log.append(session, run.tenant_id, run.id, "run.failed", {"node": node.id, "error": error})
    _open_incident(
        session,
        run,
        "run_failed",
        "A task stopped and needs a person.",
        {"node": node.id, "error": error, "output": outcome.output},
    )
    return "failed"


def _resolve_connections(session, automation: Automation) -> dict[str, str]:
    mapping = dict(automation.config.get("connections") or {})
    if automation.source_connections:
        rows = session.execute(select(Connection).where(Connection.id.in_(automation.source_connections))).scalars()
        for connection in rows:
            mapping.setdefault(connection.metadata_json.get("capability") or connection.provider, connection.id)
    return mapping


def _begin_step(tenant_id: str, run_id: str, node: Node) -> StepRun:
    with tenant_tx(tenant_id) as session:
        step = session.execute(
            select(StepRun).where(StepRun.run_id == run_id, StepRun.node_id == node.id).with_for_update()
        ).scalar_one_or_none()
        if step is None:
            step = StepRun(id=new_id("stp"), tenant_id=tenant_id, run_id=run_id, node_id=node.id, primitive=node.primitive)
            session.add(step)
            session.flush()
        return step


def advance(tenant_id: str, run_id: str, worker_id: str, *, transport: Any = None) -> AdvanceResult:
    from gluons.skills.primitives import PRIMITIVES

    if not _acquire_lease(tenant_id, run_id, worker_id):
        return AdvanceResult("busy_or_finished", run_id)
    stopped: str | None = None
    try:
        for _ in range(MAX_STEPS_PER_SLICE):
            with tenant_tx(tenant_id) as session:
                run = session.get(Run, run_id, with_for_update=True)
                automation = session.get(Automation, run.automation_id)
                org = session.get(Organization, tenant_id)
                gate = _control_gate(session, run, automation, org)
                if gate is not None:
                    stopped = gate
                    break
                if run.status in TERMINAL_RUN_STATES:
                    stopped = run.status
                    break
                if run.started_at is None:
                    run.started_at = utcnow()
                transition_run(run, "running")
                run_log.append(session, tenant_id, run_id, "run.running", {"cursor": run.cursor}) if run.attempts == 0 else None
                run.attempts += 1
                skill = get_registry().get(run.skill_id, run.skill_version)
                cursor = run.cursor or skill.graph.entry
                run.cursor = cursor
                connections = _resolve_connections(session, automation)
                snapshot = (
                    dict(run.input),
                    dict(run.state),
                    run.mode,
                    run.policy_id,
                    run.source_event_id,
                    run.automation_id,
                    dict(automation.config),
                )

            node = skill.graph.nodes[cursor]
            step = _begin_step(tenant_id, run_id, node)
            if step.status == "completed":
                # A previous worker finished this step before dying. Resume past it.
                outcome = StepOutcome(output=step.output, branch=step.output.get("branch"))
            else:
                run_input, state, mode, policy_id, source_event_id, automation_id, config = snapshot
                context = StepContext(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    automation_id=automation_id,
                    skill_id=skill.id,
                    skill_version=skill.version,
                    skill_actions=tuple(skill.actions),
                    mode=mode,
                    node_id=node.id,
                    step_id=step.id,
                    policy_id=policy_id,
                    source_event_id=source_event_id,
                    input=run_input,
                    state=state,
                    config=config,
                    connections=connections,
                    transport=transport,
                )
                try:
                    outcome = PRIMITIVES[node.primitive](context, node)
                except SideEffectBlocked as exc:
                    # A code path tried to write without the gateway. Treat as a security fault.
                    outcome = StepOutcome(kind="fail", error=f"SIDE_EFFECT_BLOCKED: {exc}")
                except GluonsError as exc:
                    if exc.code == "BUDGET_EXCEEDED":
                        outcome = StepOutcome(kind="wait_external", error=exc.code)
                    else:
                        outcome = StepOutcome(kind="fail", error=f"{exc.code}: {exc.message}")
                except Exception as exc:  # noqa: BLE001 - any step failure must reach the retry path
                    outcome = StepOutcome(kind="retry", error=f"{type(exc).__name__}: {exc}")

            with tenant_tx(tenant_id) as session:
                run = session.get(Run, run_id, with_for_update=True)
                step = session.get(StepRun, step.id, with_for_update=True)
                # Persisted so a resumed worker can follow the same branch.
                if outcome.branch is not None:
                    outcome.output = {**outcome.output, "branch": outcome.branch}
                stopped = _apply_outcome(session, run, step, node, skill, outcome)
            if stopped is not None:
                break
    finally:
        _release_lease(tenant_id, run_id, worker_id)
    return AdvanceResult(stopped or "running", run_id)


def diagnose(tenant_id: str, run_id: str) -> dict[str, Any]:
    """Plain-language failure diagnosis built from recorded facts only."""
    with tenant_tx(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            raise GluonsError("NOT_FOUND", "Run not found.", status=404)
        steps = (
            session.execute(select(StepRun).where(StepRun.run_id == run_id).order_by(StepRun.started_at)).scalars().all()
        )
        failing = next((s for s in reversed(steps) if s.status not in ("completed", "running")), None)
        audit.record(
            session,
            tenant_id=tenant_id,
            category="run",
            event_type="run.diagnosed",
            actor="runtime",
            resource_type="run",
            resource_id=run_id,
        )
        return {
            "run_id": run_id,
            "status": run.status,
            "error": run.error,
            "stuck_at": failing.node_id if failing else run.cursor,
            "step_status": failing.status if failing else None,
            "step_error": failing.error if failing else None,
            "attempts": failing.attempts if failing else None,
            "completed_steps": [s.node_id for s in steps if s.status == "completed"],
        }
