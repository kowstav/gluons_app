"""Owner browser passkey enrollment through the existing session and CSRF boundary."""
from typing import Any

from fastapi import APIRouter

from gluons import passkeys
from gluons.api.deps import SessionDep

router = APIRouter(prefix="/v1/auth/passkey", tags=["auth"])


@router.get("")
def status(view: SessionDep) -> dict[str, bool]:
    return passkeys.status(view)


@router.post("/enrollment/options")
def enrollment_options(view: SessionDep) -> dict[str, Any]:
    return passkeys.begin_enrollment(view)


@router.post("/enrollment/finish")
def finish_enrollment(body: passkeys.Proof, view: SessionDep) -> dict[str, bool]:
    passkeys.finish_enrollment(view, body)
    return {"enrolled": True}
