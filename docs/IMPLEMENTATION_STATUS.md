# Implementation status — 2026-11-02

Gluons runs locally end to end and is not production ready. No real provider account has been connected, nothing is deployed, and the images and CI have never run. Everything below is proven against real PostgreSQL, the labelled ERP simulator or labelled provider doubles only.

`docs/BLUEPRINT_TRACEABILITY.md` holds the row-by-row position, and `docs/TEST_RESULTS.md` holds the runs.

## Working locally

- **Identity:** verified email, businesses, roles, two-step sign-in with attempt limits, browser sessions, CSRF, forced tenant row level security, an encrypted vault and an append-only audit chain.
- **Assistants:** OAuth with PKCE and resource binding, rotating refresh tokens, code replay revocation, independent revocation, and a remote MCP server with 47 tools.
- **Runtime:** durable runs, leases, retries, dead letters, approvals, budgets with concurrency proof, pause and cancel, write-ahead receipts and independent readback.
- **Skills:** all 31 contracts and the commerce playbook. The order skill runs end to end against the simulator. The other 30 produce review packets only, with deterministic domain constraints applied to every draft. See `docs/SKILL_CAPABILITIES.md`.
- **Channels:** Gmail history sync and attachment ingestion, WhatsApp Embedded Signup, signed webhooks and the send gate, ElevenLabs post-call and tool intake, and phone route state. All three feed one order engine.
- **Commerce:** Shopify OAuth, draft products, publication and fulfillment records with exact approval, supplier inquiries, and Shopify Payments status. Nothing here claims KYC, a live store or delivery.
- **Models:** four provider adapters, evaluated routing, token and cost accounting, and fallback. No model is certified, so skills that need one stay blocked until evaluation evidence exists.
- **Evidence:** labelled replay, shadow and supervised trials, measured versus estimated ROI, anomaly detection, and data export with deletion requests.
- **Owner app:** every area of the mission has a screen backed by the real API, including messages, orders, data rights, assisted setup and business launch.
- **Operations:** Terraform environments over modules, pinned Dockerfiles, CI and a manual deploy workflow, structured logging with redaction, an OIDC worker wake endpoint and runbooks.

## Blocked on outside action

Each needs something only the owner can provide. `docs/EXTERNAL_DEPENDENCIES.md` lists what to obtain.

| Area | Needs |
|---|---|
| Gmail live test | Google OAuth client, consent test users, Pub/Sub topic |
| WhatsApp live test | Approved Meta app, Embedded Signup configuration, test number |
| Voice live test | ElevenLabs account, a number or SIP route, one real inbound call |
| Shopify live test | App credentials and a development store |
| Model use | At least one provider key, then evaluation runs |
| Cloud deployment | Owner approval for chargeable resources |
| CI | A GitHub remote |
| Images | A running Docker daemon |

## Not built

- **Stripe status:** only Shopify Payments status is read. ADR-016 limits Stripe to Gluons' own billing and standalone merchants, so this waits for that decision.
- **Deletion purge:** a deletion request closes access and destroys credentials, but no job erases the marked records yet. The response says so.
- **Domain workflows for 30 skills:** each needs a provider action graph and real evidence before it can act.
- **Per-IP rate limits outside the edge:** these depend on Cloud Armor, which is configured but not deployed.
- **Trial measurement and supervised trial screens:** both work through REST and MCP, but the owner app has no form for them.

## Unverified assumptions

These were written from provider documentation and only exercised against doubles:

- ElevenLabs may not return a conversation while the call is still running. Voice order intake reads it back during the call, so a real call may need a change.
- Meta's popup behaviour with the app's `no-referrer` policy is unknown.
- `shopify.fulfillment.create` needs a verified `shipping.shipment.dispatch` receipt, and no connector produces one yet, so it fails closed.
- The ERP connector has only met the simulator, never a real ERPNext.
