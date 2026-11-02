"""Read-only ElevenLabs evidence; this module never buys numbers or places calls."""
from __future__ import annotations

import re
from typing import Any

import httpx

from gluons.config import get_settings
from gluons.core import GluonsError
from gluons.net import GuardedClient


def read(kind: str, identifier: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    if kind not in ("conversations", "phone-numbers", "agents") or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", identifier):
        raise GluonsError("VOICE_INVALID_REFERENCE", "The provider reference is invalid.", status=422)
    key = get_settings().elevenlabs_api_key
    if not key:
        raise GluonsError("VOICE_CONFIGURATION_REQUIRED", "Configure ElevenLabs credentials securely before verification.", status=409)
    client = GuardedClient(("api.elevenlabs.io",), timeout=8, transport=transport, headers={"xi-api-key": key})
    try:
        response = client.get(f"https://api.elevenlabs.io/v1/convai/{kind}/{identifier}")
        if response.status_code != 200:
            raise GluonsError("VOICE_EVIDENCE_UNAVAILABLE", "The provider did not verify this reference.", status=502,
                              details={"http_status": response.status_code})
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Expected provider object")
        return result
    except (httpx.HTTPError, ValueError) as exc:
        raise GluonsError("VOICE_EVIDENCE_UNAVAILABLE", "Provider evidence is temporarily unavailable.", status=502) from exc
    finally:
        client.close()
