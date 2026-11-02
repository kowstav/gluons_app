"""Structured logs, request correlation and optional tracing.

Logs go to stdout as JSON, which Cloud Run forwards to Cloud Logging. The `logging.googleapis.com/*`
fields tie each line to its Cloud Trace trace. Every record passes through `RedactionFilter`
first, because a provider token must never reach a log even if a developer logs a whole
request or exception by mistake. Redaction is a safety net, not permission to log secrets.

The infra/terraform/modules/observability log metrics match on `message` and `logger`, so
changing either one for an alerting record means changing the filter there as well.

Environment: GLUONS_LOG_LEVEL (INFO), GLUONS_LOG_FORMAT (json or text, default json when
hosted), GLUONS_OTEL_ENABLED and the standard OTEL_EXPORTER_OTLP_* variables.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from gluons.config import get_settings
from gluons.core import new_id

logger = logging.getLogger("gluons.observability")

REDACTED = "[REDACTED]"
MAX_DEPTH = 6

# Credential shapes that are recognisable with no surrounding context.
_TOKEN_SHAPES = (
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*",  # JWT, with or without signature
    r"\bya29\.[\w.-]{16,}",  # Google access token
    r"\b1//[\w-]{20,}",  # Google refresh token
    r"\bGOCSPX-[\w-]{16,}",  # Google OAuth client secret
    r"\bAIza[\w-]{30,}",  # Google API key
    r"\bsk-[A-Za-z0-9_-]{16,}",  # OpenAI and Anthropic
    r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}",  # Stripe
    r"\bsk_[A-Za-z0-9]{24,}",  # ElevenLabs
    r"\bwhsec_[A-Za-z0-9+/=_-]{16,}",  # Stripe webhook signing secret
    r"\bshp(?:at|ss|ca|pa)_[A-Fa-f0-9]{16,}",  # Shopify
    r"\bEAA[A-Za-z0-9]{24,}",  # Meta
    r"\bSK[0-9a-f]{32}\b",  # Twilio API key
    r"\bgh[pousr]_[A-Za-z0-9]{24,}",  # GitHub
    r"\bgithub_pat_[A-Za-z0-9_]{24,}",
    r"\bAKIA[0-9A-Z]{16}\b",  # AWS
    r"\bxox[abprs]-[A-Za-z0-9-]{16,}",  # Slack
)

# `token` has to end the name, so `max_tokens=4096` stays readable.
_SENSITIVE_NAME = (
    r"[\w-]*(?:(?:password|passwd|pwd|secret|api[_-]?key|private[_-]?key|signing[_-]?key|kek"
    r"|database[_-]?url|smtp[_-]?url)[\w-]*|token)"
)

_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Multi-line keys first: `private_key: ...` below would otherwise take only the first word.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), REDACTED),
    # Whole header values next, so a two-part value such as `Bearer abc` goes in one piece.
    (
        re.compile(r"""(?i)((?:proxy-|x-serverless-)?authorization["']?\s*[:=]\s*["']?)[^"'\r\n]+"""),
        rf"\1{REDACTED}",
    ),
    (re.compile(r"""(?i)((?:set-)?cookie["']?\s*[:=]\s*["']?)[^"'\r\n]+"""), rf"\1{REDACTED}"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    # Frappe and ERPNext send `token key:secret`.
    (re.compile(r"(?i)\btoken\s+[A-Za-z0-9_-]{6,}:[A-Za-z0-9_-]{6,}"), f"token {REDACTED}"),
    # scheme://user:password@host, as in database and SMTP URLs.
    (re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://[^:/?#\s@]*):[^@/\s]+@"), rf"\1:{REDACTED}@"),
    # An OAuth code and state are bearer material only as URL parameters; `code=404` elsewhere is not.
    (re.compile(r"(?i)([?&](?:code|state)=)[^&\s\"']+"), rf"\1{REDACTED}"),
    # name=value, name: value and "name": "value".
    (
        re.compile(rf"""(?i)({_SENSITIVE_NAME}["']?\s*[:=]\s*["']?)[^\s"'&,;}}]+"""),
        rf"\1{REDACTED}",
    ),
    *((re.compile(shape), REDACTED) for shape in _TOKEN_SHAPES),
)

# Dictionary keys whose whole value is withheld. `input_tokens` and `token_count` are usage
# metrics, not credentials, so this matches `token` only as the end of a name.
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|credential|api[_-]?key|private[_-]?key|signing[_-]?key"
    r"|authorization|cookie|database[_-]?url|smtp[_-]?url|kek|(?:^|[_-])token$)"
)

_RESERVED = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def redact_text(text: str) -> str:
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any, *, depth: int = 0) -> Any:
    """Keeps structure so logs stay queryable, and withholds anything under a sensitive key."""
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, bytes | bytearray | memoryview):
        return f"[{len(value)} bytes]"
    if depth >= MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _SENSITIVE_KEY.search(str(key)) else redact_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_value(item, depth=depth + 1) for item in value]
    return redact_text(str(value))


class RedactionFilter(logging.Filter):
    """Rewrites the record in place. It runs on the handler, so it covers third-party loggers."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        if record.exc_info:
            record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        for key, value in list(record.__dict__.items()):
            if key not in _RESERVED:
                record.__dict__[key] = REDACTED if _SENSITIVE_KEY.search(key) else redact_value(value)
        return True


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str | None = None
    sampled: bool = False


_CLOUD_TRACE = re.compile(r"^([0-9a-f]{32})(?:/(\d{1,20}))?(?:;o=([01]))?$")
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_ZERO_TRACE = "0" * 32

_request_id: ContextVar[str | None] = ContextVar("gluons_request_id", default=None)
_trace: ContextVar[TraceContext | None] = ContextVar("gluons_trace", default=None)
_fields: ContextVar[dict[str, Any] | None] = ContextVar("gluons_log_fields", default=None)


def parse_trace_headers(headers: Mapping[str, str]) -> TraceContext | None:
    """Reads W3C `traceparent`, then Google's `X-Cloud-Trace-Context`. Bad input means no trace."""
    parent = _TRACEPARENT.match((headers.get("traceparent") or "").strip().lower())
    if parent and parent.group(1) != _ZERO_TRACE:
        return TraceContext(parent.group(1), parent.group(2), bool(int(parent.group(3), 16) & 1))
    cloud = _CLOUD_TRACE.match((headers.get("x-cloud-trace-context") or "").strip().lower())
    if cloud and cloud.group(1) != _ZERO_TRACE:
        # Cloud Trace headers carry a decimal span id; the log field wants 16 hex characters.
        span = f"{int(cloud.group(2)) & (2**64 - 1):016x}" if cloud.group(2) else None
        return TraceContext(cloud.group(1), span, cloud.group(3) == "1")
    return None


def safe_request_id(candidate: str | None) -> str:
    """A caller's id is kept only if harmless, so it cannot forge log lines or headers."""
    if candidate and _REQUEST_ID.match(candidate):
        return candidate
    return new_id("req")


@contextmanager
def request_scope(headers: Mapping[str, str]) -> Iterator[str]:
    """Binds request id and trace for everything logged while handling one request."""
    request_id = safe_request_id(headers.get("x-request-id"))
    id_token = _request_id.set(request_id)
    trace_token = _trace.set(parse_trace_headers(headers))
    try:
        yield request_id
    finally:
        _request_id.reset(id_token)
        _trace.reset(trace_token)


@contextmanager
def bind(**fields: Any) -> Iterator[None]:
    """Adds fields to every record in this scope, such as a job id in the worker."""
    token = _fields.set({**(_fields.get() or {}), **fields})
    try:
        yield
    finally:
        _fields.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()


def propagation_headers() -> dict[str, str]:
    """Headers for an outbound call, so one request can be followed across services."""
    headers: dict[str, str] = {}
    request_id = _request_id.get()
    if request_id:
        headers["X-Request-Id"] = request_id
    trace = _trace.get()
    if trace and trace.span_id:
        headers["traceparent"] = f"00-{trace.trace_id}-{trace.span_id}-{'01' if trace.sampled else '00'}"
    return headers


class JsonFormatter(logging.Formatter):
    """One JSON object per line, in the shape Cloud Logging reads from stdout."""

    def __init__(self, project_id: str | None = None) -> None:
        super().__init__()
        self._project_id = project_id

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "logger": record.name,
        }
        request_id = _request_id.get()
        if request_id:
            entry["request_id"] = request_id
        trace = _trace.get()
        if trace:
            prefix = f"projects/{self._project_id}/" if self._project_id else ""
            entry["logging.googleapis.com/trace"] = f"{prefix}traces/{trace.trace_id}"
            entry["logging.googleapis.com/trace_sampled"] = trace.sampled
            if trace.span_id:
                entry["logging.googleapis.com/spanId"] = trace.span_id
        entry.update(redact_value(_fields.get() or {}))
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in entry:
                entry[key] = value
        if record.exc_text:
            entry["exception"] = record.exc_text
        if record.stack_info:
            entry["stack"] = record.stack_info
        return json.dumps(entry, default=lambda item: redact_text(str(item)), ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Readable local output, carrying the same correlation fields."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        request_id = _request_id.get()
        return f"{line} request_id={request_id}" if request_id else line


class _StdoutHandler(logging.StreamHandler):
    """Looks up stdout on every write, so a replaced stream is never written to after it closes."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self) -> Any:
        return sys.stdout


def configure_logging(*, level: str | None = None, log_format: str | None = None) -> None:
    """Safe to call repeatedly. Other handlers on the root logger are left alone."""
    settings = get_settings()
    chosen = (log_format or os.environ.get("GLUONS_LOG_FORMAT") or ("json" if settings.hosted else "text")).lower()
    handler = _StdoutHandler()
    handler.set_name("gluons")
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter(settings.gcp_project_id) if chosen == "json" else TextFormatter())

    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.get_name() == "gluons"]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel((level or os.environ.get("GLUONS_LOG_LEVEL") or "INFO").upper())

    for name in ("uvicorn", "uvicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    # Access lines carry the query string, which holds OAuth codes on callback URLs. The
    # request log in api/app.py records the path alone. httpx logs full request URLs at INFO.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.disabled = True
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


_SPAN_URL_ATTRIBUTES = ("http.url", "http.target", "url.full", "url.query")


def scrub_span(span: Any, scope: dict[str, Any]) -> None:
    """Span attributes bypass logging, and they include the query string by default."""
    attributes = getattr(span, "attributes", None) or {}
    for name in _SPAN_URL_ATTRIBUTES:
        value = attributes.get(name)
        if isinstance(value, str):
            span.set_attribute(name, redact_text(value))


def configure_tracing(app: Any) -> bool:
    """Exports OTLP spans when GLUONS_OTEL_ENABLED is set and the packages are installed.

    Optional by design: the service must run the same without them, so a missing package
    is a warning. Needs opentelemetry-sdk, opentelemetry-exporter-otlp-proto-http and
    opentelemetry-instrumentation-fastapi.
    """
    if os.environ.get("GLUONS_OTEL_ENABLED", "").lower() not in ("1", "true"):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("tracing requested but the OpenTelemetry packages are not installed")
        return False
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready", server_request_hook=scrub_span)
    return True
