> **CRITICAL ADDENDUM 2026-11-02:** See `13_permissions_connections_and_mobile_onboarding.md` for mandatory four-layer authorization, mobile Gluons-PWA fallback, specific Gmail/Meta consent flow, external connection-session APIs, phone-network routing, and 19 release-blocking tests. The existing OAuth descriptions in this document alone are **not sufficient** for implementation. Current ChatGPT custom MCP connection support is web-only; do not assume mobile tool access.

# 02 — Production architecture, integration contracts, and execution design

## Deployment topology on Google Cloud
**Edge/control:** Cloud Load Balancing + Cloud Armor → Cloud Run `api-gateway` (FastAPI or TypeScript) + `mcp-server` (official MCP SDK) + `web-app` (Next.js). Authenticate through Identity Platform/managed OIDC; implement OAuth resource-server flow appropriate for MCP revision, CIMD client metadata where supported, PKCE and user-specific consent. Use custom tenant RBAC and short-lived service identities.

**Persistent coordination:** Cloud SQL PostgreSQL (tenant-aware transaction/approval/workflow registry, relational source of truth), Cloud Storage (signed input/output artifacts and immutable evidence), Pub/Sub (fanout domain events), Cloud Tasks (delayed per-action retries, quotas), Cloud Scheduler (cron), Cloud Run worker pools or services (connector workers, model inference calls), and Workflows (durable externally orchestrated sequences). Google Workflows supports long-running execution up to one year, but Gluons must persist business state independently; execution metadata retention isn't a permanent audit store. Favor short, idempotent substeps and explicit waits for external approvals.

**Other services:** Secret Manager (third-party API tokens; separate per-tenant encryption envelope when necessary), Cloud KMS, Logging/Monitoring/Trace, Artifact Registry, Cloud Build/GitHub Actions, BigQuery for aggregate opt-in telemetry, Redis/Memorystore for ephemeral rate limiting only. Use PostgreSQL `pgvector` for catalog/business document search where necessary; permission-filter retrieval by tenant and source.

**Isolation:** Shared control-plane database with strictly enforced tenant foreign keys and PostgreSQL row-level security for standard tenants; sensitive tenants can use dedicated DB/project based on actual isolation requirements. Tenant ID never accepted from model-provided body as authority; derive it from authenticated principal and verified connector token. All queues and storage objects carry tenant-scoped identity. Privileged connector services run with minimum IAM. Multi-region residency only where actually provisioned.

**Recommended service modules (logical modules, not premature microservices):** `identity`, `organizations`, `connector_registry`, `skills_registry`, `planner`, `policy_engine`, `task_manager`, `run_orchestrator`, `event_ingest`, `model_gateway`, `voice_gateway`, `artifact_store`, `approvals`, `audit`, `usage_ledger`, `trial_analytics`, `mcp_bridge`, `support_console`. Begin as modular monolith + independently scaling workers; split services at proven bottlenecks.

## Engineering ownership: model versus runtime
- Host AI: understands owner's natural-language intent; selects Gluons tools; explains response.
- Gluons planner: creates structured workflow plan from approved skill and connection map; returns versioned preview.
- Policy engine: deterministic, signed, server-side authorization on every side effect; ignores model attempts to override policy.
- Workflow runtime: manages state/retries/timers/compensations independently of open chat session.
- Specialized model: only used for extraction, language, classification and uncertain judgments with test-based routing; no raw credentials.
- Integration adapter: does idempotent authorized API action, verifies external result, logs receipt.

## Canonical API contracts
REST base `/v1`; MCP layer wraps the same strongly typed functions. All writing operations require `Idempotency-Key` and `expected_version`. Common response `{id, tenant_id, status, version, created_at, next_required_action, links}`. Standard errors: `AUTH_REQUIRED`, `CONNECTION_REQUIRED`, `POLICY_DENIED`, `APPROVAL_REQUIRED`, `CONFLICT_VERSION`, `RATE_LIMITED`, `INSUFFICIENT_EVIDENCE`, `EXTERNAL_UNAVAILABLE`, `HUMAN_REVIEW_REQUIRED`.

