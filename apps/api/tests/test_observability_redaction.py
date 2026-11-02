"""Secrets never reach a log line. Fakes are assembled at runtime so that secret scanners and
hosting push protection do not mistake this file for a leak."""
from __future__ import annotations

import io
import json
import logging

import pytest

from gluons.observability import REDACTED, JsonFormatter, RedactionFilter, redact_text, redact_value, scrub_span

A40 = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
HEX32 = "0123456789abcdef0123456789abcdef"
HEX48 = HEX32 + "0123456789abcdef"

BARE_SHAPES = {
    "google access": "ya" + "29." + A40,
    "google refresh": "1" + "//" + A40,
    "google client secret": "GOC" + "SPX-" + A40[:28],
    "google api key": "AI" + "za" + A40[:35],
    "openai": "sk" + "-proj-" + A40,
    "anthropic": "sk" + "-ant-api03-" + A40,
    "stripe secret": "sk" + "_live_" + A40,
    "stripe restricted": "rk" + "_test_" + A40,
    "stripe webhook": "wh" + "sec_" + A40,
    "elevenlabs": "sk" + "_" + HEX48,
    "shopify admin": "shp" + "at_" + HEX32,
    "shopify secret": "shp" + "ss_" + HEX32,
    "meta": "EA" + "A" + A40,
    "twilio api key": "SK" + HEX32,
    "github": "gh" + "p_" + A40,
    "github fine grained": "github" + "_pat_" + A40,
    "aws": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "slack": "xo" + "xb-" + "1234567890-" + A40,
    "jwt": "eyJhbGciOiJSUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTYifQ." + "c2lnbmF0dXJlLXZhbHVl",
}

SECRET_PASSWORD = "hunter2-correct-horse"  # noqa: S105

IN_CONTEXT = [
    f"Authorization: Bearer {A40}",
    f"authorization: token {A40[:15]}:{A40[15:30]}",
    f"Authorization: Basic {A40}",
    f"X-Serverless-Authorization: Bearer {A40}",
    f"xi-api-key: {A40}",
    f'x-api-key="{A40}"',
    f"Cookie: gluons_session={A40}; other=1",
    f"postgresql+psycopg://gluons_app:{SECRET_PASSWORD}@10.0.0.3/gluons?sslmode=require",
    f"smtp://apikey:{SECRET_PASSWORD}@smtp.example.com:587",
    f"GLUONS_SIGNING_KEY_B64={A40}=",
    f"GLUONS_GOOGLE_CLIENT_SECRET={A40}",
    f"GLUONS_TWILIO_AUTH_TOKEN={HEX32}",
    f"GLUONS_VAULT_KEK_B64={A40}",
    f"https://api.example.com/v1/oauth/gmail/callback?state={A40}&code={A40}",
    f"https://graph.example.com/me?access_token={A40}&fields=id",
    f'{{"password": "{SECRET_PASSWORD}", "user": "a"}}',
    f"client_secret={A40}&grant_type=refresh_token",
    f"refresh_token={A40}",
    "-----BEGIN RSA PRIVATE KEY-----\n" + A40 + "\n-----END RSA PRIVATE KEY-----",
    '{"private_key": "-----BEGIN PRIVATE KEY-----\\n' + A40 + '\\n-----END PRIVATE KEY-----\\n"}',
]


@pytest.mark.parametrize("name", sorted(BARE_SHAPES))
def test_bare_credentials_are_removed(name):
    secret = BARE_SHAPES[name]
    cleaned = redact_text(f"provider said {secret} today")
    assert secret not in cleaned
    assert cleaned.startswith("provider said") and cleaned.endswith("today")


@pytest.mark.parametrize("line", IN_CONTEXT)
def test_credentials_in_context_are_removed(line):
    cleaned = redact_text(line)
    assert A40 not in cleaned and A40[:15] not in cleaned
    assert SECRET_PASSWORD not in cleaned and HEX32 not in cleaned
    assert REDACTED in cleaned


@pytest.mark.parametrize(
    "line",
    [
        "request path=/v1/automations status=200 ms=12",
        "input_tokens=1200 output_tokens=30 max_tokens=4096 token_count=5",
        "provider returned code=404 for sku ABC-123",
        "job_8f3a11c2d4e5f6a7b8c9d0e1 retried 3 times",
        "postgresql://10.0.0.3/gluons",
        "2026-11-02T10:00:00+00:00 run_id=run_abc",
    ],
)
def test_ordinary_diagnostics_survive(line):
    assert redact_text(line) == line


def test_structures_keep_their_shape_but_lose_secrets():
    value = {
        "provider": "gmail",
        "credential": {"anything": "at all"},
        "refresh_token": "plain-looking-value",
        "input_tokens": 1200,
        "headers": {"Authorization": "whatever", "Accept": "json"},
        "items": [{"api_key": "x"}, "Bearer " + A40, 7, None, True],
        "body": b"raw provider bytes",
    }
    cleaned = redact_value(value)
    assert cleaned["provider"] == "gmail" and cleaned["input_tokens"] == 1200
    assert cleaned["credential"] == REDACTED and cleaned["refresh_token"] == REDACTED
    assert cleaned["headers"] == {"Authorization": REDACTED, "Accept": "json"}
    assert cleaned["items"][0] == {"api_key": REDACTED}
    assert A40 not in cleaned["items"][1] and cleaned["items"][2:] == [7, None, True]
    assert cleaned["body"] == "[18 bytes]"


def test_cycles_and_deep_nesting_cannot_hang_the_logger():
    loop: dict = {}
    loop["self"] = loop
    assert "[truncated]" in json.dumps(redact_value(loop))


def emit(*args, extra=None, exc_info=None) -> dict:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter("proj"))
    test_logger = logging.getLogger("gluons.test.redaction")
    test_logger.handlers = [handler]
    test_logger.propagate = False
    test_logger.setLevel(logging.INFO)
    test_logger.info(*args, extra=extra, exc_info=exc_info)
    return json.loads(stream.getvalue())


def test_filter_covers_message_format_arguments():
    entry = emit("calling provider with %s", "Bearer " + A40)
    assert A40 not in json.dumps(entry)
    assert entry["message"].startswith("calling provider with")


def test_filter_covers_extra_fields():
    entry = emit("call", extra={"url": f"https://x.example/cb?code={A40}", "api_key": "plain", "attempt": 2})
    assert A40 not in json.dumps(entry)
    assert entry["api_key"] == REDACTED and entry["attempt"] == 2


def test_filter_covers_tracebacks():
    try:
        raise RuntimeError(f"upstream rejected Bearer {A40}")
    except RuntimeError:
        entry = emit("failed", exc_info=True)
    assert "RuntimeError" in entry["exception"]
    assert A40 not in json.dumps(entry)


def test_span_query_strings_are_scrubbed():
    class Span:
        attributes = {"http.target": f"/cb?code={A40}", "http.url": f"https://a.example/cb?state={A40}", "http.method": "GET"}

        def __init__(self):
            self.written = {}

        def set_attribute(self, key, value):
            self.written[key] = value

    span = Span()
    scrub_span(span, {})
    assert set(span.written) == {"http.target", "http.url"}
    assert A40 not in json.dumps(span.written)
