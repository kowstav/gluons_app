"""Knowledge, trials, and evidence-based reports for owner and supported assistants."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from gluons import knowledge, reports, trials
from gluons.api.deps import PrincipalDep
from gluons.mcp import operations

router = APIRouter(prefix="/v1", tags=["business"])


class DocumentBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=100_000)
    kind: str = Field(default="note", min_length=1, max_length=40)
    source: str | None = Field(default=None, max_length=2000)
    audience: list[Literal["voice"]] = Field(default_factory=list, max_length=1)


class TrialConfiguration(BaseModel):
    pass_criteria: dict[str, Any] | None = None
    expected_outcomes: dict[str, Any] | None = None
    baseline: dict[str, Any] | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=trials.MAX_TRIAL_DAYS)


class SupervisedTrial(BaseModel):
    expected_version: int
    comparator: dict[str, Any] = Field(default_factory=dict)
    baseline: dict[str, Any] = Field(default_factory=dict)
    budget_usd: float | None = Field(default=None, ge=0)
    expires_in_days: int | None = Field(default=None, ge=1, le=trials.MAX_TRIAL_DAYS)


class Measurement(BaseModel):
    metric: str = Field(min_length=1, max_length=100)
    value: float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=40)
    basis: Literal["measured", "estimated", "unverified"]
    source: str = Field(min_length=1, max_length=2000)
    run_id: str | None = None


class Budget(BaseModel):
    monthly_budget_usd: float = Field(ge=0)
    automation_id: str | None = None
    expected_version: int | None = None


@router.get("/knowledge/documents")
def documents(principal: PrincipalDep) -> dict:
    return {"documents": knowledge.list_documents(principal)}


@router.post("/knowledge/documents", status_code=201)
def add_document(body: DocumentBody, principal: PrincipalDep) -> dict:
    return knowledge.add_document(principal, **body.model_dump())


@router.get("/reports/operational")
def operational(principal: PrincipalDep, period_days: int = Query(default=30, ge=1, le=366)) -> dict:
    return reports.operational(principal, period_days=period_days)


@router.get("/reports/roi")
def roi(principal: PrincipalDep, automation_id: str | None = None) -> dict:
    return reports.roi(principal, automation_id)


@router.get("/reports/anomalies")
def anomalies(principal: PrincipalDep) -> dict:
    return {"anomalies": reports.list_anomalies(principal)}


@router.post("/reports/anomalies/scan")
def scan_anomalies(principal: PrincipalDep) -> dict:
    return reports.scan_anomalies(principal)


@router.get("/trials")
def list_trials(principal: PrincipalDep) -> dict:
    return {"trials": trials.list_all(principal)}


@router.get("/trials/{trial_id}/results")
def trial_report(trial_id: str, principal: PrincipalDep) -> dict:
    return trials.get_report(principal, trial_id)


@router.patch("/trials/{trial_id}")
def configure_trial(trial_id: str, body: TrialConfiguration, principal: PrincipalDep) -> dict:
    return trials.configure(principal, trial_id, **body.model_dump())


@router.post("/trials/{trial_id}/measurements", status_code=201)
def record_measurement(trial_id: str, body: Measurement, principal: PrincipalDep) -> dict:
    return trials.record_measurement(principal, trial_id, **body.model_dump())


@router.post("/trials/{trial_id}/complete")
def complete_trial(trial_id: str, principal: PrincipalDep) -> dict:
    return trials.complete(principal, trial_id)


@router.post("/automations/{automation_id}/supervised-trial", status_code=201)
def supervised_trial(automation_id: str, body: SupervisedTrial, principal: PrincipalDep) -> dict:
    return trials.start_supervised(principal, automation_id, body.expected_version, comparator=body.comparator, baseline=body.baseline,
                                   budget_usd=body.budget_usd, expires_in_days=body.expires_in_days)


@router.get("/skills/{skill_id}/cost-estimate", tags=["skills"])
def cost_estimate(skill_id: str, principal: PrincipalDep) -> dict:
    return operations.estimate_cost(principal, skill_id)


@router.get("/skills/{skill_id}/missing-connections", tags=["skills"])
def skill_missing_connections(skill_id: str, principal: PrincipalDep) -> dict:
    return operations.missing_connections(principal, skill_id=skill_id)


@router.get("/automations/{automation_id}/missing-connections")
def automation_missing_connections(automation_id: str, principal: PrincipalDep) -> dict:
    return operations.missing_connections(principal, automation_id=automation_id)


@router.get("/automations/{automation_id}/policy-preview")
def policy_preview(automation_id: str, principal: PrincipalDep) -> dict:
    return operations.policy_preview(principal, automation_id)


@router.get("/automations/{automation_id}/policy-status")
def policy_status(automation_id: str, principal: PrincipalDep) -> dict:
    return operations.policy_status(principal, automation_id)


@router.post("/budgets")
def set_budget(body: Budget, principal: PrincipalDep) -> dict:
    return operations.set_budget(principal, body.monthly_budget_usd, automation_id=body.automation_id, expected_version=body.expected_version)
