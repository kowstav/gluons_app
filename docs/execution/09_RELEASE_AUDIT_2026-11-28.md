# Release audit on November 28, 2026

Baseline `7ecbc219834ce4899b5ae4366531053db7874f01`. Reviewed the original owner onboarding and authorization blueprint, the November execution plan, buyer-facing setup and deployment guides, the current setup and lifecycle code, and the dated GitHub and staging results. The product starts in an assistant chat. Gluons holds the durable state and uses targeted owner handoffs for sign-in, provider consent and consequential approvals. First connecting a host still uses Gluons sign-in and consent. A supported host's setup controls, owner consent and device verification are part of onboarding.

## What the evidence establishes

| Area | Evidence observed | Work remaining |
|---|---|---|
| EX-00 baseline | Successful GitHub CI for backend, browser, dependency, secret, migration, image and Terraform checks. [Release CI](https://github.com/kowstav/gluons_app/actions/runs/46031775262) | Repeat on each release candidate and retain exact image and migration evidence. |
| EX-01–03 existing-business workflow | Gateway-backed Gmail proof harness with provider doubles. Shadow order entry and inventory reorder run through MCP. The order graph uses the labelled ERP simulator and checks approved terms and draft status. The ERPNext owner handoff can verify account identity and read access at a configured site, then securely store the key and require account confirmation. [Provider harness](EX01_PROVIDER_PROOF.md) · [ERPNext setup](../EXTERNAL_DEPENDENCIES.md) | Real Gmail consent, ingest, refresh/revocation and approved send/readback. Select a participating business and its real order system. Configure its approved site, connect a restricted account and verify an order record. Run duplicate, interruption and revocation cases against the chosen provider within the authorized scope. |
| EX-04 and EX-13 assistant continuity | Live ChatGPT staging connection created a launch draft, saved an answer and retrieved it from a second conversation. Local SDK tests cover task controls and authorization. [Staging result](../DEPLOYMENT_STATUS.md#chatgpt-staging-onboarding-on-november-27-2026) | Existing-business task start, observation, pause and reconnect in ChatGPT. Independent binding to a second compatible host, same task visibility and selective revocation. |
| EX-05 and EX-07 chat-first intake | Typed Passport and purpose-bound sharing previews, durable setup, order and inventory planning. The November 28 change adds offer and handoff classification, an explicit decision summary, required rules for delegated choices, and separate setup areas. [Setup guide](EX05_CHAT_FIRST_SETUP.md) | Hosted acceptance of the expanded intake, profile reuse and independent-user measurements. Move routine questions and configuration currently on owner pages into chat, including choosing approved mailbox labels. Add typed handoffs for other provider families. |
| EX-06 sign-in and owner authority | Hosted email OTP and assistant OAuth. Exact policy signing with a virtual WebAuthn authenticator in GitHub. Connection, policy, spending and activation enforced separately. [Deployment status](../DEPLOYMENT_STATUS.md) | SMS OTP, passkey sign-in/recovery and explicit Google account linking require separate identity work. Prove signed spending allowance on a real device. Checkout receipts and actual payouts have separate provider/KYC and authority dependencies. |
| EX-08, EX-11 and EX-12 launch and fulfillment | Durable commerce planning, shared readiness projection, gated Shopify adapters and provider-evidence rules. [Readiness](EX08_READINESS.md) | One bounded real store launch. Add or verify authoritative payment, tax, checkout, supply and fulfillment prerequisites. Require test checkout and independent publication and dispatch evidence. Existing digital product and service intake produces a saved planning draft. |
| EX-09 operations | Scanned images deployed to staging, migrations and skills synchronized, first worker wake and automated backup observed. GitHub restored a separate PostgreSQL database and checked audit chain, tenant isolation and a retained simulator receipt. [Deployment](../DEPLOYMENT_STATUS.md) · [Restore CI](https://github.com/kowstav/gluons_app/actions/runs/46030568630) | Prove hosted alert delivery, recovery after lost wake or worker interruption, and restore into isolated Cloud SQL with jobs fenced. Configure a production project, domains, durable mail, reviewer and backup/alert recipients before approved canary. Staging SQL was stopped at the end of the last pilot. |
| EX-10, EX-14 and EX-15 quality and expansion | Registered skill contracts, developer fixtures, model gating, budgets and usage ledger. A draft-only inventory-to-procurement handoff supports one pair. [Skill evidence](../SKILL_CAPABILITIES.md) | Provider-specific model evaluations and measured cost. Observe repeatable real use and reconcile costs. Add reusable composition across approved triggers and compatible inputs, per-edge authorization, observability and pause behavior. Promote skills individually after expert, provider and rollback proof. |

The first production gate is a real, authorized existing-business workflow with one independently verified business result, followed by recovery and revocation evidence. Staging draft persistence, SMTP sign-in, CI, and simulator results each support narrower claims.

## Setup areas and reusable authority

| Setup area | Once per relevant account or channel | Per workflow |
|---|---|---|
| Email and messages | Confirm inbox or messaging business/number and scopes | Allowed labels, recipients, content and sending policy |
| Phone calls | Confirm number, country route and call provider | Call purpose, routing, escalation and recording or consent rules |
| Receive payments | Merchant checkout and settlement setup | Pricing, currency, refunds, checkout status and reconciliation |
| Send payments | Authorized payment method and owner approval method | Payee, currency, exact approval or enforceable limits, spend and receipts |
| Website and storefront | Grant access to the authorized site, store or CMS | Read, edit or publish scope and approval for external changes |
| Alerts and escalations | Reuse an authorized message or call channel | Event, recipient, urgency and schedule |
| Business records | Connect the source of truth such as an ERP, CRM, POS or accounting system | Records and fields allowed, write approval and required readback |
| Documents and data | Authorize a file store and approve shareable profile fields | Input data, purpose, retention and reviewed sharing |
| Shipping and fulfillment | Connect the fulfillment system or carrier | Supplier or order routing, proof of dispatch and delivery reconciliation |
| Team access | Establish identities, roles and reviewers | Decision ownership, escalation, audit visibility and revocation |

A business can reuse confirmed profile fields and accounts within their original scope. Chat setup records what was chosen and who owns decisions. The applicable provider consent, policy and approval controls remain the source of authority. A planning estimate or simulated receipt carries its own evidence label.

## Remaining work by owner

| Owner | Next actions |
|---|---|
| Engineering | Maintain green release CI. Continue the existing-business chat trial and connector failure matrix. Prepare a redacted provider evidence record and an isolated hosted restore and alert procedure. Complete additional in-chat setup, data reuse, composition and provider workflows in dependency order. |
| Business owner | Choose the first real consenting workflow and authorized provider test accounts, set the exact permitted actions and recipients, and approve a bounded cloud window under the spending ceiling. Complete user-held consent and device checks. Select production project, domains and reviewer and approve the reviewed production cost plan before deployment. |
| Provider or reviewer | Complete applicable account verification, app/distribution review, KYC and approved test access. Supply domain review for skills that require it. Production measurements and business receipts depend on actual executions with these permissions. |
