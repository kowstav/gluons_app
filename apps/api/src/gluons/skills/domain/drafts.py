"""Evidence-bound preparation for workflows whose provider integrations are unfinished.

These functions do useful local validation and arithmetic. They do not certify external
facts or pretend to carry out the blueprint's later steps. The output is a review packet
with an explicit checklist of work still pending. No model or network is used here.
"""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gluons import events
from gluons.core import fingerprint
from gluons.db.session import tenant_tx
from gluons.runtime.step import StepContext, StepOutcome
from gluons.security import scan_untrusted
from gluons.skills.contract import Node
from gluons.skills.domain import constraints
from gluons.skills.primitives import domain


def field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


class MoneyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    entity: str = Field(min_length=1, max_length=200)
    amount_minor: int = Field(strict=True)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    invoice_id: str | None = None


class Ingredient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=100)
    demand: Decimal = Field(ge=0, allow_inf_nan=False)
    safety_stock: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)
    usable_stock: Decimal = Field(ge=0, allow_inf_nan=False)
    on_order: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)
    unit_cost_minor: int = Field(ge=0, strict=True)
    unit: str = Field(min_length=1, max_length=40)


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier: str = Field(min_length=1, max_length=200)
    part_number: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0, strict=True)
    unit_cost_minor: int = Field(ge=0, strict=True)
    shipping_minor: int = Field(default=0, ge=0, strict=True)
    duties_minor: int = Field(default=0, ge=0, strict=True)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    minimum_quantity: int = Field(default=1, gt=0, strict=True)
    lead_time_days: int = Field(ge=0, strict=True)
    source_ref: str = Field(min_length=1, max_length=500)


