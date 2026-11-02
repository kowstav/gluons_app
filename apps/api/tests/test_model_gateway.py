"""Provider-double contract tests; these do not verify live provider integration."""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update

from gluons import model_gateway
from gluons.config import get_settings
from gluons.core import GluonsError, new_id
from gluons.db.models import BudgetHold, ModelDescriptor, ModelEvalResult, Organization, UsageEvent
from gluons.db.session import system_tx, tenant_tx
from gluons.models import providers


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GLUONS_OPENAI_API_KEY", "provider-double-only")
    monkeypatch.setenv("GLUONS_ANTHROPIC_API_KEY", "provider-double-only")
    monkeypatch.setenv("GLUONS_GOOGLE_GENAI_PROJECT", "test-project")
    monkeypatch.setenv("GLUONS_PRIVATE_LLM_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setattr(providers, "_google_authorization", lambda: "Bearer provider-double-adc")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def register(purpose, provider="openai", *, passed=True, price="2"):
    model_id = new_id("model")
    with system_tx() as session:
        session.add(ModelDescriptor(id=new_id("mdl"), provider=provider, model_id=model_id, tier="test_only",
                                    capabilities=["text", "structured_output"], input_usd_per_mtok=Decimal(price),
                                    output_usd_per_mtok=Decimal("10"), cache_read_usd_per_mtok=Decimal("1"),
                                    price_source="provider-double fixture, no live price claim"))
        if passed is not None:
            session.add(ModelEvalResult(id=new_id("eval"), skill_id=purpose, provider=provider, model_id=model_id,
                                        suite_hash="test-only", score=Decimal("1") if passed else Decimal("0"), passed=passed,
                                        detail={"provider_double": True}))
    return model_id


def response(provider, *, content='{"value": 42}', finished=True):
    if provider == "openai":
        return {"id": "provider-double-response", "status": "completed" if finished else "incomplete",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}],
                "usage": {"input_tokens": 100, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 50}}}
    if provider == "anthropic":
        return {"id": "provider-double-response", "stop_reason": "end_turn" if finished else "max_tokens",
                "content": [{"type": "text", "text": content}],
                "usage": {"input_tokens": 50, "output_tokens": 20, "cache_read_input_tokens": 50}}
    if provider == "google":
        return {"candidates": [{"content": {"parts": [{"text": content}]}, "finishReason": "STOP" if finished else "MAX_TOKENS"}],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 15, "thoughtsTokenCount": 5,
                                  "cachedContentTokenCount": 50}}
    return {"id": "provider-double-response", "choices": [{"message": {"content": content},
                                                           "finish_reason": "stop" if finished else "length"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 50}}}


def test_caller_data_provider_restriction_cannot_be_broadened_by_stored_policy(world, configured):
    from gluons.db.models import Automation
    automation = world.order_automation()
    model = register(automation["skill_id"], "openai")
    with tenant_tx(world.tenant_id) as session:
        session.get(Automation, automation["id"]).model_policy = {"provider": "openai", "model_id": model, "allowed_providers": ["openai"]}
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response("openai"))
    with pytest.raises(GluonsError, match="No configured, permitted model"):
        model_gateway.complete_structured(tenant_id=world.tenant_id, purpose=automation["skill_id"], schema=Answer,
            system="Extract", user="Private input", automation_id=automation["id"],
            model_policy={"allowed_providers": ["private"]}, transport=httpx.MockTransport(handler))
    assert calls == []


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "private"])
def test_documented_provider_shapes_and_cache_usage(configured, provider):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response(provider))
    result = providers.infer(provider=provider, model_id="test-model", schema=Answer.model_json_schema(), system="Extract only.",
                              user="Input", transport=httpx.MockTransport(handler))
    assert result.text == '{"value": 42}'
    assert result.usage == {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 50, "cache_write_tokens": 0}
    payload = json.loads(requests[0].content)
    assert "tools" not in payload
    if provider == "openai":
        assert requests[0].url.path == "/v1/responses" and payload["store"] is False
        assert payload["text"]["format"]["strict"] is True
    elif provider == "anthropic":
        assert requests[0].headers["anthropic-version"] == "2026-10-21"
        assert payload["output_config"]["format"]["type"] == "json_schema"
        assert "temperature" not in payload
    elif provider == "google":
        assert requests[0].url.host.endswith("aiplatform.googleapis.com")
        assert payload["generationConfig"]["responseSchema"]["type"] == "OBJECT"
    else:
        assert requests[0].url.path == "/v1/chat/completions"