- `GET /v1/skills?industry=&capability=` → discover skills, supported modes, readiness, connector dependencies.
- `GET /v1/skills/{id}` → skill metadata, privacy notice, workflow plan template, current certification and known limitations.
- `POST /v1/discover` → owner-aware recommendations based on connected tools and consented facts.
- `POST /v1/plans` → validate skill, inputs, target system, owner policy; produce a typed plan with graph and cost forecast.
- `POST /v1/connections/{provider}/authorize` → return scoped OAuth redirect or supported secure instruction; never ask host chat for passwords.
- `GET /v1/connections` and `DELETE /v1/connections/{id}` → health/scopes/revoke with explicit effects.
- `POST /v1/automations` → create a versioned recurring automation from approved plan.
- `POST /v1/automations/{id}/activate` → activate in replay/shadow/supervised/live mode as permitted.
- `POST /v1/automations/{id}/pause|resume|cancel` → enqueue lifecycle mutation with version checks and audit.
- `GET /v1/automations/{id}` and `/runs` → status, pending approvals, receipts, metrics, costs.
- `POST /v1/approvals/{id}/decide` → allow/deny with signed identity, expiry and reason; dual-control for high risk.
- `POST /v1/automations/{id}/policy-changes` → policy diff and restricted approval workflow.
- `POST /v1/trials` → create trial with explicit comparator and baseline; `/trials/{id}/results` reports evidence.
- `GET /v1/billing/usage` → provider-level metered cost by tenant/skill/run.
- `POST /v1/webhooks/{provider}` → signature validation, replay defense, raw event hash, dedup and handoff.

## MCP design
Target the current MCP standard where available (2026-07-28) using Streamable HTTP and OAuth. Expose **small, typed universal tools**, not 31x50 individual tools: `discover_automations`, `inspect_automation`, `analyze_business`, `create_plan`, `estimate_cost`, `start_trial`, `activate_automation`, `get_task_status`, `list_running_tasks`, `set_task_policy`, `approve_action`, `pause_task`, `resume_task`, `cancel_task`, `get_trial_report`, `get_spending`, `list_connections`, `connect_service`, `get_alerts`. Tool descriptions are narrow, side-effect labels explicit, and each tool response contains link and provenance IDs. Implement optional MCP Tasks extension with fallback polling for host clients that lack it. Support compatibility matrix by ChatGPT plan, Claude plan, local MCP host; do not assume personal ChatGPT hosts allow unrestricted write tools.

### MCP security rules
MCP OAuth client-to-Gluons is distinct from Gluons-to-Gmail/Shopify OAuth. Never send third-party tokens to host model. Sensitive third-party auth uses redirect/URL-mode elicitation where supported or Gluons dashboard. Read-only tool defaults; mutation requires specific authenticated tenant and verified approver. Use dynamic scope reduction, signed action intent, expiry and idempotency. Resist SSRF by allowlisted connector URLs and no arbitrary open-web POST.

## Task state design
Entity hierarchy: `Organization -> User/Role -> Connection -> SkillVersion -> AutomationDefinition -> Run -> StepRun -> ExternalActionReceipt`. Approvals, incidents, cost ledger and trial evidence reference run/step IDs. Both recurring automations and their event-triggered runs have status/version. Store one immutable ordered `audit_event` stream per run (sequence number) and transactionally update a compact current-state projection.

Transitions: `draft, configuring, ready, trial_replay, trial_shadow, awaiting_activation, active, waiting_approval, waiting_external, paused, retrying, reconciliation_required, completed, failed, cancel_requested, cancelled`. Implement compare-and-swap version checking. Snapshot at consistent boundaries; prevent re-entry into canceled runs. A recurring automation remains `active` while many job runs reach `completed`.

Idempotency: unique `(tenant_id, provider, source_event_id)` on incoming signals; unique `(tenant_id, action_type, business_key)` on external writes; provider idempotency key where supported. If call times out, query external source of truth before retry. Cloud Tasks/PubSub are at-least-once and require independent deduplication. Use outbox table within DB transaction to avoid update-without-publish inconsistency. Compensating actions are explicit, not automatic 'rollback' of externally visible emails or calls.

## Model gateway and routers
Provider descriptors contain `model_id, provider, capabilities (text/vision/tool/reasoning), throughput, context_limit, data_region, price_by_token, cache_behavior, max_latency_s, eval_pass_by_skill, retention, availability`. Refresh versioned public cost records; meter actual vendor responses. Routing objectives `(total expected cost, latency, quality threshold, risk, customer/provider constraints)` rather than lowest price only. Use cost-efficient model for narrow extraction, larger model for complex ambiguity, multimodal model for scanned images, deterministic functions for arithmetic and policy. Critical steps require confidence/calibration and source-grounding evaluation; don't trust uncalibrated LLM self-confidence.

