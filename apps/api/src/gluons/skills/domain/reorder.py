"""Calculate reviewable reorder drafts from a business's saved rules and supplied counts."""
from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from gluons.core import validation_failed
from gluons.passport import SENSITIVE_MARKERS
from gluons.runtime.step import StepContext, StepOutcome
from gluons.skills.contract import Node
from gluons.skills.primitives import domain

SKILL_ID = "inventory_reorder"
Sku = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$")]
Quantity = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
PositiveQuantity = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
Label = Annotated[str, Field(min_length=1, max_length=100)]


class ReorderRule(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sku: Sku
    supplier: Label
    reorder_below: PositiveQuantity
    target: PositiveQuantity
    max_order: PositiveQuantity
    pack_size: PositiveQuantity = 1
    unit_cost_minor: int | None = Field(default=None, strict=True, ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def check_bounds(self) -> ReorderRule:
        if self.target < self.reorder_below or self.pack_size > self.max_order:
            raise ValueError("Set a target at or above the threshold, with one pack within the order limit.")
        if SENSITIVE_MARKERS.search(self.supplier):
            raise ValueError("Use the supplier's business name without credentials or sensitive details.")
        return self


class ReorderRules(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    stock_location: Label
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    items: list[ReorderRule] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def check_items(self) -> ReorderRules:
        if len({item.sku for item in self.items}) != len(self.items):
            raise ValueError("Configure each SKU once for this stock location.")
        if SENSITIVE_MARKERS.search(self.stock_location):
            raise ValueError("Use a stock location name without credentials or sensitive details.")
        return self


class StockCount(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sku: Sku
    available: Quantity
    on_order: Quantity


class StockSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    stock_location: Label
    observed_at: AwareDatetime
    basis: Literal["synthetic", "owner_supplied"]
    counts: list[StockCount] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def check_location(self) -> StockSnapshot:
        if SENSITIVE_MARKERS.search(self.stock_location):
            raise ValueError("Use a stock location name without credentials or sensitive details.")
        return self


def pin_case(config: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        rules = ReorderRules.model_validate(config.get("inventory_rules"))
        snapshot = StockSnapshot.model_validate(payload)
    except ValidationError:
        raise validation_failed("Save the reorder rules and supply valid stock counts before submitting a case.") from None
    return {"inventory_rules": rules.model_dump(mode="json")}, snapshot.model_dump(mode="json")


def evaluate(rules: ReorderRules, snapshot: StockSnapshot) -> dict[str, Any]:
    by_sku: dict[str, list[StockCount]] = defaultdict(list)
    for count in snapshot.counts:
        by_sku[count.sku].append(count)
    rows = []
    for rule in rules.items:
        counts = by_sku.get(rule.sku, [])
        row: dict[str, Any] = {"sku": rule.sku, "supplier": rule.supplier}
        if snapshot.stock_location != rules.stock_location:
            reason = "stock_location_mismatch"
        elif len(counts) != 1:
            reason = "duplicate_stock_count" if counts else "missing_stock_count"
        else:
            position = counts[0].available + counts[0].on_order
            row["stock_position"] = position
            if position >= rule.reorder_below:
                rows.append({**row, "status": "stock_sufficient"})
                continue
            shortfall = rule.target - position
            packs = (shortfall + rule.pack_size - 1) // rule.pack_size
            quantity = packs * rule.pack_size
            if quantity > rule.max_order:
                reason = "quantity_limit_exceeded"
            elif rule.unit_cost_minor is None:
                reason = "missing_price"
            else:
                rows.append({**row, "status": "draft", "quantity": quantity,
                             "estimated_cost_minor": quantity * rule.unit_cost_minor})
                continue
            row["suggested_quantity"] = quantity
        rows.append({**row, "status": "needs_review", "reason": reason})
    return {"rows": rows, "currency": rules.currency, "stock_location": rules.stock_location,
            "observed_at": snapshot.observed_at.isoformat(), "basis": snapshot.basis,
            "ignored_skus": sorted(by_sku.keys() - {rule.sku for rule in rules.items})}


@domain("reorder.evaluate")
def evaluate_step(ctx: StepContext, node: Node) -> StepOutcome:
    try:
        rules = ReorderRules.model_validate(ctx.state.get("inventory_rules"))
        snapshot = StockSnapshot.model_validate(ctx.input)
    except ValidationError:
        return StepOutcome(kind="fail", error="Configure valid reorder rules and supply a structured stock snapshot.")
    result = evaluate(rules, snapshot)
    return StepOutcome(output={"items_assessed": len(result["rows"]), "basis": snapshot.basis},
                       state={"inventory_assessment": result}, model_identifier="deterministic:inventory-reorder-v1")


@domain("reorder.group_drafts")
def group_drafts_step(ctx: StepContext, node: Node) -> StepOutcome:
    assessment = ctx.state["inventory_assessment"]
    suppliers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in assessment["rows"]:
        if row["status"] == "draft":
            suppliers[row["supplier"]].append(row)
    drafts = [{"supplier": supplier, "lines": items,
               "estimated_total_minor": sum(row["estimated_cost_minor"] for row in items)}
              for supplier, items in sorted(suppliers.items())]
    review_count = sum(row["status"] == "needs_review" for row in assessment["rows"])
    result = {**assessment, "drafts": drafts, "cost_basis": "configured_unit_estimates",
              "items_needing_review": review_count, "external_actions_performed": 0}
    return StepOutcome(output={"supplier_drafts": len(drafts)}, state={"reorder_review": result})
