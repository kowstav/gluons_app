# 14 — Business Launch Orchestration, Owned Web Portal, and Cross-Agent Identity

**Status:** Full product architecture amendment, not implemented. **Priority: P0 owner identity/permission portal, P1 business-launch orchestration**. Updated 2026-11-02.

## Product decision

A Gluons-owned browser application is the **authoritative identity, provider-connection, business-approval, and operational control surface**. ChatGPT, Claude, and private assistants are interchangeable conversational clients. A business owner may use several clients and belong to multiple organizations. **Do not pair one ChatGPT conversation with one immutable Gluons account.** Bind each authenticated client installation to an owner/member in a selected business organization, and enforce scopes and tenant authorization on *every* tool request.

The cloud task orchestration layer continues independently of the assistant's conversation. The web portal is responsible for provider sign-in, credentials, merchant verification handoffs, approval decisions, billing limits, cancel/pause/monitor and source receipts. The assistant may provide secure links, but external providers—not the assistant—authenticate merchants.

## Identity and tenancy model

- `user`: Gluons identity with verified authentication and optional MFA/passkey.
- `organization`: legally or operationally distinct business. One user can be owner of several organizations.
- `membership`: user-organization-role link; owner/admin/approver/operator/viewer and restricted setup representative.
- `agent_binding`: provider (ChatGPT/Claude/local), OAuth client identifier, Gluons user subject and permitted organization IDs; token audience/scopes/expiry tracked. One user can have multiple bindings; one binding may be revoked without removing other connections.
- `provider_connection`: Google/Shopify/Meta/Stripe/ERP connector credential and provider business account identifier, **scoped to one Gluons organization**.
- `grant`: owner-signed policy specifying resource, action, limits, expiration, autonomy level, and approver.
- `workflow_instance`: versioned playbook configuration and persistent execution state, independent of any agent.
- `approval`: server-side action proposal fingerprint, expected values, owner identity, expiration, cryptographic binding, decision, audit receipt.

Identity binding must be based on verified Gluons sign-in and standard protocol claims, never a chat ID, unverified email address, URL parameter, or the model's statement that someone is the owner.

## Recommended user journey (mobile and desktop)

1. Owner asks ChatGPT: "Start an e-commerce business." Agent reads `gluons.business_launch` skill and calls `business_launch.plan` with user-provided objectives, not secrets.
2. If no Gluons auth exists, the supported host initiates OAuth/PKCE; otherwise show a **short-lived setup link** returned by Gluons. On mobile this opens the standard browser or supported auth surface. Do not assume a specific in-chat browser pane.
3. Gluons Web authenticates the owner, shows the selected organization and proposed work, then asks for *business-operating approval* to create a draft project. A setup URL alone **never** grants authority.
4. Gluons creates a durable plan with checklist, availability/pricing estimates, eligible vendor integrations, owner-required actions, and time-sensitive approvals. Show progress in web and, where supported, through assistant polling.
5. Provider onboarding takes place on provider-owned OAuth, merchant-admin, or hosted compliance pages. Gluons resumes only after backend verification of the resulting account scope/status.
6. The owner may close ChatGPT; approved background operations continue in Google Cloud. Unapproved operations remain waiting. Re-enter from any connected client or browser.
7. In the Gluons portal owner can inspect all sessions/agents, revoke the ChatGPT connection, disconnect Shopify, adjust policy, pause, cancel, export or delete data, and inspect audit evidence.

**Compatibility:** ChatGPT plugin installation and mobile support vary by plan/workspace/surface; remote custom MCP availability and write rights cannot be assumed. The Gluons web app is guaranteed fallback; a Gluons-native chat surface can provide compatible control in that app. Optional "Sign in with ChatGPT" requires eligibility and is **not assumed available to Gluons**.

## Shopify store creation: supported routes

**Route A, merchant-owned:** Take owner to official Shopify signup. Owner creates account with own email/identity and accepts billing/terms. Once store exists, install/authorize Gluons' Shopify app or grant permitted collaborator access. Verify actual store ID and scopes. Shopify Admin API controls an **existing store**, not a universal independent merchant signup endpoint. Avoid taking merchant passwords or controlling their credentials.

