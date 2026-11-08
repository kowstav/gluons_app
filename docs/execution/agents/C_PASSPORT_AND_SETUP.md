# Agent C — Business Passport and setup API

Own EX-05 and backend support for EX-07. Read `Organization`, `ConnectionSession`, `Connection`, `LaunchPlan`, `AgentBinding`, `knowledge.py`, connection services, launch service, data export/deletion and existing API conventions. The November 8 master proposes a richer profile than current `business_facts`.

## Deliverables

1. A short contract decision: typed projection over existing facts versus dedicated versioned profile records, with one authoritative source and migration path.
2. Minimal non-secret profile fields with source/confirmation/freshness/version. Provider import is allowlisted, previewed and confirmed; do not dump arbitrary provider payloads into profiles.
3. Provider-sharing preview/consent tied to fields, purpose, provider and version. Data sharing is not business action approval.
4. Durable setup sessions linking current launch plans and provider connection sessions. One reconnect room reports current prerequisites and owner actions across redirects.
5. Shared JSON contracts and schema fixtures for E/B/F, service-level authorization, REST wrappers, export/deletion handling and tests.

## Acceptance

- Cover SETUP-01 through SETUP-04, AUTH-03 and relevant UI-02 API behavior.
- Tenant/user/org derive from authenticated context. A stolen/expired/replayed setup link cannot grant access or bind another organization.
- Atomic redemption, expected-version conflict handling and idempotent creation prevent duplicate setup state.
- Correct progress survives provider cancel, failure, review delay, browser close, new login and reconnect.
- Existing connections are reused only after account identity, scopes and health satisfy the selected workflow.
- Changing profile fields marks dependent previews stale; no automatic update of provider legal/bank records.
- Sensitive IDs, passwords and secrets are rejected/excluded from general profile APIs, logs and model context.
- Every new tenant table has RLS/grants and every new field has export/deletion/retention semantics.

## Files and coordination

Prefer focused new profile/setup service and route modules. Propose schema changes to A; do not edit central models/migrations concurrently. B owns identity links and MCP transport; E owns UI; D owns provider authorization; F owns readiness. Setup state must refer to these services rather than copying their authority. Get API states/errors agreed before E begins integration.

No provider credentials are needed for typed schema, negative tests or UX fixtures. Mark real redirect verification pending until D supplies actual provider evidence. Do not create a new generic workflow engine for setup.
