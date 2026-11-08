# Architecture and interface guidance

This file proposes additions, not implemented endpoints. Match actual code at the assigned base commit before coding. Existing `docs/ARCHITECTURE_DECISIONS.md` remains authoritative for implementation decisions; record a new ADR when changing one. New founder priorities in Sections 2D/9A guide product requirements without overriding provider constraints or security invariants.

## Preserve the existing authority chain

`web or authenticated MCP → thin route/tool → Principal-aware service → tenant_tx → policy/approval/budget → gateway.execute → GuardedClient connector → independent provider readback → receipt/events → projections`

Postgres owns durable execution. Cloud Tasks wakes workers; it does not become a second task authority. Keep internal draft generation separate from external draft creation. Only the latter requires an external action path. Account-maintenance exceptions are narrowly defined in ADR-024 and must not become a general-purpose bypass for business operations.

## Existing records to reuse

| Existing record/module | Use | Do not duplicate with |
|---|---|---|
| `User`, `IdentitySession`, `Membership` | Canonical person, browser session and current org role | A user per assistant or per provider email |
| `AgentBinding`, `OAuthToken` | Assistant user/org/scopes and revocation | A second assistant grant/token table |
| `ConnectionSession`, `Connection`, vault | Provider-specific authorization and health | A new credential store inside Passport/setup |
| `Organization.business_facts`, `KnowledgeDocument` | Existing business facts and evidence | Blind untyped profile copying |
| `LaunchPlan`, `LaunchEvidence` | Launch intent, stage, evidence | Separate web-only launch state |
| `Automation`, `AutomationPolicy`, `Approval` | Durable action authority | Approval inferred from chat text or onboarding completion |
| `Job`, `Run`, `ActionReceipt`, `UsageEvent` | Durable execution, outcome and cost | Browser timers or LLM transcript as task state |

## Proposed additions

Names are provisional until EX-05 contracts are agreed. Schema changes go through the integrator, with one next Alembic revision and backfill/rollback plan.

### External identity link

`identity_links`: ID, canonical user ID, provider issuer, immutable provider subject, linked time, last authenticated time, disabled/revoked time and minimal verified metadata. Unique `(issuer, subject)`. Email is a display/contact attribute, not the identity key.

- Google sign-in token validation checks signature, expected issuer/audience, expiry and nonce where applicable through a maintained library.
- Explicitly linking a provider to an existing account requires authenticated proof of that Gluons account and the provider identity. Same email alone does not authorize linking.
- Keep the outer assistant OAuth request and inner identity sign-in transaction separate, including state, nonce, PKCE and callback records.
- Do not accept an ID token as an MCP access token; do not issue Gmail access from a Google login.
- Unlinking the last usable authentication method must not strand the user. Recovery and MFA downgrade are explicit reviewed flows.
- Identity tables are global security records with narrow identity-service access; do not mechanically apply tenant filters to user identity records. Tenant profile/setup records must have forced RLS and explicit grants.

### Business Passport

Prefer typed `BusinessProfile` plus field metadata or a versioned typed projection over current business facts. Decide in EX-05 based on migration simplicity, query needs and future multi-brand scope. Do not build two competing authoritative copies.

Minimum v1: organization, brand/business name, country, timezone, currency, business type, selling regions, support contact/hours and optional approved operational addresses. Each reusable fact records value, source kind/reference, confirmation time, confirming actor, sensitivity classification and schema/profile version. Collect legal identifiers only when a supported flow needs them.

Excluded: passwords, access/refresh tokens, card data, bank credentials and identity-document images. Do not import arbitrary provider JSON. Use an allowlist and make source provenance visible. Untrusted extracted facts remain proposed until confirmed.

Proposed service methods:

```python
get_profile(principal) -> ProfileView
patch_profile(principal, patch, expected_version) -> ProfileView
preview_import(principal, connection_id, field_names) -> ImportPreview
confirm_import(principal, preview_id, expected_version) -> ProfileView
preview_provider_share(principal, provider, field_names, purpose) -> SharePreview
confirm_provider_share(principal, preview_id, expected_version) -> ConsentReceipt
```

Sharing consent binds provider, fields, purpose, profile version, expiry and actor. A changed sensitive field invalidates the preview. Separate consent to share data from permission to mutate a provider resource. Include all new data in export/deletion and define retention for minimal consent evidence.

### Durable setup session

Add a `SetupSession` above existing provider `ConnectionSession` objects: tenant, creating user, optional assistant binding, goal/launch-plan reference, return destination identifier, version, status, expiry and provider-step references. It orchestrates UX state, not a second background engine.

