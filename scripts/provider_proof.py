"""Collect a minimal, read-only summary of an existing Gluons run.

This utility cannot trigger an event, open a write permit, or query a provider.
It does not replace the separately retained provider-side evidence and review.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_ENVIRONMENTS = {"simulator", "sandbox", "production"}
_STATUSES = {"pending", "preview", "succeeded", "failed", "mismatch", "uncertain", "not_applied"}
_MAX_BYTES = 2_000_000


def api_origin(value: str) -> str:
    """Accept an origin only; never put session credentials in a URL."""
    try:
        parts = urlsplit(value)
        port = parts.port
        host = parts.hostname
    except ValueError as exc:
        raise ValueError("Invalid API origin") from exc
    if (not host or parts.username is not None or parts.password is not None or
            parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("API URL must be an origin without credentials, path, query, or fragment")
    if parts.scheme != "https":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if parts.scheme != "http" or not loopback:
            raise ValueError("API origin must use HTTPS, except on loopback")
    host_part = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host_part}" + (f":{port}" if port else "")


def summarize(run: Any, *, provider: str, action: str, environment: str) -> dict[str, Any]:
    """Keep an allowlisted summary, never provider IDs, payloads, readbacks, or warnings."""
    if not isinstance(run, dict) or not isinstance(run.get("receipts"), list):
        raise TypeError("Invalid run response")
    receipts = [r for r in run["receipts"] if isinstance(r, dict) and
                r.get("provider") == provider and r.get("action") == action and
                r.get("environment") == environment]
    if len(receipts) != 1:
        raise ValueError("Expected exactly one matching receipt; select a specific run")
    receipt = receipts[0]
    status = receipt.get("status")
    verified = receipt.get("verified")
    if status not in _STATUSES or type(verified) is not bool:
        raise ValueError("Receipt has an invalid status or verification flag")
    timestamp = receipt.get("verified_at")
    mode = receipt.get("mode")
    if mode not in {"supervised", "live", "shadow", "historical_replay"}:
        raise ValueError("Receipt has an invalid mode")
    if verified:
        if status != "succeeded" or mode not in {"supervised", "live"} or not isinstance(timestamp, str):
            raise ValueError("Verified receipt is inconsistent")
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise ValueError("Verified receipt has an invalid timestamp") from exc
        if parsed.tzinfo is None or not receipt.get("provider_object_id") or not isinstance(receipt.get("readback"), dict) or not receipt["readback"]:
            raise ValueError("Verified receipt lacks a timestamp, provider object, or readback")
    return {
        "schema_version": 1,
        "collected_at": datetime.now(UTC).isoformat(),
        "evidence_class": environment,
        "provider": provider,
        "action": action,
        "receipt_status": status,
        "mode": mode,
        "gateway_verified": verified,
        "provider_object_present": bool(receipt.get("provider_object_id")),
        "gateway_readback_present": bool(receipt.get("readback")),
        "independent_provider_evidence": "not_collected",
        "review_status": "pending",
    }


def collect(origin: str, session: str, run_id: str, *, provider: str, action: str, environment: str,
            transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    if not session or any(c in session for c in "\r\n;"):
        raise ValueError("A valid Gluons session is required")
    if not _IDENTIFIER.fullmatch(run_id):
        raise ValueError("Invalid run identifier")
    if (provider, action) != ("gmail", "email.send"):
        raise ValueError("Only the selected Gmail connector proof is supported")
    if environment not in _ENVIRONMENTS:
        raise ValueError("Invalid evidence environment")
    url = f"{api_origin(origin)}/v1/runs/{run_id}"
    with (httpx.Client(timeout=15, follow_redirects=False, transport=transport, trust_env=False) as client,
          client.stream("GET", url, cookies={"gluons_session": session}) as response):
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > _MAX_BYTES:
                raise ValueError("Run response exceeds the size limit")
    run = json.loads(content)
    if not isinstance(run, dict) or run.get("id") != run_id:
        raise ValueError("Run response does not match the requested run")
    return summarize(run, provider=provider, action=action, environment=environment)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-origin", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--provider", required=True, choices=("gmail",))
    parser.add_argument("--action", required=True, choices=("email.send",))
    parser.add_argument("--environment", required=True, choices=sorted(_ENVIRONMENTS))
    args = parser.parse_args()
    try:
        result = collect(args.api_origin, os.environ.get("GLUONS_PROOF_SESSION", ""), args.run_id,
                         provider=args.provider, action=args.action, environment=args.environment)
    except (TypeError, ValueError, httpx.HTTPError, json.JSONDecodeError):
        print("Evidence collection failed; check the local configuration and authorized run.", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
