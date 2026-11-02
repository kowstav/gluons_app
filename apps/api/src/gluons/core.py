"""Small shared primitives: ids, time, canonical hashing, tokens, canonical errors."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical_json(value: Any) -> str:
    """Stable JSON used for anything that is hashed or signed."""

    def default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.astimezone(UTC).isoformat()
        if isinstance(obj, Decimal):
            return str(obj)
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        raise TypeError(f"not canonicalizable: {type(obj)!r}")

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def fingerprint(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def hmac_sha256_hex(key: bytes, message: str | bytes) -> str:
    if isinstance(message, str):
        message = message.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def constant_time_equals(a: str | bytes, b: str | bytes) -> bool:
    a_bytes = a.encode() if isinstance(a, str) else a
    b_bytes = b.encode() if isinstance(b, str) else b
    return hmac.compare_digest(a_bytes, b_bytes)


def new_secret_token(prefix: str = "", nbytes: int = 32) -> str:
    token = secrets.token_urlsafe(nbytes)
    return f"{prefix}{token}" if prefix else token


def token_hash(token: str) -> str:
    """Bearer-style secrets are stored only as a hash."""
    return sha256_hex(token)


class GluonsError(Exception):
    """Canonical error surfaced identically over REST and MCP (blueprint doc 02)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def auth_required(message: str = "Sign in to Gluons to continue.") -> GluonsError:
    return GluonsError("AUTH_REQUIRED", message, status=401)


def forbidden(message: str = "You do not have permission to do that.") -> GluonsError:
    return GluonsError("FORBIDDEN", message, status=403)


def not_found(what: str = "Resource") -> GluonsError:
    # Identical for "absent" and "belongs to another tenant" so ids cannot be probed.
    return GluonsError("NOT_FOUND", f"{what} not found.", status=404)


def conflict_version(current: int | None = None) -> GluonsError:
    return GluonsError(
        "CONFLICT_VERSION",
        "This record changed since you loaded it. Refresh and retry.",
        status=409,
        details={"current_version": current} if current is not None else {},
    )


def conflict(message: str, **details: Any) -> GluonsError:
    return GluonsError("CONFLICT", message, status=409, details=details)


def validation_failed(message: str, **details: Any) -> GluonsError:
    return GluonsError("VALIDATION_FAILED", message, status=422, details=details)