**Route B, partner-built:** With a properly authorized Shopify Partner organization, create a **client transfer store**, build configuration, then transfer ownership to merchant via Shopify's official acceptance flow. Do not assert that client-transfer-store creation/transfer is available through a general public API unless that route is separately verified; the documented flow uses the Shopify partner Dev Dashboard. No production transactions before transfer; partner-transfer stores have other restrictions. Merchant accepts plan, ownership and billing.

After either route: using authorized Admin APIs and approved write scopes, stage theme/content/catalog, collections, prices, shipping/tax settings and inventory synchronization. Test changes in draft and require owner approval for publication and significant changes. Honor storefront licensing and image intellectual property.

Sources: https://help.shopify.com/en/manual/intro-to-shopify/initial-setup ; https://shopify.dev/docs/apps/build/stores/client-transfer-stores ; https://help.shopify.com/en/partners/manage-clients-stores/working-on-client-stores

## Payment onboarding is a gated human-owned action

- Determine owner **business country, entity type, product category, currency, processor availability**. Shopify Payments is available only in listed countries and requires business/identity/bank verification; the owner activates it inside Shopify admin/mobile app. The assistant can prepare a checklist and deep-link into official setup, not fabricate eligibility or complete KYC as the owner.
- Stripe hosted onboarding / Connect should be used only with a legitimate matching platform and merchant payment configuration. A Stripe Connect account is not automatically an eligible Shopify credit-card gateway. Third-party gateway support, required contracts, local restrictions and fees must be checked for the shop and country.
- Merchant holds its own payment accounts; don't use Gluons to custody sale proceeds by default. Never put bank account details, IDs, password, payment-card PAN or raw KYC in an LLM conversation or agent logs.
- Gate `payments.verified` on provider-confirmed account status/capability, not simply a clicked setup link; handle `under_review`, `rejected`, `restricted`, `requires_action`, and changes in eligibility.

Sources: https://help.shopify.com/en/manual/payments/shopify-payments/supported-countries ; https://help.shopify.com/en/manual/payments/shopify-payments/onboarding/account-setup ; https://help.shopify.com/en/manual/payments/third-party-providers ; https://stripe.com/connect/features

## Supplier discovery, outreach, purchasing and fulfillment

**Discovery:** Research credible suppliers via approved sources; store links and dated evidence. Estimate unit economics including minimum order quantities, landed cost, delivery times, samples, shipping, duties, taxes and returns. Flag unknown reliability rather than making unsupported claims.

**Outreach:** Draft RFQs tied to shortlisted suppliers and product specs. Before first contact or any external communication, show recipient, content, represented entity and sending account to authorized owner. After a scoped communication policy is accepted, routine followups may run autonomously with rate limits, business-hours rules and compliance. Do not mass-spam scraped contacts or evade provider/marketplace policies.

**Negotiation:** Extract offers into comparable quotes; use configured negotiation bounds, human review for commitments, exclusivity, payment, samples and material price changes. No agent may sign contracts or move funds merely because a supplier asks.

**Catalog and inventory:** Draft descriptions with factual attributes sourced from verified supplier information. Never claim certifications, stock, delivery date, warranty, or origin absent evidence. Require sign-off before publishing.

**Fulfillment:** Receive paid orders using Shopify events, verify payment status and fraud flags, map order lines to permitted supplier/warehouse, reserve inventory, submit approved purchase/fulfillment requests, track acknowledgments and shipment evidence, create fulfillment record **only when actual physical fulfillment status is verified**. The Shopify GraphQL `fulfillmentCreate` mutation requires fulfillment-order scopes and fulfill-and-ship permission. Record tracking, returns and disputes. A Shopify API "fulfilled" state is not proof of actual delivery.

Source: https://shopify.dev/docs/api/admin-graphql/latest/mutations/fulfillmentCreate

