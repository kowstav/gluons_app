"""State machines from blueprint doc 01 and 02. An undeclared transition is an error."""
from __future__ import annotations

from gluons.core import conflict, conflict_version

AUTOMATION_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"configuring", "ready", "cancelled"}),
    "configuring": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"trial_replay", "trial_shadow", "active", "configuring", "cancelled"}),
    "trial_replay": frozenset({"trial_shadow", "awaiting_activation", "ready", "paused", "cancel_requested"}),
    "trial_shadow": frozenset({"awaiting_activation", "ready", "paused", "cancel_requested"}),
    "awaiting_activation": frozenset({"active", "trial_shadow", "trial_replay", "ready", "paused", "cancel_requested"}),
    "active": frozenset(
        {"waiting_external", "paused", "reconciliation_required", "cancel_requested", "failed", "completed", "awaiting_activation"}
    ),
    "waiting_external": frozenset({"active", "paused", "cancel_requested"}),
    "paused": frozenset({"active", "trial_shadow", "trial_replay", "awaiting_activation", "cancel_requested"}),
    "reconciliation_required": frozenset({"active", "paused", "cancel_requested"}),
    "cancel_requested": frozenset({"cancelled"}),
    "failed": frozenset({"ready", "cancel_requested"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}

RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "paused", "cancelled"}),
    "running": frozenset(
        {
            "waiting_approval",
            "waiting_external",
            "retrying",
            "paused",
            "reconciliation_required",
            "completed",
            "failed",
            "cancelled",
        }
    ),
    "waiting_approval": frozenset({"running", "paused", "cancelled", "failed"}),
    "waiting_external": frozenset({"running", "paused", "cancelled", "failed"}),
    "retrying": frozenset({"running", "paused", "cancelled", "failed"}),
    "paused": frozenset({"running", "cancelled"}),
    "reconciliation_required": frozenset({"running", "completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

TERMINAL_RUN_STATES = frozenset({"completed", "failed", "cancelled"})
ACCEPTS_EVENTS = frozenset({"trial_replay", "trial_shadow", "active"})


def check_version(current: int, expected: int | None) -> None:
    if expected is not None and current != expected:
        raise conflict_version(current)


def transition_automation(automation, target: str) -> None:
    if target not in AUTOMATION_TRANSITIONS.get(automation.status, frozenset()):
        raise conflict(f"An automation cannot go from {automation.status} to {target}.")
    automation.status = target
    automation.version += 1


def transition_run(run, target: str) -> None:
    if run.status == target:
        return
    if target not in RUN_TRANSITIONS.get(run.status, frozenset()):
        raise conflict(f"A run cannot go from {run.status} to {target}.")
    run.status = target
    run.version += 1
