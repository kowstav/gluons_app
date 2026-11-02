"""Explicit owner downloads; exports do not delete or revoke business data."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gluons import data_export
from gluons.api.deps import PrincipalDep

router = APIRouter(prefix="/v1/data-export", tags=["data export"])


class ExportPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=200, ge=1, le=1000)
    cursor: str | None = Field(default=None, max_length=2048)


@router.get("")
def manifest(principal: PrincipalDep) -> JSONResponse:
    return JSONResponse(data_export.manifest(principal), headers={"Cache-Control": "no-store"})


@router.post("/{dataset}")
def export(dataset: str, body: ExportPage, principal: PrincipalDep) -> JSONResponse:
    result = data_export.export_page(principal, dataset, cursor=body.cursor, limit=body.limit)
    return JSONResponse(result, headers={"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="gluons-{result["dataset"]}.json"'})
