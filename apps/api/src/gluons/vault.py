"""Envelope encryption for provider credentials.

Each secret has its own data key. The data key is wrapped by a key-encryption key held
in Cloud KMS (hosted) or the environment (local). Ciphertext is bound to its tenant and
secret id through AES-GCM associated data, so a row copied into another tenant cannot be
decrypted. Plaintext leaves this module only for connector code, never for a model,
tool result or log.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from gluons.config import Settings, get_settings
from gluons.core import GluonsError, new_id, utcnow
from gluons.db.models import VaultSecret


class KeyProvider(Protocol):
    ref: str

    def wrap(self, dek: bytes, aad: bytes) -> bytes: ...
    def unwrap(self, wrapped: bytes, aad: bytes) -> bytes: ...


class LocalKeyProvider:
    def __init__(self, kek: bytes) -> None:
        self._aead = AESGCM(kek)
        self.ref = "local-kek-v1"

    def wrap(self, dek: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, dek, aad)

    def unwrap(self, wrapped: bytes, aad: bytes) -> bytes:
        return self._aead.decrypt(wrapped[:12], wrapped[12:], aad)


class GcpKmsKeyProvider:
    def __init__(self, key_name: str) -> None:
        from google.cloud import kms  # imported lazily: optional 'gcp' extra

        self._client = kms.KeyManagementServiceClient()
        self._name = key_name
        self.ref = key_name

    def wrap(self, dek: bytes, aad: bytes) -> bytes:
        response = self._client.encrypt(
            request={"name": self._name, "plaintext": dek, "additional_authenticated_data": aad}
        )
        return response.ciphertext

    def unwrap(self, wrapped: bytes, aad: bytes) -> bytes:
        response = self._client.decrypt(
            request={"name": self._name, "ciphertext": wrapped, "additional_authenticated_data": aad}
        )
        return response.plaintext


def key_provider(settings: Settings | None = None) -> KeyProvider:
    settings = settings or get_settings()
    if settings.kms_key_name:
        return GcpKmsKeyProvider(settings.kms_key_name)
    return LocalKeyProvider(settings.key_bytes(settings.vault_kek_b64))


def _aad(scope: str, secret_id: str) -> bytes:
    return f"{scope}|{secret_id}".encode()


def _seal(provider: KeyProvider, plaintext: bytes, aad: bytes) -> tuple[str, str]:
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = nonce + AESGCM(dek).encrypt(nonce, plaintext, aad)
    return base64.b64encode(ciphertext).decode(), base64.b64encode(provider.wrap(dek, aad)).decode()


def _open(provider: KeyProvider, ciphertext_b64: str, wrapped_b64: str, aad: bytes) -> bytes:
    raw = base64.b64decode(ciphertext_b64)
    dek = provider.unwrap(base64.b64decode(wrapped_b64), aad)
    return AESGCM(dek).decrypt(raw[:12], raw[12:], aad)


def store(session: Session, tenant_id: str, purpose: str, secret: dict[str, Any]) -> str:
    provider = key_provider()
    secret_id = new_id("sec")
    ciphertext, wrapped = _seal(provider, json.dumps(secret).encode(), _aad(tenant_id, secret_id))
    session.add(
        VaultSecret(
            id=secret_id,
            tenant_id=tenant_id,
            purpose=purpose,
            ciphertext=ciphertext,
            wrapped_dek=wrapped,
            kek_ref=provider.ref,
        )
    )
    session.flush()
    return secret_id


def read(session: Session, tenant_id: str, secret_id: str) -> dict[str, Any]:
    row = session.get(VaultSecret, secret_id)
    if row is None or row.tenant_id != tenant_id or row.destroyed_at is not None:
        raise GluonsError("CONNECTION_REQUIRED", "Stored credential is unavailable.", status=409)
    plaintext = _open(key_provider(), row.ciphertext, row.wrapped_dek, _aad(tenant_id, secret_id))
    return json.loads(plaintext)


def replace(session: Session, tenant_id: str, secret_id: str, secret: dict[str, Any]) -> None:
    row = session.get(VaultSecret, secret_id)
    if row is None or row.tenant_id != tenant_id:
        raise GluonsError("CONNECTION_REQUIRED", "Stored credential is unavailable.", status=409)
    provider = key_provider()
    row.ciphertext, row.wrapped_dek = _seal(provider, json.dumps(secret).encode(), _aad(tenant_id, secret_id))
    row.kek_ref = provider.ref
    row.rotated_at = utcnow()


def destroy(session: Session, tenant_id: str, secret_id: str) -> None:
    """Keeps the row for audit and removes the key material, so nothing can be decrypted."""
    row = session.get(VaultSecret, secret_id)
    if row is None or row.tenant_id != tenant_id or row.destroyed_at is not None:
        return
    row.ciphertext = ""
    row.wrapped_dek = ""
    row.destroyed_at = utcnow()


def seal_global(scope: str, secret: str) -> str:
    """For user-level secrets (TOTP seed), which belong to no tenant."""
    provider = key_provider()
    record_id = new_id("usr")
    ciphertext, wrapped = _seal(provider, secret.encode(), _aad(scope, record_id))
    return json.dumps({"id": record_id, "c": ciphertext, "w": wrapped})


def open_global(scope: str, sealed: str) -> str:
    data = json.loads(sealed)
    return _open(key_provider(), data["c"], data["w"], _aad(scope, data["id"])).decode()
