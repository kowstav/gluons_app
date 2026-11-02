# UPDATED 2026-11-02 — GITHUB-FIRST REUSE PLAN

**Read `docs/10_github_first_build_strategy.md` and `docs/11_github_repository_inventory.md` FIRST.** This document describes the original greenfield architecture; implementation now prioritizes approved, license-audited code components and vendor SDKs. Do not start by implementing document parsing, route optimization, generic MCP infrastructure or connector libraries from scratch. The **31-skill product scope remains unchanged**, but each skill is assembled from audited components plus Gluons-specific policies and real integrations. The original security, tenancy, authorization, and verification gates below remain mandatory.

# 03 — End-to-end engineering plan: full platform and complete skill library

This is the full system plan, not a 'restaurant MVP' scope. It deliberately separates **product completeness** from **production readiness**: all 31 skills are designed, but external access, testing, compliance and permissions determine when each can run live. Projects with proprietary vendor systems require business agreements; do not mock them as production.

## Mandatory release-blocking authorization gate — do this BEFORE a live integration

**Read `13_permissions_connections_and_mobile_onboarding.md` in full.** Workstream B / E / F previously stated OAuth, RBAC and approvals without adequately specifying the actual owner-facing browser / mobile provider authorization and carrier call routing. This is now a **P0 dependency** for all provider-connected workflows. Required four layers: assistant-to-Gluons authorization; Gluons-to-provider external permission; owner-to-automation action approval; and independently verified inbound telephone routing. Do not allow temporary demo workflow engines to bypass it.

**Current compatibility restriction:** ChatGPT custom MCP connections are web-only as documented in November 2026; the Gluons responsive web/PWA is the mobile fallback. Test ChatGPT write-action entitlements before committing to any in-chat demo.

**Acceptance:** real Google consent in mobile browser for authorized test user; server-side OAuth callback and verified external account; provider token inaccessible to assistant; action send blocked until separate owner policy; verified real inbound call via authorized Twilio number; false/reused callback denied; revocation fences subsequent work. See AUTH-01 through POL-03 in doc 13.

## Workstreams (build in parallel with dependency gates)

### A. Project, repo and foundations
1. Create a GitHub mono-repo with `apps/web`, `apps/api`, `apps/mcp`, `workers/`, `packages/contracts`, `packages/policies`, `packages/connectors`, `packages/skills`, `infra/terraform`, `tests/`, `fixtures/`.
2. TypeScript for web/SDK/MCP; Python FastAPI + Pydantic for model/skill/evaluation services or standardize on one backend language based on team strength. PostgreSQL migrations through Alembic/Prisma. Generate API clients from OpenAPI rather than duplicating schemas.
3. Enforce branch protections, code review, dependency pinning, typed schemas, secret scanning, unit tests, integration tests, and container image scanning.
4. Create GCP Organization and separate projects: `gluons-dev`, `gluons-staging`, `gluons-prod`; budget alerts and no publicly readable Storage buckets. Bind each service a unique minimum-permission identity.
5. Provision Cloud Run, Cloud SQL Postgres, Cloud Tasks, Pub/Sub, Cloud Storage, Secret Manager, Cloud KMS, Cloud Logging/Monitoring and Artifact Registry with Terraform; version infra changes through code. Set deployment region to match first customer and data residency, not a marketing claim about global coverage.
6. Set production outbound provider API tokens only in Secret Manager; separate staging/sandbox credentials; never put keys in skills, scripts, agent memory or videos.
**Acceptance:** CI tests and image scans pass; deployments are reproducible into staging; forbidden credentials never appear in logs or front-end bundles; budget alarms trigger in test.

### B. Organization, tenant, permissions and billing
1. Create organization, member, team and connector ownership models. Roles: owner, admin, operator, approver, auditor, field-rep-assist, support-read-only.
2. Implement server-side mandatory tenant validation and RLS/row policies. On each external action require approved tenant ownership and exact intended connector identity.
3. Implement auth/session, OIDC and MCP resource OAuth; configure CIMD/PKCE where supported. Store encrypted third-party grants with expiries, scope list and revoke functionality. Enforce exact provider access at action time, not plan time.
4. Implement action-level permissions (`read`, `draft`, `send`, `buy`, `refund`, `publish`, `call`, `schedule`, `cancel`, `write_accounting`, `view_sensitive`). Separate permission from model inference capability.
5. Implement tenant balance/ledger for costs with provider charge, units, price/version, workflow ID and reconciled invoice. Use a payment provider with subscription + metered billing and authoritative webhooks; append-only usage events prevent double charging.
**Acceptance:** cross-tenant reads and writes fail; operator cannot approve own high-risk spend; revocation halts new actions; budget breach stops usage; billing totals reconcile to provider logs.

