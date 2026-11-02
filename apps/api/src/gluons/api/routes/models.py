"""Reviewed model availability and quality-gated selection."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gluons import model_gateway
from gluons.api.deps import PrincipalDep

router = APIRouter(prefix="/v1", tags=["models"])


class ModelSelection(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    model_id: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)


class EvaluateModel(BaseModel):
    skill_id: str
    provider: str
    model_id: str


@router.get("/models")
def list_models(principal: PrincipalDep) -> dict:
    return {"models": model_gateway.list_models(principal)}


@router.post("/models/evaluate")
def evaluate_model(body: EvaluateModel, principal: PrincipalDep) -> dict:
    return model_gateway.evaluate_candidate(principal, body.skill_id, body.provider, body.model_id)


@router.post("/automations/{automation_id}/model")
def select_model(automation_id: str, body: ModelSelection, principal: PrincipalDep) -> dict:
    return model_gateway.set_skill_model(principal, automation_id, body.provider, body.model_id, body.expected_version)