def reconcile(facts: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Exact entity/currency/amount matches only. Never initiate or post a payment."""
    if "transactions" not in facts or "invoices" not in facts:
        return {}, []
    transactions = [MoneyRecord.model_validate(r) for r in facts["transactions"]]
    invoices = [MoneyRecord.model_validate(r) for r in facts["invoices"]]
    reasons: list[str] = []
    counts = Counter(r.id for r in transactions)
    duplicate_invoices = {key for key, count in Counter(r.id for r in invoices).items() if count > 1}
    used: set[str] = set()
    matches: list[dict[str, str]] = []
    unresolved: list[str] = []
    for transaction in transactions:
        candidates = [
            invoice for invoice in invoices
            if invoice.id == transaction.invoice_id
            and invoice.entity == transaction.entity
            and invoice.currency == transaction.currency
            and invoice.amount_minor == transaction.amount_minor
        ]
        if counts[transaction.id] != 1 or len(candidates) != 1:
            unresolved.append(transaction.id)
            continue
        candidate = candidates[0]
        if candidate.id in used or candidate.id in duplicate_invoices:
            unresolved.append(transaction.id)
            continue
        used.add(candidate.id)
        matches.append({"transaction_id": transaction.id, "invoice_id": candidate.id})
    if unresolved:
        reasons.append("Unmatched, duplicated or ambiguous transactions require an accountant.")
    return {"proposed_matches": matches, "unresolved_transaction_ids": unresolved, "posted_entries": 0}, reasons


def inventory(facts: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if "ingredients" not in facts:
        return {}, []
    ingredients = [Ingredient.model_validate(r) for r in facts["ingredients"]]
    if len({r.sku for r in ingredients}) != len(ingredients):
        return {}, ["Duplicate ingredient SKUs require review before calculating purchasing requirements."]
    recommendations = []
    for ingredient in ingredients:
        needed = max(Decimal(0), ingredient.demand + ingredient.safety_stock - ingredient.usable_stock - ingredient.on_order)
        # Fractional minor currency is retained for review; the purchasing system must
        # apply its own rounding and pack-size policy before any purchase.
        recommendations.append({
            "sku": ingredient.sku, "quantity": str(needed), "unit": ingredient.unit,
            "estimated_cost_minor": str(needed * ingredient.unit_cost_minor),
        })
    return {"purchase_recommendations": recommendations, "basis": "owner_supplied_demand_estimate", "purchase_orders_created": 0}, []


def compare_quotes(facts: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if "quotes" not in facts:
        return {}, []
    quotes = [Quote.model_validate(r) for r in facts["quotes"]]
    requested_part = facts.get("requested_part_number")
    reasons = []
    results = []
    for quote in quotes:
        eligible = quote.quantity >= quote.minimum_quantity and (requested_part is None or quote.part_number == requested_part)
        if not eligible:
            reasons.append(f"Quote from {quote.supplier} has a part-number or minimum-order mismatch.")
        results.append({
            **quote.model_dump(), "eligible_for_review": eligible,
            "landed_cost_minor": quote.quantity * quote.unit_cost_minor + quote.shipping_minor + quote.duties_minor,
            "supplier_reliability": "unverified", "purchase_authorized": False,
        })
    results.sort(key=lambda r: (r["currency"], not r["eligible_for_review"], r["landed_cost_minor"]))
    return {"quote_comparison": results, "currency_conversion_performed": False}, reasons


def dispatch(facts: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if "dispatch_problem" not in facts:
        return {}, []
    from gluons.skills.domain.dispatch import optimize

    candidate = optimize(facts["dispatch_problem"])
    reasons = []
    if candidate["status"] != "candidate_for_review" or candidate.get("unassigned_job_ids"):
        reasons.append("Dispatch needs human review: an emergency, missing optimizer or unassigned work remains.")
    return candidate, reasons


CALCULATORS = {
    "bookkeeping_reconciliation": reconcile,
    "accounting_practice_operations": reconcile,
    "restaurant_inventory_planning": inventory,
    "hardware_procurement": compare_quotes,
    "home_service_dispatch": dispatch,
    "field_service_opportunity_detection": constraints.field_opportunities,
}


def prepare(skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic preparation packet, preserving source hashes, not secrets."""
    from gluons.skills.registry import get_registry

    skill = get_registry().loaded(skill_id)
    contract = skill.contract
    errors = sorted(Draft202012Validator(contract.inputs).iter_errors(payload), key=lambda error: str(list(error.path)))
    reasons = [f"Input at {'.'.join(str(p) for p in error.path) or 'root'} fails {error.validator} validation." for error in errors]
    facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
    requirements = skill.blueprint.get("required_data", [])
    missing = [label for label in requirements if facts.get(field_name(label)) in (None, "", [], {})]
    reasons.extend(f"Missing source data: {label}." for label in missing)
    text = str(payload.get("text", ""))
    # Scanning is an advisory signal. No returned text can change grants or actions:
    # draft-only contracts have an empty action allowlist and cannot activate live.
    flags = scan_untrusted(text + "\n" + str(facts))
    if flags:
        reasons.append("Untrusted source contains instruction or authority claims; review required.")
    source_refs = payload.get("source_evidence", [])
    if not source_refs:
        reasons.append("Source evidence references are required before this draft can be reviewed.")
    if contract.regulated:
        reasons.append("Licensed professional review is required; no regulated sign-off has occurred.")
    if skill.contract.slug == "workflow_discovery" and facts.get("explicit_observation_consent") is not True:
        reasons.append("Observation consent is absent. No monitoring or telemetry collection is permitted.")
    findings = constraints.evaluate(contract.slug, regulated=contract.regulated, text=text, facts=facts)
    reasons.extend(stop["reason"] for stop in findings.stops)
    calculation: dict[str, Any] = {}
    calculator = CALCULATORS.get(skill.contract.slug)
    if calculator and not errors:
        try:
            calculation, extra_reasons = calculator(facts)
            reasons.extend(extra_reasons)
        except (ValidationError, TypeError, ValueError, InvalidOperation):
            reasons.append("Structured calculation inputs are invalid; no arithmetic result was guessed.")
    digest = fingerprint({"skill_id": contract.slug, "version": contract.version, "payload": payload})
    return {
        "draft_id": f"draft_{digest[:24]}", "skill_id": contract.slug, "skill_version": contract.version,
        "execution_scope": "draft_only", "status": "needs_review" if reasons else "draft_prepared",
        "source_hash": fingerprint(payload), "source_evidence": source_refs,
        "supplied_fields": sorted(facts), "missing_data": missing, "review_reasons": reasons,
        "hard_constraints": {"checks": findings.checks, "stops": findings.stops, "preserved": findings.preserved},
        "escalation": findings.escalation,
        "injection_flags": flags, "invariants": skill.blueprint.get("invariants"),
        "pending_workflow": [{**step, "status": "not_executed"} for step in skill.blueprint.get("workflow", [])],
        "calculations": calculation, "external_actions_performed": 0, "verified_external_outcome": False,
        "metrics": {name: {"value": None, "basis": "unmeasured"} for name in contract.metrics},
    }


@domain("drafts.prepare")
def prepare_step(ctx: StepContext, node: Node) -> StepOutcome:
    packet = prepare(ctx.skill_id, ctx.input)
    if packet["escalation"]:
        # A hazard cannot wait in a review queue. This reaches the owner in Gluons in every
        # mode and contacts nobody outside it, so shadow still has zero external writes.
        with tenant_tx(ctx.tenant_id) as session:
            events.notify(session, ctx.tenant_id, kind="safety_escalation", title="Urgent: a person must respond now",
                          body=packet["escalation"]["instruction"], severity="action", link=f"/runs/{ctx.run_id}",
                          resource_type="run", resource_id=ctx.run_id)
    return StepOutcome(
        output=packet,
        state={"draft": packet, "review_reasons": packet["review_reasons"]},
        model_identifier="deterministic:draft-preparation-v1",
    )