### C. Model routing and expert skills
1. Implement model registry with capability labels, supported data regions, context window, cost version, observed accuracy and latency by skill; no single fixed universal 'best model'.
2. Implement adapters for Vertex Gemini, OpenAI API, Anthropic API, open-source vLLM or Ollama; all response contracts normalized; support structured output validation and tool response schema validation.
3. Implement cost-aware router: route based on **minimum evaluated quality** for task/risk class, then price and latency. Route uncertain cases to more capable model or human. For financial arithmetic use deterministic functions and external authoritative data, never free-form LLM calculations.
4. Import the 31 `workflow.yaml` and `SKILL.md` files as **untrusted executable specifications** until verified. Registry rejects malformed schema, missing policy, missing tests and unsupported connectors.
5. Domain expert review each skill. Create at least 10 task-specific reference cases plus six universal failure fixtures per skill; adversarial prompt injection tests in vendor documents; evaluate extraction F1, action accuracy, unsupported claims, error recovery and approval correctness.
6. Release a skill as `production_verified` only with supported provider/version, jurisdiction, signed evaluation, designated domain reviewer, rollback and observed reliability. Dynamic user requests can modify customer policy only within platform hard constraints.
**Acceptance:** injection cannot elevate privileges; every model change runs skill regression eval; every production skill has owner and rollback path; unsupported workflow clearly appears as 'requires integration'.

### D. Durable workflow orchestration
1. Implement immutable event log and current-state projection in PostgreSQL. Distinguish recurring automation from individual event-triggered runs.
2. Implement outbox + Pub/Sub for fanout; Cloud Tasks for per-action retries, per-provider rate limits and scheduled callbacks; Cloud Workflows for long-running standard state chains. Cloud Run handles short API calls; no entire business process waits inside an HTTP handler.
3. Implement pending approvals as durable records with expiry and verified tenant approver; resumption happens on approval event, not chat continuity.
4. Add action idempotency and read-after-write verification; if timeout after external write, query external record before retry. Develop compensation rule for reversible actions (cancel draft), but label emails, phone calls and bank transfers as potentially irreversible.
5. Add pause/cancel race controls, fault injection (5xx, out-of-order events, duplicate webhook, partial transaction), dead-letter queues and operator intervention queue.
6. Add reconciliation sweeper: compare task state with external system for stuck or uncertain actions; present mismatches.
**Acceptance:** 10x duplicate events create one external write; cancel blocks future queued writes; retries do not send duplicate customer SMS; crash recovery resumes exact checkpoint; approval denial has no external side effects.

### E. Connector platform and business data
1. Define normalization contracts: Customer, Contact, Product/SKU, Order, Invoice, Appointment, Property, MaintenanceTicket, Shipment, Lead, PurchaseOrder, EmployeeSchedule, Document. All have source IDs, last-synced timestamps and tenant ownership.
2. Build provider adapters in this sequence of **shared dependencies** (not industry-specific product rollout): Google OAuth/Gmail/Calendar, Twilio/ElevenLabs, Shopify, Stripe, HubSpot, QuickBooks/Xero, WhatsApp Business, a sandboxed ERP, an accessible POS, carrier tracking, document OCR and maps/routing.
3. For each adapter implement OAuth, event ingest/webhook signature, backfill, normalized read/write, sandbox testing, rate limiting, retries, error mapping, read-after-write verification and revocation.
4. Integrate only authorized vendor access; some restaurant POS, insurance carrier, title/MLS, EHR/PMS and banking services need partner approval, customer agreements or dedicated compliance work. Track per-skill live readiness rather than implying generalized coverage.
5. UI asks the owner for *capability-specific* scopes just in time, explaining why each is needed and whether it can be removed later.
**Acceptance:** contract test suite against vendor sandboxes; permission-revoked error handled correctly; no secret leaks; webhook replay rejected; provider outage results in human-readable pending action.

### F. Calls, email, messaging and specialized I/O
1. ElevenLabs: configure voice agent per skill family; connect Twilio or SIP, appropriate voice, languages and transfer path; tool calls return structured business records from Gluons policy gateway.
2. Capture customer AI disclosure and recording consent where required. No card-number capture in transcripts; payment links must use compliant vendor flow. Run noise, interruptions, foreign names, duplicate-item and unsafe-request evals.
3. Verify all ElevenLabs HMAC webhook signatures and timestamps, dedupe call event IDs, redact sensitive transcripts under owner retention policy, sync call outcome to task event stream. Don't place conversational state only inside ElevenLabs.
4. For email, use Gmail/Graph with least-permission scopes and user verification. **Important:** Gmail read scopes may be restricted and subject to Google review and security assessment. For the YC demo use a test workspace account or a non-restricted sending-only flow and make approvals transparent; do not assume unrestricted public access.
5. For WhatsApp implement approved business account setup, template-message rules, customer opt-in and permitted service windows; no cold bulk sends. Country-specific spam/call restrictions reviewed with counsel.
**Acceptance:** real phone test; agent hears owner-approved store data; creates correct sandbox draft; human transfer works with selected integration; invalid webhooks rejected; forced outage escalates cleanly.

