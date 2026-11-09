"""The EX-01 collector is read-only and never emits raw provider evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

_path = Path(__file__).resolve().parents[3] / "scripts" / "provider_proof.py"
_spec = importlib.util.spec_from_file_location("provider_proof", _path)
assert _spec and _spec.loader
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)


@pytest.fixture(scope="session", autouse=True)
def database():
    """These tests use only a provider-independent HTTP double."""
    yield


def receipt(**changes):
    value = {"id": "private-receipt", "provider": "gmail", "action": "email.send", "environment": "sandbox",
             "status": "succeeded", "verified": True, "verified_at": "2026-11-09T10:00:00+00:00", "mode": "live",
             "provider_object_id": "private-message", "readback": {"message_id": "private-message"},
             "warnings": ["private-warning"]}
    value.update(changes)
    return value


def test_collect_reads_only_and_removes_private_data():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "run_123", "receipts": [receipt()], "input": "private-payload"})

    result = proof.collect("https://api.example.com", "secret-session", "run_123", provider="gmail",
                           action="email.send", environment="sandbox", transport=httpx.MockTransport(handler))
    assert len(requests) == 1 and requests[0].method == "GET"
    assert requests[0].url.path == "/v1/runs/run_123"
    assert result["gateway_verified"] is True and result["independent_provider_evidence"] == "not_collected"
    assert result["review_status"] == "pending"
    assert "private" not in json.dumps(result) and "secret-session" not in json.dumps(result)


@pytest.mark.parametrize("url", ["http://example.com", "https://u:p@example.com", "https://example.com/path",
                                 "https://example.com/?token=x", "https://example.com/#x"])
def test_rejects_unsafe_origins_without_a_request(url):
    with pytest.raises(ValueError):
        proof.collect(url, "session", "run_123", provider="gmail", action="email.send", environment="sandbox",
                      transport=httpx.MockTransport(lambda request: pytest.fail("request must not be sent")))


def test_missing_or_wrong_environment_never_becomes_provider_proof():
    with pytest.raises(ValueError):
        proof.summarize({"receipts": [receipt(environment="simulator")]}, provider="gmail", action="email.send",
                        environment="sandbox")
    result = proof.summarize({"receipts": [receipt(status="uncertain", verified=False, verified_at=None, readback=None)]},
                             provider="gmail", action="email.send", environment="sandbox")
    assert result["gateway_verified"] is False and result["review_status"] == "pending"


def test_supervised_gmail_is_supported_but_duplicate_receipts_are_ambiguous():
    selected = receipt(mode="supervised", environment="production")
    result = proof.summarize({"receipts": [selected]}, provider="gmail", action="email.send", environment="production")
    assert result["mode"] == "supervised" and result["evidence_class"] == "production"
    with pytest.raises(ValueError):
        proof.summarize({"receipts": [selected, selected]}, provider="gmail", action="email.send", environment="production")


@pytest.mark.parametrize("change", [{"mode": "shadow"}, {"readback": {}}, {"provider_object_id": None},
                                   {"verified_at": None}, {"status": "pending"}])
def test_inconsistent_verified_receipts_fail_closed(change):
    with pytest.raises(ValueError):
        proof.summarize({"receipts": [receipt(**change)]}, provider="gmail", action="email.send", environment="sandbox")


def test_redirect_does_not_forward_session():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://other.example/"})

    with pytest.raises(httpx.HTTPStatusError):
        proof.collect("https://api.example.com", "secret-session", "run_123", provider="gmail", action="email.send",
                      environment="sandbox", transport=httpx.MockTransport(handler))
    assert len(requests) == 1 and requests[0].url.host == "api.example.com"


def test_oversized_response_is_rejected():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (proof._MAX_BYTES + 1)))
    with pytest.raises(ValueError, match="size limit"):
        proof.collect("http://127.0.0.1:8000", "session", "run_123", provider="gmail", action="email.send",
                      environment="production", transport=transport)


def test_no_session_or_other_action_can_issue_a_request():
    transport = httpx.MockTransport(lambda request: pytest.fail("request must not be sent"))
    with pytest.raises(ValueError):
        proof.collect("https://api.example.com", "", "run_123", provider="gmail", action="email.send",
                      environment="production", transport=transport)
    with pytest.raises(ValueError):
        proof.collect("https://api.example.com", "session", "run_123", provider="erpnext", action="erp.order.create",
                      environment="production", transport=transport)


def test_explicit_readback_is_second_get_and_does_not_expose_receipt_id():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/runs/run_123":
            return httpx.Response(200, json={"id": "run_123", "receipts": [receipt(id="private-receipt")]})
        return httpx.Response(200, json={"provider": "gmail", "environment": "sandbox", "found": True,
                                        "matches": True, "scope": "sent_mail_readback_not_delivery",
                                        "receipt_modified": False, "account_identity_reverified": False})

    result = proof.collect("https://api.example.com", "secret-session", "run_123", provider="gmail", action="email.send",
                           environment="sandbox", independent_readback=True, transport=httpx.MockTransport(handler))
    assert [r.method for r in requests] == ["GET", "GET"]
    assert requests[1].url.path == "/v1/proofs/gmail/receipts/private-receipt/readback"
    assert result["independent_provider_evidence"] == "matched" and result["review_status"] == "pending"
    assert "private-receipt" not in json.dumps(result) and "secret-session" not in json.dumps(result)
