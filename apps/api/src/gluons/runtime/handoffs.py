"""Link a completed inventory trial to a separate, draft-only procurement trial."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from gluons import audit
from gluons.authz import Principal
from gluons.core import GluonsError, conflict, validation_failed
from gluons.db.models import Automation, Organization, Run
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle
from gluons.runtime.state import check_version
from gluons.skills.domain.reorder import SKILL_ID as INVENTORY
from gluons.skills.registry import get_registry

PROCUREMENT = "hardware_procurement"
LINKABLE = frozenset({"ready", "trial_shadow", "trial_replay", "paused"})


def _draft_skill(automation: Automation, skill_id: str) -> bool:
    if automation.skill_id != skill_id:
        return False
    try:
        contract = get_registry().get(automation.skill_id, automation.skill_version)
    except GluonsError as error:
        if error.code == "NOT_FOUND":
            return False
        raise
    return contract.execution_scope == "draft_only" and not contract.actions


def configure(principal: Principal, automation_id: str, target_id: str | None, expected_version: int) -> dict[str, Any]:
    principal.require("automations:write")
    principal.require("tasks:read")
    principal.require("trials:write")
    from gluons.assist import require_active

    require_active(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        source = lifecycle._load(session, automation_id, lock=True)
        check_version(source.version, expected_version)
        if not _draft_skill(source, INVENTORY):
            raise validation_failed("A trial handoff must start from an inventory reorder automation.")
        if target_id is not None:
            target = lifecycle._load(session, target_id)
            if not _draft_skill(target, PROCUREMENT):
                raise validation_failed("Select a hardware procurement draft automation for the handoff.")
            if source.status not in LINKABLE or target.status not in LINKABLE:
                raise conflict("Link automations that are ready, in trial, or paused.")
        if source.trial_handoff_target_id != target_id:
            source.trial_handoff_target_id = target_id
            source.version += 1
            audit.record(session, tenant_id=principal.tenant_id, category="automation",
                         event_type="automation.trial_handoff_changed", actor=principal, resource_type="automation",
                         resource_id=source.id, data={"trial_handoff_target_id": target_id})
        return lifecycle.serialize(source)


def on_completed(session: Session, run: Run, outcome: dict[str, Any]) -> dict[str, Any] | None:
    """Enqueue at most one destination run in the source completion transaction."""
    if run.skill_id != INVENTORY or run.mode not in lifecycle.TRIAL_MODES:
        return None
    source = session.get(Automation, run.automation_id)
    if source is None or source.trial_handoff_target_id is None:
        return None
    result: dict[str, Any] = {"target_automation_id": source.trial_handoff_target_id, "status": "skipped"}
    if source.mode != run.mode or source.status != lifecycle.TRIAL_MODES[run.mode]:
        return {**result, "reason": "source_trial_inactive"}
    org = session.get(Organization, run.tenant_id)
    if org is None or org.emergency_stop_at is not None or org.deleted_at is not None:
        return {**result, "reason": "business_stopped"}
    review = outcome.get("reorder_review")
    if not isinstance(review, dict) or review.get("items_needing_review") != 0:
        return {**result, "reason": "inventory_review_required"}
    if not review["drafts"]:
        return {**result, "reason": "no_reorder_drafts"}
    target = session.get(Automation, source.trial_handoff_target_id, with_for_update=True)
    if target is None or not _draft_skill(target, PROCUREMENT):
        return {**result, "reason": "target_unavailable"}
    if target.mode != run.mode or target.status != lifecycle.TRIAL_MODES[run.mode]:
        return {**result, "reason": "target_trial_inactive_or_mode_mismatch"}
    # Reorder amounts are estimates, so this handoff supplies purchasing needs and no quotes.
    bom = [{"part_number": line["sku"], "quantity": line["quantity"], "supplier": draft["supplier"],
            "estimated_total_minor": line["estimated_cost_minor"]}
           for draft in review["drafts"] for line in draft["lines"]]
    payload = {"facts": {"bom": bom, "currency": review["currency"], "inventory_basis": review["basis"],
                         "cost_basis": "configured_unit_estimates"}, "source_evidence": [run.id]}
    queued = lifecycle._insert_run(session, target, source=lifecycle.TRIAL_HANDOFF_SOURCE,
                                   source_event_id=f"{lifecycle.TRIAL_HANDOFF_EVENT_PREFIX}{run.id}",
                                   payload=payload, state={"source_trial_run_id": run.id},
                                   actor=lifecycle.TRIAL_HANDOFF_SOURCE, exact_retry=True)
    return {**result, "status": "queued", "run_id": queued["run_id"]}