The assistant may receive a short-lived owner setup URL. Opening the URL does not grant action authority. Require sign-in, current membership and explicit org match; store opaque link secrets hashed, expire/redeem them atomically, and prevent tokens/referrers/logs from leaking them. Use fixed/allowlisted return destinations. An expired link can be replaced after authentication without losing the durable underlying plan.

Suggested provider-card states:

| State | Meaning | Source |
|---|---|---|
| `not_needed` | Current workflow does not require it | Workflow dependency definition |
| `not_connected` | Required, no usable link | Connection query |
| `action_required` | Owner must sign in/consent/select or supply missing data | Structured missing prerequisite |
| `under_review` | Provider reports pending review | Dated provider fact |
| `verified` | Required account/scopes/current health checked | Connection/provider evidence |
| `needs_reauth` | Grant revoked, expired or insufficient | Health/scopes check |
| `failed` | Attempt failed; retry/manual path explicit | Classified error |

`already_connected` should be display text for an existing verified connection, not a second incompatible success state. A “verified” connection does not imply verified checkout, permitted spending or a live shop.

Proposed service surface:

```python
start_setup(principal, intent, idempotency_key, launch_plan_id=None) -> SetupView
get_setup(principal, setup_id) -> SetupView
get_pending_owner_actions(principal, setup_id) -> list[OwnerAction]
request_setup_connection(principal, setup_id, provider, capabilities, expected_version) -> ConnectionHandoff
resume_setup(principal, setup_id, expected_version) -> SetupView
```

Derive tenant and actor from `Principal`. Provider capabilities are allowlisted server-side and mapped to minimal scopes. If consent must be browser-only, a tool returns an owner handoff rather than invoking the browser service with invented authority.

### Shared response envelope

Keep current error conventions and version checks. New web/API/MCP views should share fields such as:

```json
{
  "id": "opaque-resource-id",
  "version": 3,
  "status": "needs_owner_action",
  "environment": "sandbox",
  "next_actions": [{"code": "connect_store", "label": "Connect your store", "owner_url": "/setup/example"}],
  "evidence": [],
  "blockers": [{"code": "STORE_CONNECTION_REQUIRED", "message": "Connect and confirm the store account."}]
}
```

This is an illustrative shape, not an existing payload contract. Explicitly distinguish permission/configuration errors from transient provider failures and uncertain external outcomes. No secrets in responses. Bound list sizes and return IDs/pagination, not full transcripts.

## Launch readiness

Implement one read-only projector used by `launch.py`, `commerce/service.py`, REST and MCP. It combines current connection health/scopes, current approved plan version, required stage evidence, exact receipt linkage and provider facts. Keep orchestration mutations separate from projection.

Readiness must distinguish:

- Internal draft exists.
- Store identity/ownership connection verified.
- Required external catalog drafts verified.
- Payment-provider eligibility/status known; KYC may still be pending.
- Tax/shipping setup has appropriate evidence/review.
- Permitted test checkout succeeded in the stated environment.
- Exact publication manifest has fresh owner approval.
- Publication receipts verified.
- Ongoing operational readiness and fulfillment evidence.

A test checkout does not prove production payments. A published product does not prove all store launch prerequisites. A fulfillment record does not prove dispatch or delivery. Every readiness fact includes source, observed time, expiry/recheck rule, environment and linkage to current plan/connection/resource/version. Reject wrong-tenant, wrong-store, stale, replayed, revoked or mismatched receipts.

Use monotonic evidence history but reversible readiness: a previously ready store can become `needs_reauth` or `blocked`. Do not erase earlier receipts when access changes. Avoid holding a database transaction over slow remote readiness reads; fetch/reconcile through bounded operations and then persist verified facts with version checks.

## Approval and concurrent execution boundaries

- Approval fingerprints include action, target account/resource, payload/manifest, currency/amount, limits, policy/plan version and expiry.
- Review existing gateway behavior before adding new locks. Never weaken action exclusion, write-ahead receipts or lookup-before-retry.
- Recheck policy, membership, current grants, emergency stop and budgets at dispatch time. Test revocation/pause between preparation and provider dispatch.
- If a provider side effect succeeds but evidence attachment fails, preserve the receipt and reconcile the projection. Do not replay the side effect merely to repair the UI.
- A batch may partially succeed. Report each item, keep verified receipts, and request new authority only for changed/unapproved items. Never roll back by inventing an irreversible-action undo.

## Compatibility and migration

Prefer additive endpoints/tools. Map the master’s conceptual names to existing tools before adding aliases. Keep authorization in services so all surfaces enforce the same rules. Use schema fixtures and contract tests for new response shapes. Additive migrations must define RLS, indexes, uniqueness, foreign keys, export/deletion handling and retention. Rehearse upgrade on a populated copy; do not edit released migrations to make new tests pass.