def test_unevaluated_cheaper_model_is_not_used(world, configured):
    purpose = new_id("purpose")
    register(purpose, passed=False, price="0")
    approved = register(purpose)
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response("openai"))
    result = model_gateway.complete_structured(tenant_id=world.tenant_id, purpose=purpose, schema=Answer, system="Extract.", user="Input",
                                               transport=httpx.MockTransport(handler))
    assert result.value.value == 42 and result.model_identifier == f"openai:{approved}"
    assert requests[0]["model"] == approved
    assert result.cost_usd == Decimal("0.00035")
    with tenant_tx(world.tenant_id) as session:
        ledger = session.execute(select(UsageEvent).where(UsageEvent.skill_id == purpose)).scalar_one()
        assert ledger.cost_basis == "token_rate_estimate" and ledger.detail["usage"]["cache_read_tokens"] == 50
        assert session.execute(select(BudgetHold)).first() is None


def test_data_provider_restrictions_are_a_hard_ceiling(world, configured):
    purpose = new_id("purpose")
    register(purpose)
    with tenant_tx(world.tenant_id) as session:
        session.execute(update(Organization).where(Organization.id == world.tenant_id).values(allowed_model_providers=["private"]))
    def forbidden(request):
        pytest.fail("Disallowed data must never reach an inference provider")
    with pytest.raises(GluonsError) as error:
        model_gateway.complete_structured(tenant_id=world.tenant_id, purpose=purpose, schema=Answer, system="Extract.", user="Private input",
                                          model_policy={"allowed_providers": ["openai"]}, transport=httpx.MockTransport(forbidden))
    assert error.value.code == "MODEL_UNAVAILABLE"


def test_budget_is_reserved_before_any_provider_request(world, configured):
    purpose = new_id("purpose")
    register(purpose)
    with tenant_tx(world.tenant_id) as session:
        session.execute(update(Organization).where(Organization.id == world.tenant_id).values(monthly_budget_usd=0))
    def forbidden(request):
        pytest.fail("Budget denial must happen before inference")
    with pytest.raises(GluonsError) as error:
        model_gateway.complete_structured(tenant_id=world.tenant_id, purpose=purpose, schema=Answer, system="Extract.", user="Input",
                                          transport=httpx.MockTransport(forbidden))
    assert error.value.code == "BUDGET_EXCEEDED"


def test_invalid_output_falls_back_but_both_attempts_are_metered(world, configured):
    purpose = new_id("purpose")
    first = register(purpose, price="1")
    second = register(purpose, provider="anthropic", price="2")
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload["model"])
        return httpx.Response(200, json=response("openai", content='{"value": "not-an-int"}') if payload["model"] == first else response("anthropic"))
    result = model_gateway.complete_structured(tenant_id=world.tenant_id, purpose=purpose, schema=Answer, system="Extract.", user="Input",
                                               transport=httpx.MockTransport(handler))
    assert calls == [first, second] and result.value.value == 42
    assert [a["status"] for a in result.attempts] == ["invalid_output", "validated"]
    with tenant_tx(world.tenant_id) as session:
        assert len(session.execute(select(UsageEvent).where(UsageEvent.skill_id == purpose)).scalars().all()) == 2


def test_incomplete_model_output_cannot_become_success(world, configured):
    purpose = new_id("purpose")
    register(purpose)
    with pytest.raises(GluonsError) as error:
        model_gateway.complete_structured(tenant_id=world.tenant_id, purpose=purpose, schema=Answer, system="Extract.", user="Input",
                                          transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response("openai", finished=False))))
    assert error.value.details["attempts"][0]["status"] == "invalid_output"


def test_provider_timeout_is_accounted_conservatively_and_falls_back(world, configured):
    purpose = new_id("purpose")
    first = register(purpose, price="1")
    register(purpose, provider="anthropic", price="2")
    def handler(request):
        if json.loads(request.content)["model"] == first:
            raise httpx.ReadTimeout("provider-double timeout", request=request)
        return httpx.Response(200, json=response("anthropic"))
    result = model_gateway.complete_structured(tenant_id=world.tenant_id, purpose=purpose, schema=Answer, system="Extract.", user="Input",
                                               transport=httpx.MockTransport(handler))
    assert result.value.value == 42 and result.attempts[0]["code"] == "TRANSPORT_ERROR"
    with tenant_tx(world.tenant_id) as session:
        rows = session.execute(select(UsageEvent).where(UsageEvent.skill_id == purpose)).scalars().all()
    assert {r.cost_basis for r in rows} == {"estimated", "token_rate_estimate"}


def test_switch_to_unverified_candidate_fails_and_prose_fixtures_do_not_certify_model(world, configured):
    automation = world.order_automation()
    with pytest.raises(GluonsError) as error:
        model_gateway.set_skill_model(world.owner, automation["id"], "openai", "not-certified", automation["version"])
    assert error.value.code == "MODEL_EVALUATION_REQUIRED"
    with pytest.raises(GluonsError) as error:
        model_gateway.evaluate_candidate(world.owner, "multichannel_order_entry", "openai", "not-certified")
    assert error.value.code == "MODEL_EVALUATION_REQUIRED" and error.value.details["status"] == "requires_domain_evaluation"
