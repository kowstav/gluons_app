# Implementation status — 2026-11-07

> November 8: this historical checkpoint is superseded for GitHub/build evidence by the [current verification ledger](execution/06_VERIFICATION_AND_RELEASE.md). Private `kowstav/gluons_app` is published; functional CI checks passed and three images built, but dependency/image security scans failed. No live-provider or deployment proof was added.

Gluons runs locally end to end and is not production ready. No real provider account has been connected, nothing is deployed, and the images and CI have never run. Everything below is proven against real PostgreSQL, the labelled ERP simulator or labelled provider doubles only.

`docs/BLUEPRINT_TRACEABILITY.md` holds the row-by-row position, `docs/TEST_RESULTS.md` the runs, and `docs/ACCOUNT_CONNECTIONS.md` how owners sign in to each provider.

## Working locally

- **Identity:** verified email, businesses, roles, and two-step sign-in with shared attempt limits. Browser sessions, CSRF, forced tenant row level security, an encrypted vault and an append-only audit chain. Per-address limits on every endpoint anyone can call.
- **Assistants:** OAuth with PKCE and resource binding, rotating refresh tokens, code replay revocation, independent revocation, and a remote MCP server with 47 tools.
- **Account connections:**
  - Gmail through Google's consent screen.
  - Shopify installed from Shopify and bound to a business after sign-in, with uninstall and the three privacy webhooks. Typed shop domains work for operator-approved pilots only.
  - WhatsApp through Meta's Embedded Signup.
  - A health check every 6 hours renews access or flags it before it lapses.
- **Runtime:** durable runs, leases, retries, dead letters, approvals, budgets with concurrency proof, pause and cancel, write-ahead receipts and independent readback.
- **Skills:** all 31 contracts and the commerce playbook. The order skill runs end to end against the simulator. The other 30 produce review packets, with deterministic domain constraints on every draft. See `docs/SKILL_CAPABILITIES.md`.
- **Channels:** Gmail history sync and attachments, WhatsApp webhooks and the send gate, ElevenLabs post-call and tool intake, and phone route state. All three feed one order engine.
- **Commerce:** draft products, publication and fulfillment records with exact approval, supplier inquiries, and Shopify Payments status. Nothing claims KYC, a live store or delivery.
- **Models:** four provider adapters, evaluated routing, token and cost accounting, and fallback. No model is certified, so skills that need one stay blocked until evaluation evidence exists.
- **Evidence:** replay, shadow and supervised trials with recorded measurements, measured versus estimated ROI, anomaly detection, data export, and deletion with a purge after a grace period.
- **Owner app:** every mission area has a screen backed by the real API, including connections, messages, orders, data rights, trials, assisted setup and business launch.
- **Operations:** Terraform environments over modules with multi-platform provider locks, pinned Dockerfiles, CI and a manual deploy workflow, structured logging with redaction, an OIDC worker wake endpoint and runbooks.

## Needs you

`docs/EXTERNAL_DEPENDENCIES.md` says where to get each one.

| Area | Needs |
|---|---|
| Gmail live test | Google OAuth client, consent test users, and optionally a Pub/Sub topic |
| Shopify live test | App credentials, the app set up from `infra/shopify/shopify.app.toml.example`, a development store |
| WhatsApp live test | Meta app, Embedded Signup configuration, business verification, a test number |
| Voice live test | ElevenLabs account, a number or SIP route, one real inbound call |
| Model use | At least one provider key, then evaluation runs |
| Images and real ERPNext | Open Docker Desktop once. It did not start unattended here. |
| Cloud deployment | Your approval for chargeable resources |
| CI | A GitHub remote |
| App review | A privacy policy and a data deletion page, which are legal texts |
| Stripe | Whether Gluons becomes a Stripe Connect platform (ADR-016) |
| 30 draft-only skills | Which business systems each should write to, and real accounts for them |

## Unverified assumptions

These were written from provider documentation and only exercised against doubles:

- ElevenLabs may not return a conversation while the call is still running. Voice order intake reads it back during the call, so a real call may need a change.
- Meta's popup behaviour under the app's `no-referrer` policy is unknown.
- Whether `use_legacy_install_flow` is required for a non-embedded Shopify app (marked in the TOML template).
- The `X-Forwarded-For` layout behind Google's load balancer, which sets `GLUONS_TRUSTED_PROXY_HOPS=2` (deployment runbook step 10).
- `shopify.fulfillment.create` needs a verified `shipping.shipment.dispatch` receipt, and no connector produces one yet, so it fails closed.
- The ERP connector has only met the simulator, never a real ERPNext.
