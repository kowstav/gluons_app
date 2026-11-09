"""Owner-controlled Business Passport facts."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from gluons import passport
from gluons.api.deps import PrincipalDep
from gluons.passport import PassportFields

router = APIRouter(prefix="/v1/business-passport", tags=["business-passport"])


class UpdatePassport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    fields: PassportFields


@router.get("")
def get_passport(principal: PrincipalDep) -> dict:
    return passport.get(principal)


@router.patch("")
def update_passport(body: UpdatePassport, principal: PrincipalDep) -> dict:
    return passport.update(principal, body.expected_version, body.fields.model_dump(exclude_unset=True, mode="json"))
