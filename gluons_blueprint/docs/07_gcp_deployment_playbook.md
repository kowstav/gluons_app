> **MUST IMPLEMENT FIRST:** Follow `13_permissions_connections_and_mobile_onboarding.md` for callback URLs, state/PKCE, mobile fallback, webhook signatures, Gmail watch renewal, Meta Embedded Signup, Twilio number routing, policy authorizations, vault design and all P0 tests. A Cloud Run deployment with an MCP endpoint and secret store **is not yet a working account connection system**.

# 07 — Google Cloud installation and operations playbook

The commands below are a **production-system preparation guide**, not evidence the production app is implemented. Never publicly deploy the included demo reference API: it has no authentication and is strictly local.

## Prerequisites
GCP organization and billing with budget alarms; `gcloud` CLI installed and logged in; IAM rights for project/IAM/service accounts; Terraform pinned in repo; approved region matching customer data-residency requirements; bought/authorized domain; separate staging and production account/credentials; ElevenLabs API account and Twilio/SIP test phone line; real vendor sandbox accounts.

## 1. Separate projects and APIs
Replace variables with actual IDs and billing configuration. Configure billing project association and valid owner IAM outside these example commands.

```bash
export REGION=us-central1
export PROJECT_ID=gluons-staging-YOURUNIQUEID
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  cloudkms.googleapis.com cloudtasks.googleapis.com pubsub.googleapis.com \
  workflows.googleapis.com cloudscheduler.googleapis.com \
  identitytoolkit.googleapis.com logging.googleapis.com monitoring.googleapis.com \
  aiplatform.googleapis.com storage.googleapis.com --project "$PROJECT_ID"
```

Use organization policies to restrict public buckets, external keys and default service-account broad permissions. Enable audit logs for sensitive APIs. Implement managed infrastructure with Terraform using per-environment variables; don't copy staging secrets into production.

## 2. Required service identities
Create separate service accounts for `gluons-api`, `gluons-mcp`, `gluons-worker`, `gluons-webhooks`, `gluons-evaluator`, `gluons-billing`. Grant only needed permissions per service: specific Cloud SQL instance, Cloud Tasks queue, Pub/Sub topic, named Secret Manager secret, bucket prefix and KMS key. Avoid Editor/Owner service roles. Workload Identity Federation for CI/CD instead of downloadable service-account key JSON.

## 3. Database, events and audit
Provision private Cloud SQL PostgreSQL and backups, PITR, regional HA if budget/reliability requires it; use SQL connector / private connectivity. Apply schema migrations and RLS policies; write tenant isolation tests before creating any customer tenant. Create Pub/Sub topics `incoming-events`, `state-events`, `automation-alerts`, `dead-letters`, and Cloud Tasks queues `model-work`, `connector-actions`, `scheduled-followups`. Use stable deduplication keys and transactional outbox. Create private Cloud Storage buckets for document evidence with object retention policies and lifecycle rules; do not log customer source documents.

## 4. Remote MCP endpoints
Expose **only** public HTTPS MCP endpoints behind rate limiting and app-level OAuth; ChatGPT/Claude need network accessibility unless supported secure tunneling is used. IAM-only Cloud Run auth by itself generally won't give third-party consumer clients the MCP OAuth interaction they expect. Handle OAuth protected resource metadata, issuer, audience, host feature negotiation, scoped user tokens, short-lived redirect. Pin current MCP SDK and implement Streamable HTTP; don't assume all clients support Tasks extension or sampling.

## 5. ElevenLabs and Twilio/SIP
- Start in ElevenLabs developer test workspace; create an inbound order/reservation template with a genuine test number and separate customer agent config.
- Native Twilio integration is simpler and supports call transfers; `register-call` offers more bespoke Twilio control but does **not** provide built-in call transfer for that method.
- Tool webhooks go to `voice-action-gateway` with signed authentication and owner-specific policy checks; don't authorize a write solely because a voice model asked for it.
- On call completion, validate ElevenLabs webhook using vendor SDK HMAC verification and deduplicate `conversation_id` + event type. Preserve costs, evidence and restricted transcript only according to privacy policy.
- Test 20+ accented/multilingual calls, barge-in, repeated item, change after confirmation, silence, caller distress, handoff, downstream 503, partial POS write, unexpected disconnect and consent.
- Estimate ElevenAgents hosting using published per-minute rates, plus **separate** LLM/telephony/provider expenses. Use current billing page rather than static sample pricing in production.

