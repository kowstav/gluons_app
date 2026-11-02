# Architecture decisions

## ADR-022 Cloud SQL-compatible worker authority (supersedes ADR-003 role attribute)
Cloud SQL does not expose PostgreSQL superusers, and PostgreSQL requires a superuser
to grant BYPASSRLS. Fresh migration 0002 creates the system role without this
attribute; migration 0007 grants explicit system_access RLS policies to that separate
role and removes legacy local BYPASSRLS. The app role remains forced through tenant
RLS. Append-only grants/triggers are unchanged. Hosted Cloud SQL execution remains
untested until an authorized staging deployment.

## ADR-023 Cross-process action exclusion
An action-scoped PostgreSQL session advisory lock covers authorization, provider
execution and readback, including the gap between write-ahead receipt transactions.
It uses an independent NullPool connection so holders cannot exhaust the app pool.
Contenders return retry. Process death releases the lock; durable receipts still
require provider lookup before recovery. Gmail missing search results never prove
that an ambiguous send failed, so they require human reconciliation.

Each entry records a decision and, where it departs from the blueprint, why. Provider facts were checked on 2026-11-02 and are detailed in `docs/research/`.

## ADR-001 Modular monolith plus workers
The API, MCP server and connectors share one Python package (`gluons`), with separately deployed worker and webhook services. Follows doc 02 ("begin as modular monolith"). Next.js serves the web app.

## ADR-002 Postgres is the durable execution authority
**Blueprint:** Cloud Workflows and Cloud Tasks for orchestration.
**Decision:** a Postgres job table with leases and `FOR UPDATE SKIP LOCKED` is the single execution authority. Cloud Tasks only wakes workers. Doc 10 requires "one execution authority per task", and business state must live in Gluons regardless of the orchestrator.
**Why:** runs locally and in tests with no cloud dependency, keeps state changes and enqueues in one transaction, and survives lost wake-ups. Cloud Workflows can be added behind `BackendAdapter` if proven necessary.

## ADR-003 Isolation enforced by the database
`gluons_app` runs under `FORCE ROW LEVEL SECURITY`. `gluons_system` has no `BYPASSRLS` attribute: Cloud SQL customers cannot grant it, so migration `0007_system_policies` gives this one role explicit `system_access` policies instead. It is limited to queue claims and webhook routing. Both roles are created by migration SQL, never through the Cloud SQL Admin API, which would add them to `cloudsqlsuperuser`. History tables are append-only by trigger and by missing grants. Tests connect as the restricted role.

## ADR-004 Opaque OAuth tokens for assistants
Gluons is both authorization server and MCP resource server, so tokens are opaque and hashed at rest, giving immediate revocation by database lookup. Refresh tokens rotate with reuse detection.

## ADR-005 Two independent locks on shadow mode
`gateway` never reaches a provider write in shadow or replay, and `net.GuardedClient` separately refuses any state-changing HTTP request without a write permit. A connector bug fails loudly rather than touching a live system.

## ADR-006 Write-ahead receipts for exactly-once effects
A pending receipt commits before the provider call. A retry finds it and calls `lookup` instead of writing again. Only a matching independent readback sets `verified`.

## ADR-007 Skill contracts are a separate authority layer
Blueprint `workflow.yaml` and `SKILL.md` are treated as untrusted specifications. `packages/skills/<slug>/contract.yaml` is the executable graph, and the registry rejects any contract that leaves a blueprint step, failure branch or metric uncovered. Domain logic is Python, not a rule DSL.

## ADR-008 Capability status is derived from evidence
A skill's status comes from `skill_evidence` rows, not from a label in a file. `implemented` means it runs in shadow and passes its evals. `sandbox_verified` and `live_verified` need real provider receipts.

## ADR-009 Sandbox ERP implements a Frappe REST subset
Real ERPNext needs Docker and several GB. `apps/sandbox_erp` is a separate process with its own datastore, and one connector talks to both. Doc 12 explicitly allows a labelled simulator. Receipts carry `environment=simulator`. The connector is **not** verified against real ERPNext until that test is run.

## ADR-010 Embedded Postgres for local development and tests
`pixeltable-pgserver` provides real PostgreSQL with pgvector and no Docker. `pgserver` itself has no Python 3.13 wheel. `docker-compose.yml` remains the equivalent path.

## ADR-011 MCP uses SDK 2.x and serves both protocol eras
**Blueprint:** `FastMCP`, session-based Streamable HTTP, DCR, core Tasks.
**Current:** `mcp` 2.3 renamed it `MCPServer`. Revision 2026-07-28 removed `Mcp-Session-Id` and `initialize`, deprecated DCR in favour of CIMD, and moved Tasks to an extension the Python SDK does not implement.
**Decision:** one endpoint serves both eras because Claude and ChatGPT docs still describe 2025-era auth. The authorization server supports CIMD and DCR, returns `iss`, and advertises `none` plus `S256`. Long-running work uses polling tools (`get_task_status`) rather than the Tasks extension.

## ADR-012 Telephony is provider-neutral; India cannot use Twilio numbers
**Blueprint:** doc 13 options A and B assume a Twilio inbound number.
**Current:** Twilio states it sells no Local or Mobile India numbers. India toll-free takes domestic callers only, and at least Jio will not forward to international numbers. Exotel has been a first-class ElevenLabs provider since 2026-06-01.
**Decision:** `PhoneRouteProvider` is an interface with Twilio (US, UK) and Exotel or SIP (India) implementations. Route setup selects by country and refuses unsupported combinations rather than promising them. A forwarding test proves routing only through an observed call from a known test caller in a time window; a missing `ForwardedFrom` proves nothing.