## Business launch workflow and state gates

```
INTENT_CAPTURED
  -> MARKET_RESEARCH_DRAFTED
  -> BUSINESS_PLAN_APPROVED
  -> STORE_ONBOARDING_REQUIRED
  -> STORE_CONNECTED
  -> STOREFRONT_STAGED
  -> SUPPLIERS_SHORTLISTED
  -> SUPPLIER_OUTREACH_APPROVED
  -> QUOTES_EVALUATED
  -> CATALOG_AND_OPERATIONS_READY
  -> PAYMENT_ONBOARDING_REQUIRED
  -> PAYMENT_VERIFIED
  -> TEST_CHECKOUT_VERIFIED
  -> GO_LIVE_APPROVAL_PENDING
  -> LIVE
  -> ORDER_FULFILLMENT_MONITORING
```

Each stage may branch to `WAITING_FOR_OWNER`, `WAITING_FOR_PROVIDER`, `NEEDS_REVIEW`, `FAILED_RECOVERABLE`, `PAUSED`, `CANCELLED`. Task completion depends on verified external records. A supplier agreement or KYC can stall indefinitely without blocking unrelated draft work. The task engine enforces task-local budgets, queue idempotency, timeouts, repeatable checkpoints and replay-safe resume.

## Approval tiers

| Tier | Examples | Default policy |
|---|---|---|
| 0: read-only | Research niches, fetch authorized catalogs, compare supplier offers | Automatic with read permission |
| 1: draft | Create product drafts, draft supplier emails, prepare shop theme preview | Automatic as draft with owner-visible log |
| 2: outward communications | First supplier contact, customer notification, promotional email | Explicit owner approval before first/novel class; bounded rules thereafter |
| 3: external commitments | Accept supplier quotes, order samples, publish store, change significant prices, fulfill paid orders | Scoped owner approval or explicit limited spending policy, plus provider verification |
| 4: identity, finance, legal | Merchant KYC, tax filings, banking, processor acceptance, contracts, major refunds | Owner/provider-controlled, never silently delegated to a model |

## APIs and UX surfaces

```
POST /v1/business-launch/plans
GET  /v1/business-launch/plans/{plan_id}
POST /v1/business-launch/plans/{plan_id}/start
GET  /v1/business-launch/plans/{plan_id}/timeline
POST /v1/connections/sessions
GET  /v1/connections/sessions/{session_id}
GET  /v1/organizations/{org_id}/connections
POST /v1/approvals/{approval_id}/decision
POST /v1/business-launch/plans/{plan_id}/pause
POST /v1/business-launch/plans/{plan_id}/resume
POST /v1/business-launch/plans/{plan_id}/cancel
GET  /v1/business-launch/plans/{plan_id}/evidence
```

All API resources are organization-scoped by authenticated subject and required action scope. MCP tools return plan IDs, actionable next steps, and **short-lived setup URLs**, not provider tokens. Browser success redirects to owner's workflow page; assistant can poll status or receive updates only where supported. Never put state-changing approvals in URL GET parameters.

Web routes: `/login`, `/org/select`, `/setup/{session}`, `/connections`, `/plans/{id}`, `/approvals`, `/suppliers`, `/payments/status`, `/store`, `/automation-control`, `/audit`, `/security/sessions`.

## Proposed permissions & connection data

```json
{
  "organization_id": "org_123",
  "client_binding_id": "binding_chatgpt_45",
  "connection_id": "conn_shopify_23",
  "provider": "shopify",
  "provider_account_id": "shop_456",
  "scopes": ["read_products", "write_products", "read_orders"],
  "status": "verified",
  "owner_grants": [
    {"capability": "catalog.draft", "mode": "automatic", "expires_at": "2026-12-31T00:00:00Z"},
    {"capability": "catalog.publish", "mode": "approval_required", "expires_at": "2026-12-31T00:00:00Z"}
  ]
}
```

