"""Durable launch planning with explicit provider and owner handoffs."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gluons import launch
from gluons.api.deps import IdempotencyKey, PrincipalDep, idempotent

router = APIRouter(prefix="/v1/business-launch/plans", tags=["business launch"])


class NewPlan(BaseModel):
    goal: str = Field(min_length=3, max_length=5000)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    product_category: str | None = Field(default=None, max_length=200)
    budget_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    fulfillment_model: str | None = None


class Versioned(BaseModel):
    expected_version: int = Field(ge=1)


class BindStore(Versioned):
    connection_id: str


@router.post("", status_code=201)
def create(body: NewPlan, principal: PrincipalDep, key: IdempotencyKey) -> dict:
    return idempotent(principal, key, {"operation": "launch.create", **body.model_dump()}, lambda: launch.create_plan(principal, **body.model_dump()))


@router.get("")
def list_plans(principal: PrincipalDep) -> dict:
    return {"plans": launch.list_plans(principal)}


@router.get("/{plan_id}")
def get(plan_id: str, principal: PrincipalDep) -> dict:
    return launch.get_plan(principal, plan_id)


@router.post("/{plan_id}/start")
def start(plan_id: str, body: Versioned, principal: PrincipalDep) -> dict:
    return launch.start(principal, plan_id, body.expected_version)


@router.post("/{plan_id}/pause")
def pause(plan_id: str, body: Versioned, principal: PrincipalDep) -> dict:
    return launch.pause(principal, plan_id, body.expected_version)


@router.post("/{plan_id}/resume")
def resume(plan_id: str, body: Versioned, principal: PrincipalDep) -> dict:
    return launch.resume(principal, plan_id, body.expected_version)


@router.post("/{plan_id}/cancel")
def cancel(plan_id: str, body: Versioned, principal: PrincipalDep) -> dict:
    return launch.cancel(principal, plan_id, body.expected_version)


@router.post("/{plan_id}/store")
def bind_store(plan_id: str, body: BindStore, principal: PrincipalDep) -> dict:
    return launch.bind_store(principal, plan_id, body.connection_id, body.expected_version)


@router.get("/{plan_id}/timeline")
def timeline(plan_id: str, principal: PrincipalDep) -> dict:
    return launch.timeline(principal, plan_id)


@router.get("/{plan_id}/evidence")
def evidence(plan_id: str, principal: PrincipalDep) -> dict:
    return launch.evidence(principal, plan_id)