## 6. Model service accounts and route testing
Create provider credentials in Secret Manager and select default Vertex Gemini route plus Anthropic/OpenAI provider routes. Store model versions and cost sheets with effective timestamps, model eval results, allowed data classifications and region. Never let prompt content include secrets. For private model option, route to tenant-owned model endpoint or own isolated GPU workload; disclose whether Gluons cloud still receives task data. Limit token loops, tool calls, wall time and concurrency per tenant.

## 7. API, UI and worker deploy sequence
1. Deploy migrations and schema to staging; assert RLS and cross-tenant tests.
2. Deploy policy engine, connector worker and queue consumer; run duplicate-event and restart tests.
3. Deploy public OAuth/MCP server and HTTPS web app. Confirm third-party host authorization with a staging tenant; verify writes are not exposed where host doesn't support them.
4. Configure DNS, TLS, WAF and runtime monitoring; register `status.gluons...` and incident notifications.
5. Deploy skill registry and evaluation runner; mark all unintegrated skills `SPEC_ONLY`.
6. Promote chosen provider integrations to `SANDBOX_INTEGRATED` only after read/write verified receipt and rollback path.
7. Activate a customer pilot **only** after agreements, scope and permissions; monitor correctness, token spend, telephony bill, human escalations and provider statuses.
8. Promote to production with canary deployment and tested rollback.

## 8. Production alerts and runbooks
- **Duplicate external transaction:** block action queue; reconcile source record by idempotency key; notify owner and incident lead.
- **Over-budget:** reject new metered actions; preserve incoming events until policy permits backlog handling; display owner notice.
- **Webhook gap:** detect missing recurring provider events; show stale data banner and trigger permitted catch-up.
- **Gmail/ERP OAuth expired:** move affected tasks to `waiting_external`, prompt re-auth in secure owner dashboard.
- **Suspected prompt injection:** block privileged action, preserve redacted payload hash, review without automatically retransmitting harmful content.
- **ElevenLabs provider outage:** fallback to human routing where configured, not silent lost calls.
- **Cross-tenant concern:** disable affected connector and isolate tenant data; trigger incident response and required notifications.
- **Workflow policy updated:** pin existing runs to their approved version; migration only after dry-run and owner approval for significant behavior changes.

## 9. Production launch checklist
- [ ] Authentication, session rotation, OAuth audience and scope validation.
- [ ] Tenant isolation, role checks, approval signature and Maker/Checker checks.
- [ ] Strong audit logging with data minimization and regional compliance.
- [ ] Idempotency across all event/webhook and external write surfaces.
- [ ] Correct pause/cancel semantics and credible disclosure of irreversibility.
- [ ] Source-of-truth external write readback; no fabricated success.
- [ ] Model evaluation with per-skill thresholds and fallback.
- [ ] Rate limits, costs, quotas, spend caps, notification paths.
- [ ] Provider availability, webhook verification, fallback and runbooks.
- [ ] Trial correctness, measured baseline, no synthetic-ROI deception.
- [ ] Legal/domain review for regulated workflows and outbound communications.
- [ ] Support/helpdesk and owner-facing offboarding/data export.

## Cost-estimation formula
`direct monthly COGS = inference + speech + telecom + messaging + third-party data + cloud runtime + storage + support labor + onboarding amortization + payments/other fulfillment`.

For a phone task, example baseline: ElevenAgents advertises $0.08/additional minute before separate LLM and telephony, so 1,000 **additional** minutes implies $80 agent hosting as a component, not total. Rates/plan entitlements change, and actual incurred costs depend on specific billing tier, silence discounts, LLM use, concurrent call burst and country telephony pricing. Source: https://elevenlabs.io/pricing/agents ; https://elevenlabs.io/docs/help-center/product/eleven-agents/how-much-does-eleven-agents-cost
