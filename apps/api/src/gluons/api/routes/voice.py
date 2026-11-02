"""Owner routing controls and separately authenticated provider callbacks."""
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from gluons.api.deps import PrincipalDep
from gluons.core import GluonsError
from gluons.db.models import Call
from gluons.db.session import tenant_tx
from gluons.telephony import gateway, provider, service

router = APIRouter(prefix="/v1", tags=["voice"])


class BeginRoute(BaseModel):
    mode: str
    number_hint: str | None = None
    country: str | None = None
    automation_id: str | None = None


class OrderIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,200}$")
    intent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    text: str = Field(min_length=1, max_length=20_000)


class KnowledgeQuery(BaseModel):
    query: str = Field(min_length=2, max_length=500)


@router.post("/phone-routes", status_code=201)
def begin(body: BeginRoute, principal: PrincipalDep) -> dict:
    return service.begin_route(principal, **body.model_dump())


@router.get("/phone-routes")
def routes(principal: PrincipalDep) -> dict:
    return {"routes": service.list_routes(principal)}


@router.get("/phone-routes/{route_id}")
def status(route_id: str, principal: PrincipalDep) -> dict:
    return service.route_status(principal, route_id)


@router.post("/phone-routes/{route_id}/start-test")
def start_test(route_id: str, principal: PrincipalDep) -> dict:
    return service.start_test(principal, route_id)


@router.post("/phone-routes/{route_id}/verify-test")
def verify_test(route_id: str, principal: PrincipalDep) -> dict:
    return service.verify_test_call(principal, route_id)


@router.delete("/phone-routes/{route_id}")
def disable(route_id: str, principal: PrincipalDep) -> dict:
    return service.disable_route(principal, route_id)


@router.post("/webhooks/elevenlabs")
async def post_call(request: Request, elevenlabs_signature: str = Header(default="")) -> dict:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 256_000:
            raise GluonsError("PAYLOAD_TOO_LARGE", "Voice event exceeds permitted size.", status=413)
    return gateway.post_call(bytes(data), elevenlabs_signature)


@router.post("/voice/{route_id}/order-intent")
def order_intent(route_id: str, body: OrderIntent, x_gluons_voice_secret: str = Header(default="")) -> dict:
    return gateway.submit_order(route_id, x_gluons_voice_secret, **body.model_dump())


@router.post("/voice/{route_id}/knowledge")
def knowledge(route_id: str, body: KnowledgeQuery, x_gluons_voice_secret: str = Header(default="")) -> dict:
    return gateway.business_knowledge(route_id, x_gluons_voice_secret, body.query)


@router.get("/calls")
def calls(principal: PrincipalDep) -> dict:
    principal.require("tasks:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        return {"calls": [{"id": c.id, "route_id": c.phone_route_id, "conversation_id": c.conversation_id,
                           "status": c.status, "duration_seconds": c.duration_seconds, "cost_usd": str(c.cost_usd) if c.cost_usd is not None else None,
                           "is_test": c.is_test, "run_id": c.run_id, "started_at": c.started_at.isoformat()}
                          for c in session.scalars(select(Call).order_by(Call.started_at.desc()).limit(200))]}


@router.post("/phone-routes/{route_id}/reconcile/{conversation_id}")
def reconcile(route_id: str, conversation_id: str, principal: PrincipalDep) -> dict:
    principal.require("phone:manage")
    route = service.route_status(principal, route_id)
    data = provider.read("conversations", conversation_id)
    if data.get("agent_id") != route["voice_agent_id"]:
        raise GluonsError("VOICE_EVIDENCE_MISMATCH", "This conversation belongs to another route.", status=403)
    return gateway.record_conversation(data)
