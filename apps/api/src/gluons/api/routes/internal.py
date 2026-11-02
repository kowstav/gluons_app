"""Internal endpoints called by Google Cloud, never by people or assistants.

Cloud Tasks and Cloud Scheduler wake the private worker here. A wake carries no instructions:
the request body is ignored and the jobs come from Postgres, so the worst a valid caller can
do is make due work run sooner (ADR-002).

This route ships in the same image as the public API, so it cannot lean on the worker's
network position. Every request must present a Google-signed OIDC token for the configured
audience and service account. The X-CloudTasks-* headers are informational and prove nothing.
On the public services neither setting is present, so every request there is rejected.
"""
from __future__ import annotations

import logging
import os
import socket
from functools import lru_cache
from typing import Annotated, Any

import httpx
import jwt
from fastapi import APIRouter, Header

from gluons.config import get_settings
from gluons.core import GluonsError, auth_required
from gluons.runtime import worker

logger = logging.getLogger("gluons.internal")

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
WAKE_MAX_ROUNDS = 20  # Matches `gluons worker --once`, and keeps one wake inside the request timeout.
CLOCK_SKEW_SECONDS = 10


@lru_cache
def google_keys() -> jwt.PyJWKClient:
    """A failed lookup raises, and lru_cache keeps only successes, so the next wake retries."""
    discovery = httpx.get(GOOGLE_DISCOVERY_URL, timeout=10)
    discovery.raise_for_status()
    return jwt.PyJWKClient(discovery.json()["jwks_uri"], cache_keys=True, timeout=10)


def _reject(reason: str) -> GluonsError:
    """The caller learns nothing. The reason goes to the log, for the wake-rejected alert."""
    logger.warning("internal wake rejected", extra={"reason": reason})
    return auth_required("Authentication required.")


def verify_wake_token(authorization: str | None) -> dict[str, Any]:
    settings = get_settings()
    audience = os.environ.get("GLUONS_WORKER_WAKE_AUDIENCE") or settings.worker_wake_url
    caller = settings.worker_service_account
    if not audience or not caller:
        raise _reject("not_configured")

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _reject("missing_token")

    try:
        key = google_keys().get_signing_key_from_jwt(token.strip())
        claims = jwt.decode(
            token.strip(),
            key,
            algorithms=["RS256"],  # Never taken from the token, which would allow a forged `alg`.
            audience=audience,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except (jwt.PyJWKClientConnectionError, httpx.HTTPError) as exc:
        # Not the caller's fault. Cloud Tasks retries a 503, and it stays a refusal.
        logger.error("internal wake could not load Google keys", extra={"error": type(exc).__name__})
        raise GluonsError("EXTERNAL_UNAVAILABLE", "Try again shortly.", status=503) from exc
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        logger.info("internal wake token refused", extra={"error": type(exc).__name__})
        raise _reject("bad_token") from exc

    if claims["iss"] not in GOOGLE_ISSUERS:
        raise _reject("wrong_issuer")
    if claims.get("email") != caller or claims.get("email_verified") not in (True, "true"):
        raise _reject("wrong_caller")
    return claims


@router.post("/worker/wake")
def wake(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Synchronous on purpose: FastAPI runs it in a thread, so draining never blocks the loop."""
    verify_wake_token(authorization)
    worker.scheduler_tick()
    processed = worker.drain(f"wake:{socket.gethostname()}:{os.getpid()}", max_rounds=WAKE_MAX_ROUNDS)
    return {"status": "ok", "processed": processed}