This is a conceptual data model. Store provider credentials encrypted server-side and never expose them in the agent's response. Provider scope is a hard ceiling; owner action grant cannot elevate privileges beyond connected scope.

## Required Google Cloud services

Cloud Run for API, MCP and connection callback handlers; Cloud SQL Postgres for organizations/client bindings/approvals/workflow state; Secret Manager/KMS for credentials; Cloud Tasks and Pub/Sub for event delivery; Cloud Workflows or task engine for multi-week task execution; Cloud Storage for encrypted authorized evidence; Cloud Logging and Monitoring for observability. Have a durable outbox, replay-safe provider-event handler, retention rules and task-cost metering.

## YC demo (real integrations, honest labels)

1. Initiate the business launch from ChatGPT or Claude if the host supports the connected tool. Otherwise use Gluons' integrated chat and explain the limitation.
2. Open a short-lived Gluons Web setup link on a mobile phone; complete owner login and organization binding.
3. Show a **real Shopify dev or client transfer sandbox** (with restrictions clearly labeled); don't claim a store or checkout is merchant production-ready unless verified.
4. Show draft brand/store/catalog generated from an owner-approved objective; demonstrate supplier comparison and a **consensual inquiry to a test supplier email**.
5. Show a **test checkout** through a permitted payment test environment. Do not display it as a live merchant payment processor or completed KYC.
6. Obtain owner approval to publish a test listing and execute a Shopify test order. Display actual API response/receipt.
7. Simulate test shipping events from a documented fixture or use a real sandbox shipment; explicitly distinguish mock shipment from shipped parcel.
8. Owner pauses/resumes via Gluons portal; connected assistant reads the same task state. All provider logins and approvals remain on secure external/owned web surfaces.

## Release-blocking acceptance tests

- One Gluons user binds ChatGPT and Claude to the same permitted organization; revoking one binding does not revoke the other.
- Unverified email and tampered setup URLs never link accounts; expired setup requests cannot be reused.
- OAuth state/PKCE, tenant and scopes are verified; callback replay rejected.
- A user with two business organizations cannot read the other organization's Shopify account through a tool call without authorization.
- Read permission cannot create a product or approve a supplier purchase; policy grant cannot exceed provider scope.
- Customer can cancel during provider onboarding; no credentials remain accidentally granted.
- Merchant ownership/terms, Shopify plan billing, Stripe onboarding and Shopify Payments KYC never auto-complete through model assertions.
- Publish, supplier-contract acceptance, payment, payout and high-value refund require documented, owner-authorized state transition.
- Supplier email cannot be sent twice under repeated queue events; vendor confirmation is not fabricated.
- Fulfillment only marked complete after verified operational state; simulated delivery clearly labeled.
- Pausing prevents new external actions while presenting already-completed irreversible actions honestly.
- Works as Gluons PWA on Android/iOS without assuming a ChatGPT mobile custom MCP connection.
- Owner can export audit history and disconnect a provider or assistant, with dependent automations safely paused.

## Primary external sources (verify versions before coding)

- ChatGPT plugin/app availability and account connections: https://help.openai.com/en/articles/20001256-plugins-in-chatgpt
- ChatGPT sign-in identity offering (limited participating partners): https://help.openai.com/en/articles/20001410-sign-in-with-chatgpt
- MCP auth flow: https://developers.openai.com/plugins/build/auth
- Shopify merchant signup: https://help.shopify.com/en/manual/intro-to-shopify/initial-setup
- Shopify client-transfer stores: https://shopify.dev/docs/apps/build/stores/client-transfer-stores
- Shopify collaborator accounts: https://help.shopify.com/en/partners/manage-clients-stores/working-on-client-stores
- Shopify Payments eligibility and verification: https://help.shopify.com/en/manual/payments/shopify-payments/supported-countries
- Third party payment providers: https://help.shopify.com/en/manual/payments/third-party-providers
- Stripe Connect onboarding: https://stripe.com/connect/features
- Shopify fulfillment scopes: https://shopify.dev/docs/api/admin-graphql/latest/mutations/fulfillmentCreate