Provider adapters: Vertex Gemini, Anthropic, OpenAI, local/private vLLM/Ollama. BYOK options require clear responsibility for vendor spend and security; do not run arbitrary untrusted customer code in shared privileged workers. Protect outbound provider calls from customer data exfiltration through explicit allowed provider list and data classification.

## ElevenLabs voice integration (real operations)
1. Create ElevenAgents templates by domain, distinct tenant agent ID/variables and approved skill-specific tool webhooks.
2. Connect telephone via ElevenLabs native Twilio integration or SIP. If requiring advanced custom telephony routing, Twilio `register-call` is supported but **does not support call transfer through that register-call method**; select native integration when transfers are required.
3. On inbound caller, provide limited dynamic variables from an authenticated Gluons conversation-init webhook; avoid inserting secrets or unrelated customer records into prompts.
4. Real-time voice tools hit `voice-action-gateway` and are subject to the **same** policy engine as MCP tools: `lookup_order`, `check_availability`, `draft_order`, `confirm_order`, `transfer_to_human` etc. Never trust model claims that a transaction was committed.
5. Verify ElevenLabs HMAC signatures on post-call webhooks (do not trust unauthenticated callbacks), dedupe by `conversation_id` + event type, and process asynchronously. Preserve metadata, transcript only under retention policy. Handle webhook retries and disabled webhooks.
6. Publish business task events with `source=voice`, provider call ID, cost and verification receipts. Support consent, call recording rules, languages and transfer routes by region. For payments use PCI-compliant hosted links; do not collect card numbers through prompts/transcripts.

Official docs: https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/register-call ; https://elevenlabs.io/docs/eleven-agents/workflows/post-call-webhooks ; https://elevenlabs.io/pricing/agents

## Connector capability contracts
Every provider connector must expose `auth_scopes, endpoints, read/write mode, source-of-truth readback, idempotency support, event ingestion, rate limits, sandbox, PII categories, fallback strategy, operational status`. Adapter functions use normalized domain contracts and mapping tables. Required connector packs: Gmail/Google Calendar/Google Drive (respect restricted scopes), Microsoft 365, Shopify, WooCommerce, HubSpot, Salesforce, QuickBooks, Xero, Stripe, ElevenLabs, Twilio, Meta WhatsApp, one accessible POS, one ERP/TMS sandbox, mapping/route API, document OCR, GCS. Vendor partnership or commercial integration restrictions may apply (e.g. restaurant POS/MLS/insurance carriers).

## Minimal database tables
`tenants`, `users`, `memberships`, `roles`, `connector_accounts`, `oauth_grants`, `skills`, `skill_versions`, `skill_certifications`, `business_policies`, `plans`, `automations`, `runs`, `step_runs`, `run_events`, `approvals`, `action_receipts`, `usage_events`, `trial_baselines`, `trial_measurements`, `model_provider_accounts`, `incidents`, `artifact_blobs`, `webhook_inbox`, `outbox`, `support_tickets`. All sensitive rows have tenant identifiers and retention class. Distinguish internal permission grants from external OAuth grants. Add unique indexes for external event IDs and idempotency keys, state/version, cost ledger and owner approvals.

## Interoperability and privacy
- ChatGPT: plugin built from skill + MCP server + optional UI, subject to product plan and app review. Build a Gluons dashboard/native assistant fallback for unsupported hosts.
- Claude: remote MCP with OAuth-based custom connector, supported account tiers vary; maintain feature probes.
- Open source: local MCP client using remote protected Gluons MCP endpoint, or deploy a customer-managed connector in private infrastructure; infer locally only if all model calls really stay local. Explicitly disclose any data still sent to third-party connectors or hosted Gluons.
- Data security: envelope encryption, per-tenant RBAC, approval chains, redaction, regional retention, GDPR/DPDP/local compliance where applicable, audit export. Review HIPAA/BAA, CAN-SPAM/TCPA, local telephone recording, employment and insurance restrictions by deployment geography. Regulatory clearance is a prerequisite, not a feature toggle.

## Security and reliability gate
OWASP Agentic AI Top 10, prompt injection red team, cross-tenant data isolation test, webhook authenticity, leaked OAuth token tests, signed policies, duplicate retries, provider drift and 3rd-party API failure tests. Every run with side effects needs upstream readback. SLO candidates: >=99.9% control plane, 100% approval enforcement, zero accepted cross-tenant accesses, near-zero unintended duplicate external writes, error budgets measured over real pilot data. Declare target SLOs provisional, not actual results.
