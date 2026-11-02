"""Whole-business deletion. A browser session only: no assistant token reaches this."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gluons import deletion
from gluons.api.deps import PrincipalDep

router = APIRouter(prefix="/v1/organizations/current", tags=["data"])


class DeleteRequest(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=200)
    export_acknowledged: bool = False


@router.post("/delete-request", status_code=202)
def delete_request(body: DeleteRequest, principal: PrincipalDep) -> dict[str, Any]:
    return deletion.request_deletion(principal, confirm_name=body.confirm_name, export_acknowledged=body.export_acknowledged)