## ADR-013 WhatsApp builds on Embedded Signup v5
**Blueprint:** "unified onboarding" (v4), WABA ids, no popups.
**Current:** v5 shipped 2026-10-20 and uses `messaging_account_ids`. The exchange code lives 30 seconds. The flow requires a popup window, so the mobile page opens it from a direct tap and offers desktop fallback. Graph API default is `v26.0`.
**Decision:** model Messaging account id, phone number id and a nullable WAAC id. Key customers on BSUID as well as phone. Track "client payment method added" as an onboarding state, because service messages are charged since 2026-10-01 and stop without one. Dedupe on message id for at least 7 days, since Meta signatures carry no timestamp.
**Risk to surface:** WhatsApp terms section 4.7 bars providers whose AI is the primary functionality, at Meta's discretion. Needs legal review before launch; recorded in `EXTERNAL_DEPENDENCIES.md`.

## ADR-014 ElevenLabs tool and initiation webhooks use static secrets
**Current:** only post-call webhooks are signed. Tool calls and the initiation webhook authenticate with static headers. Post-call retries are off by default.
**Decision:** per-tenant secrets, compared in constant time, plus reconciliation against the conversations API so a lost post-call webhook is recovered. The initiation webhook is on the live call path, so it stays fast and fails open to a safe default greeting rather than dropping the call.

## ADR-015 Shopify: one reviewed public app, dev store for the demo
**Blueprint:** doc 14 Route A assumes one app serving many merchants, and Route B configures a transfer store through Admin APIs.
**Current:** API Terms 2.3.20 limit custom apps to one merchant. Public distribution needs app review, Shopify Billing for app charges, and no manual store-domain field. Transfer stores block custom and draft apps. Fulfillment scopes need Shopify approval.
**Decision:** code targets the authorization code grant with expiring offline tokens and refresh. Until the app is reviewed, Gluons connects one pilot merchant or dev stores only, and the UI says so. The store-domain field exists only in non-App-Store pilot mode. The YC demo uses a dev store, labelled as one. Route B stays a manual, Dev Dashboard handoff.

## ADR-016 Stripe is not a Shopify checkout route
Shopify states Stripe is not a separate third-party gateway and cannot be activated where Shopify Payments is supported. Gluons models Stripe only for Gluons' own billing and for standalone Connect merchants, and returns Gluons setup URLs, since Account Links must not leave the platform.

## ADR-017 Gluons billing starts ledger-first
**Blueprint:** subscription plus metered billing through a payment provider.
**Current:** Stripe marks the Billing Meters guide "Not Recommended" for new integrations in favour of Metronome, which lacks Connect support.
**Decision:** the append-only `usage_events` ledger is authoritative. Export to a billing provider stays behind an interface and is a business decision, recorded in `EXTERNAL_DEPENDENCIES.md`.

## ADR-018 Activepieces is a sidecar option, not an embedded library
**Blueprint:** selectively embed pieces.
**Current:** pieces are TypeScript packages needing Node. Direct connectors in Python are simpler, so `ActivepiecesAdapter` is an optional webhook backend. n8n and Windmill stay excluded, as the blueprint says.

## ADR-019 Heavy optional dependencies are isolated
Docling pulls torch (about 3.4 GB compressed on Linux), and Browser Use pins conflict with current SDKs. MarkItDown is the default document extractor. Docling runs as an optional extractor behind the same interface, and Browser Use is not installed.

## ADR-020 Model routing requires evaluated quality first
Routing filters by capability, data-provider allowance and passing `model_eval_results` for the skill, then chooses on price and latency. A model with no passing evaluation cannot be selected for a skill that requires one. Prices are effective-dated. Provider notes: Anthropic `anthropic-version: 2026-10-21` rejects `temperature`, `top_p` and `top_k`. Gemini goes through `genai.Client(enterprise=True)`.

## ADR-021 Gmail watch needs a restricted scope
`gmail.send` is sensitive, but `users.watch` and `history.list` need a restricted scope, so public launch requires Google verification and an annual CASA assessment. Test users work until then, and refresh tokens expire after 7 days while the OAuth app is in Testing status. The UI surfaces both limits.

## ADR-024 Owner account consent sits outside the action gateway
The gateway governs business actions taken for a tenant. Provider calls that only link or maintain the owner's own account are different: the Google and Meta token exchanges, Embedded Signup webhook subscription and number registration, and the Gmail watch. They run under a named `write_permit`, reachable only from an owner's browser session or a signed intake grant, and no skill, assistant or model can reach them. Shadow never creates a watch: it polls read-only.

## ADR-025 One implementation for each capability
Two lines of work built the same features. Kept: the stricter assist session (a representative's only write is a consented shadow trial), with per-representative trial scoping added; `chat.py` with sealed confirmations over the fixed-command assistant; the paged data export with an explicit column allowlist over the single-document export; `whatsapp/service.py` with Embedded Signup moved into it; `ci.yml` over `verify.yml`; and `scripts/dev.py` over the shell runners. A channel-specific intake also replaced the generic webhook inbox module, because each provider's proof and routing differ.

## ADR-026 Attempt limits live in the database as well as at the edge
Cloud Armor limits are configured but do not exist locally or before an edge is deployed. Password and second-factor failures share one per-account count, cleared for two-step accounts only by a correct code, so signing in again between guesses gains nothing. Pending consent is capped per client, and abandoned registrations are purged hourly. Per-IP limits remain the edge's job.
