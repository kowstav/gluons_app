"""Environment-driven configuration.

Secrets are read from the environment (injected from Secret Manager in hosted
environments) and are never logged. `validate_for_environment` refuses to boot a
hosted environment with development defaults.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]

# Published so a fresh checkout runs locally. Rejected outside local/test.
DEV_VAULT_KEK_B64 = "ZGV2LW9ubHktZ2x1b25zLXZhdWx0LWtleS0zMmJ5dGU="
DEV_SIGNING_KEY_B64 = "ZGV2LW9ubHktZ2x1b25zLXNpZ25pbmcta2V5LTMyYnk="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GLUONS_", env_file=".env", extra="ignore")

    env: Literal["local", "test", "staging", "production"] = "local"

    database_url: str = "postgresql+psycopg://gluons_app:gluons_app_dev@127.0.0.1:5433/gluons"
    system_database_url: str = "postgresql+psycopg://gluons_system:gluons_system_dev@127.0.0.1:5433/gluons"
    migration_database_url: str = "postgresql+psycopg://postgres@127.0.0.1:5433/gluons"

    public_api_url: str = "http://localhost:8000"
    public_web_url: str = "http://localhost:3000"
    allowed_web_origins: str = "http://localhost:3000"

    vault_kek_b64: str = DEV_VAULT_KEK_B64
    kms_key_name: str | None = None
    signing_key_b64: str = DEV_SIGNING_KEY_B64

    session_ttl_hours: int = 72
    session_cookie_name: str = "gluons_session"
    mfa_required_for_owners: bool = False

    connection_session_ttl_seconds: int = 600
    setup_link_ttl_seconds: int = 900
    oauth_access_token_ttl_seconds: int = 3600
    oauth_refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30
    approval_default_ttl_seconds: int = 60 * 60 * 24

    mail_transport: Literal["file", "smtp"] = "file"
    mail_dir: str = ".gluons-data/mail"
    smtp_url: str | None = None
    mail_from: str = "Gluons <no-reply@gluons.example>"

    queue_backend: Literal["postgres", "cloud_tasks"] = "postgres"
    worker_lease_seconds: int = 120
    worker_poll_seconds: float = 1.0
    gcp_project_id: str | None = None
    gcp_region: str = "us-central1"
    cloud_tasks_queue: str | None = None
    worker_wake_url: str | None = None
    worker_service_account: str | None = None
    pubsub_events_topic: str | None = None
    artifacts_bucket: str | None = None

    google_client_id: str | None = None
    google_client_secret: str | None = None
    gmail_pubsub_topic: str | None = None
    gmail_push_audience: str | None = None
    gmail_push_service_account: str | None = None

    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_config_id: str | None = None
    meta_webhook_verify_token: str | None = None
    meta_graph_version: str = "v26.0"

    elevenlabs_api_key: str | None = None
    elevenlabs_webhook_secret: str | None = None
    elevenlabs_tool_secret: str | None = None

    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None

    shopify_client_id: str | None = None
    shopify_client_secret: str | None = None
    shopify_api_version: str = "2026-10"
    shopify_pilot_shops: str = ""

    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    shippo_api_token: str | None = None

    erpnext_base_url: str | None = None
    sandbox_erp_url: str = "http://127.0.0.1:8100"

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_genai_project: str | None = None
    private_llm_base_url: str | None = None
    private_llm_api_key: str | None = None

    skills_dir: str = str(REPO_ROOT / "packages" / "skills")
    blueprint_dir: str = str(REPO_ROOT / "gluons_blueprint")

    @property
    def hosted(self) -> bool:
        return self.env in ("staging", "production")

    @property
    def web_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_web_origins.split(",") if o.strip()]

    def key_bytes(self, value: str) -> bytes:
        raw = base64.b64decode(value)
        if len(raw) != 32:
            raise ValueError("expected a base64-encoded 32 byte key")
        return raw


def validate_for_environment(s: Settings) -> None:
    """Refuse to serve a hosted environment with development material."""
    if not s.hosted:
        return
    problems: list[str] = []
    if s.vault_kek_b64 == DEV_VAULT_KEK_B64 and not s.kms_key_name:
        problems.append("GLUONS_KMS_KEY_NAME (or a non-default GLUONS_VAULT_KEK_B64) is required")
    if s.signing_key_b64 == DEV_SIGNING_KEY_B64:
        problems.append("GLUONS_SIGNING_KEY_B64 must not be the development key")
    for name, url in (("GLUONS_PUBLIC_API_URL", s.public_api_url), ("GLUONS_PUBLIC_WEB_URL", s.public_web_url)):
        if not url.startswith("https://"):
            problems.append(f"{name} must be https")
    if problems:
        raise RuntimeError("Unsafe configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    validate_for_environment(settings)
    return settings
