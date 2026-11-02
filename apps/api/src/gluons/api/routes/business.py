"""Knowledge, trials, and evidence-based reports for owner and supported assistants."""
from __future__ import annotations
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from gluons import knowledge, reports, trials
from gluons.api.deps import PrincipalDep

router = APIRouter(prefix="/v1", tags=["business"])


class DocumentBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=100_000)
    kind: str = Field(default="note", min_length=1, max_length=40)
    source: str | None = Field(default=None, max_length=2000)
    audience: list[Literal["voice"]] = Field(default_factory=list, max_length=1)


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


@router.get("/trials")
def list_trials(principal: PrincipalDep) -> dict:
    return {"trials": trials.list_all(principal)}


@router.get("/trials/{trial_id}/results")
def trial_report(trial_id: str, principal: PrincipalDep) -> dict:
    return trials.get_report(principal, trial_id)


@router.post("/trials/{trial_id}/complete")
def complete_trial(trial_id: str, principal: PrincipalDep) -> dict:
    return trials.complete(principal, trial_id)