### G. End-user product and on-site onboarding
1. Implement screens in `01_product_spec.md` as responsive web app: home, chat/native fallback, explore, connection manager, automation designer, trial studio, task monitor, approvals, cost explorer, audit, team/trust, field-rep assist.
2. Keep interface business-first, not workflow-engine-first. Label *real running* vs *simulation* vs *integration required* everywhere.
3. Add intent-to-plan preview, visual execution graph, before/after diff, receipts and plain-English cost explanation. Show explicit 'this message will be sent' confirmations for side effects.
4. Implement field representative's restricted setup role with temporary assistance session, owner consent, audit and completion report. Representatives never take custody of customer API passwords.
5. Add multilingual interface and plain-language educational explanations; localized customer consent and legal agreements.
**Acceptance:** owners can independently inspect and stop any task; a novice can discover a relevant workflow without knowing 'MCP'; a rep can activate a shadow trial with owner consent, without engineering access.

### H. Data, analytics, ROI and billing economics
1. Instrument every workflow: triggered, model calls, tokens/voice minutes, API actions, human reviews, errors, completion, gross revenue (where evidence exists), cost.
2. Measure baseline manually before pilot; do not infer counterfactual ROI solely from a recommendation. Classify observed savings, estimate, and unverified opportunity distinctly.
3. Cost accounting distinguishes Gluons revenue, external API expense, support/implementation labor, cloud infra, carrier costs, and separate customer paid inputs (ad spend, materials, creator compensation).
4. Provide monthly cost budgets and emergency kill switch. Build anomaly alerts for bill spikes, token loops, outbound calling spikes, failed events and expensive models.
**Acceptance:** task-level costs reconcile to invoices; reports include denominators/confidence; no savings claim without explicit basis.

### I. Reliability, red-team and regulatory readiness
1. Threat model: injection from incoming email/web page, malicious tool output, prompt exfiltration, unauthorized transfers, cross-tenant leakage, forged webhooks, replay, insider support access, billing fraud, malicious owner policies, unapproved agent tool use.
2. Add risk tiers with mandatory policy enforcement; high-risk workflows default to replay/shadow/supervised. Treat legal, tax, health and insurance skills as **assisted administrative tooling** requiring the appropriate licensed responsible party.
3. Run static analysis, dependency scans, secrets scans, penetration testing, tenant isolation tests, emergency shutdown drills and offboarding tests. Audit how field reps handle consent and PII.
4. Implement incident management, retention/deletion workflows, verified data export and region-specific regulatory checklist before enabling a jurisdiction.
**Acceptance:** all blocking high-severity security issues fixed; zero cross-tenant data exposure in test; no autonomous regulated certification; no silent background communications.

### J. Commercialization and YC proof
1. Recruit genuinely willing businesses and get explicit consent for sample data and any recordings. No manufactured usage or fabricated testimonials.
2. Run three parallel practical proofs: wholesaler order intake, service dispatch recommendations, and ElevenLabs voice reservation/order intake. At least one must execute a real sandbox or authorized production API write and verify its result.
3. Prove cross-host continuity: one assistant starts a task; owner checks status in Gluons; another permitted assistant resumes or requests approval.
4. Track completed business tasks, independent active customers, exact direct cost per completed task, correction rate, owner trust/retention, and assistant-independent task persistence.
5. Record founder-only video separately from product demo; complete YC application using concrete known traction only.
**Acceptance:** demo contains no simulated action represented as real; live external operation has a receipt and clear permissions; measurable observed customer behavior supports claims.

## Team and ownership
- Founding systems engineer: orchestration, tenancy and connectors.
- Founding AI/agent engineer: MCP, model routing, evaluations, prompt-injection boundaries.
- Full-stack product engineer: web UX, task monitoring, approvals, audit and field experience.
- Platform/security engineer: GCP, OAuth, incident response, observability.
- Vertical workflow experts (contract or advisory by domain): validate legal/tax/health/finance/restaurant/logistics specifics and create grounded evaluations.
- Customer success and field-onboarding lead: repeatable owner training and trial measurements.

This is a broad final-platform organization chart, not a claim that every role must be hired before a YC application. Founders should own the hardest engineering work and demonstrate it themselves.

