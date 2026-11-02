"""Relational source of truth. Hierarchy (blueprint doc 02):

Organization -> User/Role -> Connection -> SkillVersion -> Automation -> Run -> StepRun -> ActionReceipt

`tenant_id` always references organizations.id and is never accepted from a request
body: it comes from the authenticated principal. Row level security is applied in
migration 0002 to every table listed in TENANT_TABLES.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _ts(nullable: bool = True) -> Mapped[Any]:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


def _tenant() -> Mapped[str]:
    return mapped_column(String(40), ForeignKey("organizations.id"), nullable=False, index=True)


def _json(default: str = "{}") -> Mapped[Any]:
    return mapped_column(JSONB, nullable=False, server_default=text(f"'{default}'::jsonb"))


# --- Global: identity, catalog -------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    email_verified_at: Mapped[datetime | None] = _ts()
    password_hash: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    mfa_secret_ref: Mapped[str | None] = mapped_column(Text)
    mfa_enabled_at: Mapped[datetime | None] = _ts()
    mfa_recovery_hashes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = _ts()
    disabled_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()


class UserToken(Base):
    """Single-use email verification, password reset and invitation links."""

    __tablename__ = "user_tokens"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = _ts(False)
    used_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()


class IdentitySession(Base):
    """Browser sign-in. Authority for owner decisions; never derived from a chat."""

    __tablename__ = "identity_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active_tenant_id: Mapped[str | None] = mapped_column(String(40))
    mfa_verified_at: Mapped[datetime | None] = _ts()
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime | None] = _ts()
    expires_at: Mapped[datetime] = _ts(False)
    revoked_at: Mapped[datetime | None] = _ts()


class OAuthClient(Base):
    """An MCP host registered with the Gluons authorization server."""

    __tablename__ = "oauth_clients"
    id: Mapped[str] = mapped_column(String(300), primary_key=True)
    registration: Mapped[str] = mapped_column(String(20), nullable=False)  # dcr | cimd | preregistered
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    host_kind: Mapped[str] = mapped_column(String(40), nullable=False, server_default="other")
    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    token_endpoint_auth_method: Mapped[str] = mapped_column(String(40), nullable=False, server_default="none")
    client_secret_hash: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = _json()
    created_at: Mapped[datetime] = _created()
    disabled_at: Mapped[datetime | None] = _ts()


class OAuthAuthorizationRequest(Base):
    """Pending /authorize, held until the owner signs in and consents in Gluons Web."""

    __tablename__ = "oauth_authorization_requests"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.id"), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(10), nullable=False)
    code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    tenant_id: Mapped[str | None] = mapped_column(String(40))
    binding_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    expires_at: Mapped[datetime] = _ts(False)
    code_expires_at: Mapped[datetime | None] = _ts()
    code_used_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()


class SkillVersion(Base):
    """Validated skill contract, pinned by hash. Automations reference a version."""

    __tablename__ = "skill_versions"
    skill_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    blueprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract: Mapped[dict] = _json()
    registered_at: Mapped[datetime] = _created()


class SkillEvidence(Base):
    """What was actually verified for a skill. Capability status is derived from these rows."""

    __tablename__ = "skill_evidence"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # eval_suite | sandbox_receipt | live_receipt | domain_review
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail: Mapped[dict] = _json()
    recorded_by: Mapped[str] = mapped_column(String(200), nullable=False)
    recorded_at: Mapped[datetime] = _created()


class ModelDescriptor(Base):
    """Versioned, dated model facts. Prices are effective-dated, never overwritten."""

    __tablename__ = "model_descriptors"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    tier: Mapped[str] = mapped_column(String(40), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    context_limit: Mapped[int | None] = mapped_column(Integer)
    data_region: Mapped[str | None] = mapped_column(String(60))
    retention_note: Mapped[str | None] = mapped_column(Text)
    input_usd_per_mtok: Mapped[Any] = mapped_column(Numeric(12, 4), nullable=False, server_default="0")
    output_usd_per_mtok: Mapped[Any] = mapped_column(Numeric(12, 4), nullable=False, server_default="0")
    cache_read_usd_per_mtok: Mapped[Any | None] = mapped_column(Numeric(12, 4))
    cache_write_usd_per_mtok: Mapped[Any | None] = mapped_column(Numeric(12, 4))
    price_source: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[datetime] = _created()
    retired_at: Mapped[datetime | None] = _ts()
    __table_args__ = (UniqueConstraint("provider", "model_id", "effective_from"),)


class ModelEvalResult(Base):
    """Observed quality per skill. Routing refuses a model with no passing result."""

    __tablename__ = "model_eval_results"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    suite_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[Any] = mapped_column(Numeric(6, 4), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mean_latency_ms: Mapped[int | None] = mapped_column(Integer)
    mean_cost_usd: Mapped[Any | None] = mapped_column(Numeric(12, 6))
    detail: Mapped[dict] = _json()
    evaluated_at: Mapped[datetime] = _created()


# --- Queue and event plumbing (tenant_id nullable: system work has no tenant) ---------


class Job(Base):
    """Durable work queue. At-least-once: every handler must be idempotent."""

    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = _json()
    dedupe_key: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="6")
    leased_by: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = _ts()
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = _ts()
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_at"),
        Index(
            "uq_jobs_active_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key is not null and status in ('queued','leased')"),
        ),
    )


class OutboxEvent(Base):
    """Written in the same transaction as the state change it announces."""

    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(40), index=True)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = _json()
    created_at: Mapped[datetime] = _created()
    published_at: Mapped[datetime | None] = _ts()
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    __table_args__ = (Index("ix_outbox_unpublished", "created_at", postgresql_where=text("published_at is null")),)


class WebhookInbox(Base):
    """Raw inbound event record. The unique key is what makes replays harmless."""

    __tablename__ = "webhook_inbox"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    event_id: Mapped[str] = mapped_column(String(300), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(40), index=True)
    connection_id: Mapped[str | None] = mapped_column(String(40))
    event_type: Mapped[str | None] = mapped_column(String(100))
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = _json()
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="received")
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = _created()
    processed_at: Mapped[datetime | None] = _ts()
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),)


# --- Tenancy ---------------------------------------------------------------------------


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(300))
    industry: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(2))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    business_facts: Mapped[dict] = _json()
    emergency_stop_at: Mapped[datetime | None] = _ts()
    emergency_stop_by: Mapped[str | None] = mapped_column(String(40))
    paused_categories: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    allowed_model_providers: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    monthly_budget_usd: Mapped[Any | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = _created()
    deleted_at: Mapped[datetime | None] = _ts()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_tier_limit: Mapped[str | None] = mapped_column(String(4))
    invited_by: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _created()
    removed_at: Mapped[datetime | None] = _ts()
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_membership"),)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[datetime] = _ts(False)
    accepted_at: Mapped[datetime | None] = _ts()
    revoked_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()


class AssistSession(Base):
    """Field representative assisted onboarding. Consent-based, time boxed, audited."""

    __tablename__ = "assist_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    rep_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    owner_consent_by: Mapped[str | None] = mapped_column(String(40))
    owner_consent_at: Mapped[datetime | None] = _ts()
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="requested")
    checklist: Mapped[dict] = _json()
    completion_report: Mapped[dict | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = _ts(False)
    created_at: Mapped[datetime] = _created()
    ended_at: Mapped[datetime | None] = _ts()


class AgentBinding(Base):
    """One authenticated assistant installation bound to one member in one organization.

    A user can hold many; revoking one leaves the rest working. Never derived from a chat id.
    """

    __tablename__ = "agent_bindings"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    host_kind: Mapped[str] = mapped_column(String(40), nullable=False, server_default="other")
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    audience: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_approval_max_tier: Mapped[str | None] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime | None] = _ts()
    revoked_at: Mapped[datetime | None] = _ts()
    revoked_by: Mapped[str | None] = mapped_column(String(40))


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    binding_id: Mapped[str] = mapped_column(ForeignKey("agent_bindings.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # access | refresh
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    family_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    audience: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = _ts(False)
    used_at: Mapped[datetime | None] = _ts()
    revoked_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()


# --- Provider connections --------------------------------------------------------------


class VaultSecret(Base):
    """Envelope-encrypted credential. Ciphertext is bound to tenant and secret id."""

    __tablename__ = "vault_secrets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    purpose: Mapped[str] = mapped_column(String(60), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    wrapped_dek: Mapped[str] = mapped_column(Text, nullable=False)
    kek_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = _created()
    rotated_at: Mapped[datetime | None] = _ts()
    destroyed_at: Mapped[datetime | None] = _ts()


class ConnectionSession(Base):
    """One-time provider onboarding session. Opening the link never grants authority."""

    __tablename__ = "connection_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    initiated_by: Mapped[str] = mapped_column(String(40), nullable=False)
    initiated_via: Mapped[str] = mapped_column(String(20), nullable=False, server_default="web")
    binding_id: Mapped[str | None] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    requested_capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    requested_scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    automation_plan_id: Mapped[str | None] = mapped_column(String(40))
    setup_handle_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    state_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    pkce_verifier_ref: Mapped[str | None] = mapped_column(String(40))
    nonce_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), nullable=False, server_default="requires_external_authorization")
    connection_id: Mapped[str | None] = mapped_column(String(40))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = _json()
    expires_at: Mapped[datetime] = _ts(False)
    consumed_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()


class Connection(Base):
    """Provider-level access. Separate from any permission to act (doc 13 section 5)."""

    __tablename__ = "connections"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False, server_default="production")
    external_account_id: Mapped[str] = mapped_column(String(300), nullable=False)
    account_display: Mapped[str] = mapped_column(String(300), nullable=False)
    account_confirmed_at: Mapped[datetime | None] = _ts()
    account_confirmed_by: Mapped[str | None] = mapped_column(String(40))
    granted_scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    credential_ref: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), nullable=False, server_default="connected")
    status_detail: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = _ts()
    last_checked_at: Mapped[datetime | None] = _ts()
    last_event_at: Mapped[datetime | None] = _ts()
    metadata_json: Mapped[dict] = _json()
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _created()
    disconnected_at: Mapped[datetime | None] = _ts()
    disconnected_by: Mapped[str | None] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __table_args__ = (
        Index(
            "uq_connection_active_account",
            "tenant_id",
            "provider",
            "external_account_id",
            unique=True,
            postgresql_where=text("disconnected_at is null"),
        ),
    )


class ProviderEventSubscription(Base):
    __tablename__ = "provider_event_subscriptions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    routing_key: Mapped[str] = mapped_column(String(300), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = _ts()
    last_renewed_at: Mapped[datetime | None] = _ts()
    last_event_at: Mapped[datetime | None] = _ts()
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    __table_args__ = (Index("ix_subscription_routing", "topic", "routing_key"),)


class PhoneRoute(Base):
    __tablename__ = "phone_routes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    route_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, server_default="not_configured")
    provider_number_id: Mapped[str | None] = mapped_column(String(200))
    forwarded_from_number: Mapped[str | None] = mapped_column(String(32))
    voice_agent_id: Mapped[str | None] = mapped_column(String(200))
    transfer_number: Mapped[str | None] = mapped_column(String(32))
    business_hours: Mapped[dict] = _json()
    disclosure_text: Mapped[str | None] = mapped_column(Text)
    ownership_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    test_call_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    test_call_id: Mapped[str | None] = mapped_column(String(200))
    test_started_at: Mapped[datetime | None] = _ts()
    routing_instructions: Mapped[str | None] = mapped_column(Text)
    automation_id: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _created()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


# --- Plans, automations, policy --------------------------------------------------------


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False)
    skill_version: Mapped[str] = mapped_column(String(40), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text)
    business_context: Mapped[dict] = _json()
    graph: Mapped[dict] = _json()
    required_connections: Mapped[list] = _json("[]")
    side_effects: Mapped[list] = _json("[]")
    cost_estimate: Mapped[dict] = _json()
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_via: Mapped[str] = mapped_column(String(20), nullable=False, server_default="web")
    created_at: Mapped[datetime] = _created()


class Automation(Base):
    """A recurring automation (the owner-facing 'task'). Many runs hang off one row."""

    __tablename__ = "automations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"))
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    skill_version: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="shadow")
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="draft")
    risk_tier: Mapped[str] = mapped_column(String(4), nullable=False)
    config: Mapped[dict] = _json()
    source_connections: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    trigger: Mapped[dict] = _json()
    monthly_budget_usd: Mapped[Any | None] = mapped_column(Numeric(12, 2))
    per_action_limit_usd: Mapped[Any | None] = mapped_column(Numeric(12, 2))
    model_policy: Mapped[dict] = _json()
    current_policy_id: Mapped[str | None] = mapped_column(String(40))
    execution_backend: Mapped[str] = mapped_column(String(40), nullable=False, server_default="python-actions")
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_via: Mapped[str] = mapped_column(String(20), nullable=False, server_default="web")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class AutomationPolicy(Base):
    """Owner-signed grant. Versions are immutable: a change creates a new row."""

    __tablename__ = "automation_policies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    automation_id: Mapped[str] = mapped_column(ForeignKey("automations.id"), nullable=False, index=True)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="proposed")
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    allowed_actions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    connection_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    resource_filters: Mapped[dict] = _json()
    requires_approval_for: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    auto_limits: Mapped[dict] = _json()
    spend_limit_minor_per_month: Mapped[int | None] = mapped_column(BigInteger)
    per_action_limit_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    data_disclosures: Mapped[list] = _json("[]")
    approved_by: Mapped[str | None] = mapped_column(String(40))
    approved_at: Mapped[datetime | None] = _ts()
    expires_at: Mapped[datetime | None] = _ts()
    signature: Mapped[str | None] = mapped_column(String(64))
    supersedes: Mapped[str | None] = mapped_column(String(40))
    revoked_at: Mapped[datetime | None] = _ts()
    revoked_by: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("automation_id", "policy_version", name="uq_policy_version"),)


class Run(Base):
    """One event-triggered execution. The checkpoint survives any worker restart."""

    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    automation_id: Mapped[str] = mapped_column(ForeignKey("automations.id"), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False)
    skill_version: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_id: Mapped[str | None] = mapped_column(String(40))
    policy_version: Mapped[int | None] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="queued")
    source: Mapped[str] = mapped_column(String(40), nullable=False, server_default="manual")
    source_event_id: Mapped[str] = mapped_column(String(300), nullable=False)
    input: Mapped[dict] = _json()
    state: Mapped[dict] = _json()
    cursor: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[dict] = _json()
    error: Mapped[str | None] = mapped_column(Text)
    leased_by: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = _ts()
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[Any] = mapped_column(Numeric(14, 6), nullable=False, server_default="0")
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = _ts()
    finished_at: Mapped[datetime | None] = _ts()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __table_args__ = (
        UniqueConstraint("tenant_id", "automation_id", "source_event_id", name="uq_run_source_event"),
        Index("ix_runs_status", "tenant_id", "status"),
    )


class StepRun(Base):
    __tablename__ = "step_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    primitive: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="running")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    output: Mapped[dict] = _json()
    error: Mapped[str | None] = mapped_column(Text)
    model_identifier: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = _ts()
    __table_args__ = (UniqueConstraint("run_id", "node_id", name="uq_step_node"),)


class RunEvent(Base):
    """Immutable ordered stream per run. Update and delete are blocked by trigger."""

    __tablename__ = "run_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    engine: Mapped[str] = mapped_column(String(60), nullable=False, server_default="python-actions")
    engine_version: Mapped[str] = mapped_column(String(60), nullable=False, server_default="0")
    data: Mapped[dict] = _json()
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),)


class Approval(Base):
    """Durable, expiring proposal bound to the exact action payload by fingerprint."""

    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    kind: Mapped[str] = mapped_column(String(20), nullable=False, server_default="action")  # action | review
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    automation_id: Mapped[str | None] = mapped_column(String(40))
    node_id: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(4), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    external_system: Mapped[str | None] = mapped_column(String(100))
    connection_id: Mapped[str | None] = mapped_column(String(40))
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(300))
    records_before: Mapped[dict | None] = mapped_column(JSONB)
    records_after_preview: Mapped[dict] = _json()
    source_evidence: Mapped[list] = _json("[]")
    estimated_cost_usd: Mapped[Any | None] = mapped_column(Numeric(12, 6))
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    policy_reference: Mapped[str | None] = mapped_column(String(100))
    required_role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="approver")
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    requested_by: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    resolution: Mapped[dict | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = _ts(False)
    decided_at: Mapped[datetime | None] = _ts()
    consumed_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __table_args__ = (Index("ix_approvals_inbox", "tenant_id", "status", "expires_at"),)


class ApprovalDecision(Base):
    """One signed decision per person. High-risk approvals need several distinct people."""

    __tablename__ = "approval_decisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    via: Mapped[str] = mapped_column(String(20), nullable=False, server_default="web")
    binding_id: Mapped[str | None] = mapped_column(String(40))
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("approval_id", "user_id", name="uq_approval_decider"),)


class ActionAuthorization(Base):
    """Every policy evaluation of a side effect, allow or deny (doc 13 section 5)."""

    __tablename__ = "action_authorizations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    automation_id: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    policy_id: Mapped[str | None] = mapped_column(String(40))
    policy_version: Mapped[int | None] = mapped_column(Integer)
    approval_id: Mapped[str | None] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    connection_id: Mapped[str | None] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime] = _created()


class ActionReceipt(Base):
    """Write-ahead record of an external side effect. Unique key prevents a second write."""

    __tablename__ = "action_receipts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    step_id: Mapped[str | None] = mapped_column(String(40))
    automation_id: Mapped[str | None] = mapped_column(String(40))
    skill_id: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False, server_default="simulator")
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    connection_id: Mapped[str | None] = mapped_column(String(40))
    provider: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    verified_at: Mapped[datetime | None] = _ts()
    provider_object_id: Mapped[str | None] = mapped_column(String(300))
    provider_request_id: Mapped[str | None] = mapped_column(String(300))
    receipt: Mapped[dict] = _json()
    expected: Mapped[dict] = _json()
    readback: Mapped[dict | None] = mapped_column(JSONB)
    warnings: Mapped[list] = _json("[]")
    approval_id: Mapped[str | None] = mapped_column(String(40))
    policy_id: Mapped[str | None] = mapped_column(String(40), index=True)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    irreversible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    cost_usd: Mapped[Any | None] = mapped_column(Numeric(14, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("tenant_id", "action", "idempotency_key", name="uq_action_idempotency"),)


# --- Trials, usage, audit ---------------------------------------------------------------


class Trial(Base):
    __tablename__ = "trials"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    automation_id: Mapped[str] = mapped_column(ForeignKey("automations.id"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")
    comparator: Mapped[dict] = _json()
    baseline: Mapped[dict] = _json()
    budget_usd: Mapped[Any | None] = mapped_column(Numeric(12, 2))
    expires_at: Mapped[datetime | None] = _ts()
    results: Mapped[dict] = _json()
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = _ts()


class TrialMeasurement(Base):
    """Basis is mandatory: 'measured', 'estimated' or 'unverified' never blur together."""

    __tablename__ = "trial_measurements"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    trial_id: Mapped[str] = mapped_column(ForeignKey("trials.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(40))
    metric: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Any] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    basis: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = _created()


class UsageEvent(Base):
    """Append-only metering. The dedupe key stops a replay from double charging."""

    __tablename__ = "usage_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    dedupe_key: Mapped[str] = mapped_column(String(300), nullable=False)
    automation_id: Mapped[str | None] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    step_id: Mapped[str | None] = mapped_column(String(40))
    skill_id: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    meter: Mapped[str] = mapped_column(String(60), nullable=False)
    quantity: Mapped[Any] = mapped_column(Numeric(18, 6), nullable=False)
    unit_price_usd: Mapped[Any | None] = mapped_column(Numeric(18, 9))
    cost_usd: Mapped[Any] = mapped_column(Numeric(14, 6), nullable=False)
    cost_basis: Mapped[str] = mapped_column(String(20), nullable=False, server_default="estimated")
    price_version: Mapped[str | None] = mapped_column(String(100))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict] = _json()
    occurred_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("tenant_id", "dedupe_key", name="uq_usage_dedupe"),)


class BudgetHold(Base):
    """Reserves estimated spend before a metered call so concurrent runs cannot overshoot."""

    __tablename__ = "budget_holds"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    automation_id: Mapped[str | None] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40))
    amount_usd: Mapped[Any] = mapped_column(Numeric(14, 6), nullable=False)
    expires_at: Mapped[datetime] = _ts(False)
    created_at: Mapped[datetime] = _created()


class ApiIdempotency(Base):
    """Replays a stored response when a client retries a write with the same key."""

    __tablename__ = "api_idempotency"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_api_idempotency"),)


class AuditEvent(Base):
    """Immutable, hash-chained per tenant. Update and delete are blocked by trigger."""

    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(100))
    binding_id: Mapped[str | None] = mapped_column(String(40))
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(300))
    decision: Mapped[str | None] = mapped_column(String(40))
    data: Mapped[dict] = _json()
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("tenant_id", "seq", name="uq_audit_seq"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    severity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="info")
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    link: Mapped[str | None] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(300))
    read_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()


class Incident(Base):
    """Operator intervention queue: dead letters, reconciliation mismatches, injection flags."""

    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="warning")
    run_id: Mapped[str | None] = mapped_column(String(40))
    automation_id: Mapped[str | None] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    diagnosis: Mapped[dict] = _json()
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    resolved_by: Mapped[str | None] = mapped_column(String(40))
    resolved_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()


# --- Business data shared by channels and skills ----------------------------------------


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(300))
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    retention_class: Mapped[str] = mapped_column(String(30), nullable=False, server_default="standard")
    created_at: Mapped[datetime] = _created()


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, server_default="note")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    audience: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class CatalogItem(Base):
    """Tenant product catalog mirror used for deterministic SKU matching."""

    __tablename__ = "catalog_items"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    in_stock: Mapped[Any | None] = mapped_column(Numeric(14, 3))
    source: Mapped[str] = mapped_column(String(40), nullable=False, server_default="manual")
    last_synced_at: Mapped[datetime | None] = _ts()
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_catalog_sku"),)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    external_id: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    emails: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    phones: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    alias_map: Mapped[dict] = _json()
    opted_in_channels: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    suppressed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    source: Mapped[str] = mapped_column(String(40), nullable=False, server_default="manual")
    created_at: Mapped[datetime] = _created()


class Order(Base):
    """Shared transaction record: email, WhatsApp and voice all converge on this."""

    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(300), nullable=False)
    business_key: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="draft")
    lines: Mapped[list] = _json("[]")
    total_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    external_order_id: Mapped[str | None] = mapped_column(String(200))
    receipt_id: Mapped[str | None] = mapped_column(String(40))
    review_reasons: Mapped[list] = _json("[]")
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("tenant_id", "business_key", name="uq_order_business_key"),)


class ChannelThread(Base):
    __tablename__ = "channel_threads"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    connection_id: Mapped[str | None] = mapped_column(String(40))
    external_thread_id: Mapped[str] = mapped_column(String(300), nullable=False)
    counterparty: Mapped[str] = mapped_column(String(320), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    service_window_expires_at: Mapped[datetime | None] = _ts()
    last_message_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("tenant_id", "channel", "external_thread_id", name="uq_channel_thread"),)


class ChannelMessage(Base):
    __tablename__ = "channel_messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    thread_id: Mapped[str] = mapped_column(ForeignKey("channel_threads.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(300))
    sender: Mapped[str | None] = mapped_column(String(320))
    recipient: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="received")
    classification: Mapped[str | None] = mapped_column(String(60))
    attachments: Mapped[list] = _json("[]")
    provider_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("tenant_id", "channel", "external_message_id", name="uq_channel_message"),)


class Call(Base):
    __tablename__ = "calls"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    phone_route_id: Mapped[str | None] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40), nullable=False, server_default="elevenlabs")
    conversation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_call_id: Mapped[str | None] = mapped_column(String(200))
    caller: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="in_progress")
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Any | None] = mapped_column(Numeric(12, 6))
    summary: Mapped[str | None] = mapped_column(Text)
    transcript_ref: Mapped[str | None] = mapped_column(String(40))
    transfer_outcome: Mapped[str | None] = mapped_column(String(60))
    run_id: Mapped[str | None] = mapped_column(String(40))
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    started_at: Mapped[datetime] = _created()
    ended_at: Mapped[datetime | None] = _ts()
    __table_args__ = (UniqueConstraint("tenant_id", "conversation_id", name="uq_call_conversation"),)


# --- Business launch --------------------------------------------------------------------


class LaunchPlan(Base):
    __tablename__ = "launch_plans"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(String(2))
    product_category: Mapped[str | None] = mapped_column(String(200))
    budget_usd: Mapped[Any | None] = mapped_column(Numeric(12, 2))
    fulfillment_model: Mapped[str | None] = mapped_column(String(40))
    stage: Mapped[str] = mapped_column(String(50), nullable=False, server_default="INTENT_CAPTURED")
    branch_status: Mapped[str | None] = mapped_column(String(40))
    checklist: Mapped[list] = _json("[]")
    state: Mapped[dict] = _json()
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_via: Mapped[str] = mapped_column(String(20), nullable=False, server_default="web")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class LaunchEvidence(Base):
    __tablename__ = "launch_evidence"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    plan_id: Mapped[str] = mapped_column(ForeignKey("launch_plans.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[dict] = _json()
    receipt_id: Mapped[str | None] = mapped_column(String(40))
    recorded_at: Mapped[datetime] = _created()


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    plan_id: Mapped[str | None] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    website: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    evidence_date: Mapped[datetime | None] = _ts()
    reliability_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="candidate")
    first_contact_approved_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _created()


class SupplierQuote(Base):
    __tablename__ = "supplier_quotes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), nullable=False, index=True)
    product: Mapped[str] = mapped_column(String(300), nullable=False)
    unit_cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    moq: Mapped[int | None] = mapped_column(Integer)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    shipping_minor: Mapped[int | None] = mapped_column(BigInteger)
    duties_minor: Mapped[int | None] = mapped_column(BigInteger)
    terms: Mapped[dict] = _json()
    source_message_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="received")
    created_at: Mapped[datetime] = _created()


# Every table that row level security must cover. A test asserts this list is complete.
TENANT_TABLES: tuple[str, ...] = (
    "memberships",
    "invitations",
    "assist_sessions",
    "agent_bindings",
    "oauth_tokens",
    "vault_secrets",
    "connection_sessions",
    "connections",
    "provider_event_subscriptions",
    "phone_routes",
    "plans",
    "automations",
    "automation_policies",
    "runs",
    "step_runs",
    "run_events",
    "approvals",
    "approval_decisions",
    "action_authorizations",
    "action_receipts",
    "trials",
    "trial_measurements",
    "usage_events",
    "budget_holds",
    "api_idempotency",
    "audit_events",
    "notifications",
    "incidents",
    "artifacts",
    "knowledge_documents",
    "catalog_items",
    "customers",
    "orders",
    "channel_threads",
    "channel_messages",
    "calls",
    "launch_plans",
    "launch_evidence",
    "suppliers",
    "supplier_quotes",
)
