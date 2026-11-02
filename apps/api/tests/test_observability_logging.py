"""Structured logs, Cloud Trace correlation and request id handling, through real HTTP handling."""
from __future__ import annotations

import io
import json
import logging
import sys

import pytest
from fastapi.testclient import TestClient

from gluons import observability
from gluons.api.app import create_app
from gluons.observability import (
    JsonFormatter,
    RedactionFilter,
    TraceContext,
    bind,
    configure_logging,
    configure_tracing,
    current_request_id,
    parse_trace_headers,
    propagation_headers,
    request_scope,
    safe_request_id,
)

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


@pytest.fixture
def captured():
    """Captures what the real handler chain writes, on the logger the application uses."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter("gluons-staging"))
    root = logging.getLogger()
    root.addHandler(handler)
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    root.removeHandler(handler)


def test_json_line_has_the_fields_cloud_logging_reads(captured):
    with request_scope({"x-request-id": "req-abc", "x-cloud-trace-context": f"{TRACE}/1234;o=1"}):
        logging.getLogger("gluons.test").warning("hello", extra={"attempt": 2})
    entry = captured()[-1]
    assert entry["severity"] == "WARNING" and entry["message"] == "hello"
    assert entry["logger"] == "gluons.test" and entry["attempt"] == 2
    assert entry["request_id"] == "req-abc"
    assert entry["logging.googleapis.com/trace"] == f"projects/gluons-staging/traces/{TRACE}"
    assert entry["logging.googleapis.com/spanId"] == f"{1234:016x}"
    assert entry["logging.googleapis.com/trace_sampled"] is True


def test_cloud_trace_and_traceparent_are_parsed():
    assert parse_trace_headers({"traceparent": f"00-{TRACE}-00f067aa0ba902b7-01"}) == TraceContext(TRACE, "00f067aa0ba902b7", True)
    assert parse_trace_headers({"x-cloud-trace-context": TRACE}) == TraceContext(TRACE, None, False)
    assert parse_trace_headers({"x-cloud-trace-context": f"{TRACE}/5;o=0"}).sampled is False


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"traceparent": "garbage"},
        {"traceparent": f"00-{'0' * 32}-00f067aa0ba902b7-01"},
        {"x-cloud-trace-context": "not-a-trace"},
        {"x-cloud-trace-context": f"{TRACE}\n{{\"severity\":\"EMERGENCY\"}}"},
    ],
)
def test_malformed_trace_headers_are_ignored(headers):
    assert parse_trace_headers(headers) is None


@pytest.mark.parametrize("bad", ["a b", "x" * 129, 'x"} forged', "line\nbreak", "", None])
def test_unsafe_request_ids_are_replaced(bad):
    assert safe_request_id(bad).startswith("req_")


def test_context_ends_with_the_request(captured):
    with request_scope({"x-request-id": "req-1"}):
        assert current_request_id() == "req-1"
    assert current_request_id() is None
    logging.getLogger("gluons.test").warning("after")
    assert "request_id" not in captured()[-1]


def test_bound_fields_reach_each_record_and_nest(captured):
    with bind(job_id="job_1"), bind(job_kind="run.advance"):
        logging.getLogger("gluons.test").warning("working")
    logging.getLogger("gluons.test").warning("done")
    first, second = captured()[-2:]
    assert first["job_id"] == "job_1" and first["job_kind"] == "run.advance"
    assert "job_id" not in second


def test_propagation_headers_carry_the_ids_outward():
    assert propagation_headers() == {}
    with request_scope({"x-request-id": "req-2", "traceparent": f"00-{TRACE}-00f067aa0ba902b7-01"}):
        assert propagation_headers() == {"X-Request-Id": "req-2", "traceparent": f"00-{TRACE}-00f067aa0ba902b7-01"}


def test_http_request_is_correlated_and_logs_no_query_string(database, captured):
    client = TestClient(create_app())
    secret = "oauth-code-value-that-must-not-appear"
    response = client.get(
        f"/health?code={secret}",
        headers={"X-Request-Id": "req-http", "X-Cloud-Trace-Context": f"{TRACE}/7;o=1"},
    )
    assert response.status_code == 200 and response.headers["X-Request-Id"] == "req-http"
    entry = next(e for e in captured() if e.get("message") == "request" and e.get("path") == "/health")
    assert entry["request_id"] == "req-http" and entry["status"] == 200
    assert entry["logging.googleapis.com/trace"].endswith(TRACE)
    assert secret not in json.dumps(captured())


def test_response_gets_a_generated_id_when_the_caller_sends_a_bad_one(database):
    response = TestClient(create_app()).get("/health", headers={"X-Request-Id": "bad id"})
    assert response.headers["X-Request-Id"].startswith("req_")


def test_configure_logging_is_repeatable_and_silences_access_logs():
    root = logging.getLogger()
    before = root.level
    try:
        configure_logging(log_format="json")
        configure_logging(log_format="text")
        mine = [h for h in root.handlers if h.get_name() == "gluons"]
        assert len(mine) == 1 and any(isinstance(f, RedactionFilter) for f in mine[0].filters)
        assert logging.getLogger("uvicorn.access").disabled is True
        assert logging.getLogger("uvicorn").handlers == [] and logging.getLogger("uvicorn").propagate is True
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        root.setLevel(before)


def test_tracing_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv("GLUONS_OTEL_ENABLED", raising=False)
    assert configure_tracing(object()) is False


def test_missing_tracing_packages_do_not_stop_the_service(monkeypatch, caplog):
    monkeypatch.setenv("GLUONS_OTEL_ENABLED", "1")
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)
    with caplog.at_level(logging.WARNING, logger=observability.logger.name):
        assert configure_tracing(object()) is False
    assert "not installed" in caplog.text