## Dependency graph
```
GCP tenancy/auth/ledger
    ├── skill registry + expert review + eval platform
    ├── connector framework + provider sandboxes
    ├── durable task runtime + approval policy engine
    │     ├── multi-provider model router
    │     ├── ElevenLabs phone/email/messaging gateways
    │     └── per-industry workflow adapters
    ├── MCP server + host compatibility + native Gluons fallback
    └── app UX (explore → connect → trial → run → control → billing)
                       └── live verified pilot → YC demo and traction
```

## Work breakdown / representative implementation estimates
These are **planning ranges**, not promises or elapsed-time commitments. Actual effort depends on integrations and specialist/regulatory approvals.
- Shared secure foundation and auth/control plane: ~4–8 engineering person-weeks.
- Durable execution, eventing, approvals, idempotency, cost ledger: ~8–16 person-weeks.
- MCP and assistant compatibility plus first-party UI: ~6–12 person-weeks.
- Voice, messaging, model routing and evaluations: ~8–16 person-weeks.
- Each deeply verified external connector: from several engineer-days for straightforward sandboxes to many weeks or partner-dependent times for restricted systems.
- Each production-verified vertical skill: ongoing domain review, sandbox testing, adverse-case tests, field pilot and maintenance—not simply writing a system prompt.
- Production security and compliance reviews: independent approval milestones, potentially months depending on scope and jurisdictions.

These streams can run concurrently, but product reliability cannot be compressed by pretending inaccessible third-party systems are integrated. For YC, a genuine, narrow live demonstration is more credible than a broad catalog claiming unverified live availability.

## Functional acceptance matrix
| Capability | Test | Pass condition |
|---|---|---|
| Owner discover | owner asks in chat for tasks | suggestions grounded in actual supported integrations |
| Plan | owner chooses skill | typed graph, exact permissions, cost range and approval gates |
| Live connection | authorize in external system | scoped grant verified, expiry/revocation working |
| Shadow | 100 synthetic inbound events | zero external writes, complete comparison records |
| Approval | mutate price or send message | denied until correct owner signs, audit receipt |
| Durable | restart worker during waiting call | resumes without duplicate side effects |
| Cancel | cancel with queued work | queued writes stopped, in-flight status honest |
| Model swap | select cheaper provider | test suite passes minimum quality or swap rejected |
| Voice | one inbound call to real number | transcript/event verified and correct system action |
| Cross host | Claude starts, Gluons dashboard checks | same stable task ID and status |
| Privacy | local model tenant | trace confirms which data stayed local vs transmitted |
| Billing | replay 10,000 usage events | cost totals deduplicated and reconciled |
| Industry skill | edge-case scenario | invariants hold and human escalation when necessary |

### K. Owner-owned Gluons Web and end-to-end commerce business launch (added 2026-11-02)

**Mandatory reading:** `docs/14_business_launch_owner_portal_and_identity.md` and `playbooks/commerce_business_launch/`. This is a cross-industry orchestration playbook in addition to the 31 original workflow skill designs.

1. Implement authenticated owner/organization/member/agent-binding model. One owner can use multiple AI assistants and own multiple organizations; never map by chat ID or unverified email. Implement audited bind/revoke per assistant.
2. Make mobile-first Gluons Web the authoritative sign-in, consent, external-provider linking, billing, approvals and cancellation interface. Provide short-lived setup-link continuation from agent; implement server-side session validation and state polling.
3. Implement Shopify merchant onboarding routes: owner signup and app OAuth/collaborator authorization, or properly authorized Partner client-transfer store, with owner acceptance. No claims of merchant-account creation through generic Admin API.
4. Implement staged theme/catalog automation, business domain, tax/shipping checks, product-source verification, supplier sourcing and approved RFQs. No first supplier outreach or procurement commitment without owner-approved scope.
5. Integrate payment-provider eligibility and status, with the merchant directly completing Shopify Payments or eligible processor KYC/terms. Do not treat Stripe Connect setup as automatically available as a Shopify gateway.
6. Integrate order webhooks, fraud/payment checks, authorized supplier/3PL fulfillment handoffs, read-after-write status checks, shipping tracking and manual exception queue. No fabricated delivery completion.
7. Demonstrate ChatGPT/Claude initiation → Gluons secure web signup/approval → Shopify test store → supervised supplier inquiry → test checkout → verified test order → owner pause and cross-client status.

**Acceptance:** first-user onboarding works on supported mobile browser; no cross-tenant linkage; no third-party token leaks; merchant remains owner; unapproved outward communication/spend blocked; actual provider receipts distinguish test from live; task continues after assistant closes. Engineering must satisfy P0 connection tests in docs/13 before live provider actions.
