"""Bounded JSON inference over the providers' documented REST APIs.

Inference POSTs are read-only computations, not business writes. They run only
after the model gateway reserves budget and filters allowed data providers.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from gluons.config import get_settings
from gluons.core import GluonsError
from gluons.net import GuardedClient


@dataclass(frozen=True)
class InferenceResponse:
    text: str
    usage: dict[str, int] = field(default_factory=dict)
    request_id: str | None = None
    finished: bool = True


def configured(provider: str) -> bool:
    settings = get_settings()
    return bool({"openai": settings.openai_api_key, "anthropic": settings.anthropic_api_key,
                 "google": settings.google_genai_project, "private": settings.private_llm_base_url}.get(provider))


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(schema)
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(result)
    return result


def google_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Translate Pydantic JSON Schema to Google's documented Schema subset."""
    definitions = schema.get("$defs", {})
    supported = {"type", "format", "properties", "items", "required", "enum", "description", "nullable",
                 "minimum", "maximum", "minItems", "maxItems", "anyOf"}
    def visit(value: Any, seen: frozenset[str] = frozenset()) -> Any:
        if isinstance(value, list):
            return [visit(v, seen) for v in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            name = value["$ref"].removeprefix("#/$defs/")
            if name not in definitions or name in seen:
                raise GluonsError("MODEL_SCHEMA_UNSUPPORTED", "Recursive or external schemas are not supported for this provider.")
            return visit(definitions[name], seen | {name})
        choices = value.get("anyOf", [])
        non_null = [v for v in choices if v.get("type") != "null"]
        if choices and len(non_null) == 1 and len(choices) == 2:
            return {**visit(non_null[0], seen), "nullable": True}
        result = {}
        for key, child in value.items():
            if key not in supported:
                continue
            if key == "properties":
                result[key] = {k: visit(v, seen) for k, v in child.items()}
            elif key == "type":
                if child == "null":
                    raise GluonsError("MODEL_SCHEMA_UNSUPPORTED", "A standalone null schema is unsupported for this provider.")
                result[key] = child.upper()
            else:
                result[key] = visit(child, seen)
        return result
    return visit(schema)


def _count(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GluonsError("MODEL_INVALID_USAGE", "The provider did not report valid token usage.")
    return value


def _google_authorization() -> str:
    try:
        import google.auth
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise GluonsError("MODEL_UNAVAILABLE", "Google inference requires the gcp extra and Application Default Credentials.") from exc
    try:
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        if not credentials.token:
            raise ValueError("No ADC access token")
        return f"Bearer {credentials.token}"
    except (GoogleAuthError, ValueError) as exc:
        raise GluonsError("MODEL_UNAVAILABLE", "Google inference requires the gcp extra and Application Default Credentials.") from exc


def infer(*, provider: str, model_id: str, schema: dict[str, Any], system: str, user: str,
          max_output_tokens: int = 2048, transport: httpx.BaseTransport | None = None) -> InferenceResponse:
    settings = get_settings()
    if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,200}", model_id) or ".." in model_id or "//" in model_id:
        raise GluonsError("MODEL_UNAVAILABLE", "The configured model identifier is invalid.")
    if not configured(provider):
        raise GluonsError("MODEL_UNAVAILABLE", "This model provider requires local secure configuration.")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if provider == "openai":
        url = "https://api.openai.com/v1/responses"
        headers["Authorization"] = f"Bearer {settings.openai_api_key}"
        body = {"model": model_id, "instructions": system, "input": user, "store": False,
                "max_output_tokens": max_output_tokens,
                "text": {"format": {"type": "json_schema", "name": "gluons_output", "strict": True, "schema": strict_schema(schema)}}}
    elif provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers.update({"x-api-key": settings.anthropic_api_key or "", "anthropic-version": "2026-10-21"})
        body = {"model": model_id, "system": system, "messages": [{"role": "user", "content": user}],
                "max_tokens": max_output_tokens, "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    elif provider == "google":
        project = settings.google_genai_project or ""
        region = settings.gcp_region
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", project) or not re.fullmatch(r"[a-z0-9-]+", region):
            raise GluonsError("MODEL_UNAVAILABLE", "Configure a valid Google project and region.")
        host = "aiplatform.googleapis.com" if region == "global" else f"{region}-aiplatform.googleapis.com"
        url = f"https://{host}/v1/projects/{project}/locations/{region}/publishers/google/models/{model_id}:generateContent"
        headers["Authorization"] = _google_authorization()
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseSchema": google_schema(schema),
                                     "maxOutputTokens": max_output_tokens}}
    elif provider == "private":
        base = (settings.private_llm_base_url or "").rstrip("/")
        parts = urlsplit(base)
        if (not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
                or parts.scheme not in ("https", "http") or (settings.hosted and parts.scheme != "https")):
            raise GluonsError("MODEL_UNAVAILABLE", "The private inference endpoint must be an operator-configured secure base URL.")
        url = f"{base}/chat/completions"
        if settings.private_llm_api_key:
            headers["Authorization"] = f"Bearer {settings.private_llm_api_key}"
        body = {"model": model_id, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_output_tokens,
                "response_format": {"type": "json_schema", "json_schema": {"name": "gluons_output", "strict": True,
                                                                            "schema": strict_schema(schema)}}}
    else:
        raise GluonsError("MODEL_UNAVAILABLE", "Unsupported model provider.")
    client = GuardedClient((urlsplit(url).hostname or "",), headers=headers, timeout=45, transport=transport)
    try:
        response = client.post(url, read_only=True, json=body)
        if response.status_code >= 400:
            # Never surface provider response bodies, which can echo prompts or credentials.
            raise GluonsError("MODEL_PROVIDER_ERROR", "The configured inference provider rejected the request.",
                              status=502, details={"provider": provider, "http_status": response.status_code})
        data = response.json()
        request_id = response.headers.get("x-request-id") or response.headers.get("request-id") or data.get("id")
        if provider == "openai":
            usage = data.get("usage", {})
            text = "".join(part.get("text", "") for item in data.get("output", []) for part in item.get("content", [])
                           if part.get("type") == "output_text")
            return InferenceResponse(text, {"input_tokens": _count(usage.get("input_tokens")),
                                     "output_tokens": _count(usage.get("output_tokens")),
                                     "cache_read_tokens": _count(usage.get("input_tokens_details", {}).get("cached_tokens", 0)),
                                     "cache_write_tokens": 0}, request_id, data.get("status") == "completed")
        if provider == "anthropic":
            usage = data.get("usage", {})
            cached, written = _count(usage.get("cache_read_input_tokens", 0)), _count(usage.get("cache_creation_input_tokens", 0))
            return InferenceResponse("".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text"),
                                     {"input_tokens": _count(usage.get("input_tokens")) + cached + written,
                                      "output_tokens": _count(usage.get("output_tokens")),
                                      "cache_read_tokens": cached, "cache_write_tokens": written},
                                     request_id, data.get("stop_reason") == "end_turn")
        if provider == "google":
            usage = data.get("usageMetadata", {})
            candidate = (data.get("candidates") or [{}])[0]
            return InferenceResponse("".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", [])
                                            if not p.get("thought")),
                                     {"input_tokens": _count(usage.get("promptTokenCount")),
                                      "output_tokens": _count(usage.get("candidatesTokenCount", 0)) + _count(usage.get("thoughtsTokenCount", 0)),
                                      "cache_read_tokens": _count(usage.get("cachedContentTokenCount", 0)), "cache_write_tokens": 0},
                                     request_id, candidate.get("finishReason") == "STOP")
        usage = data.get("usage", {})
        choice = (data.get("choices") or [{}])[0]
        return InferenceResponse(choice.get("message", {}).get("content") or "",
                                 {"input_tokens": _count(usage.get("prompt_tokens")),
                                  "output_tokens": _count(usage.get("completion_tokens")),
                                  "cache_read_tokens": _count(usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)),
                                  "cache_write_tokens": 0}, request_id, choice.get("finish_reason") == "stop")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise GluonsError("MODEL_INVALID_RESPONSE", "The provider response could not be validated.", status=502) from exc
    finally:
        client.close()


def encoded_request_size(system: str, user: str, schema: dict[str, Any]) -> int:
    return len(system.encode("utf-8")) + len(user.encode("utf-8")) + len(json.dumps(schema).encode("utf-8")) + 4096
