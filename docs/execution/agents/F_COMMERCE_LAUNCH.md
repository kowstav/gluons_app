# Agent F — bounded commerce launch

Own EX-08/11/12 with D for provider adapters. Start with `launch.py`, `commerce/service.py`, `commerce/install.py`, `commerce/shopify.py`, `connectors/shopify.py`, launch/commerce routes and tests. Read blueprint doc 14 and the commerce playbook, then current ADRs before coding.

## First package: readiness, not a giant storefront rewrite

The existing launch serializer/evidence endpoint returns conservative fixed readiness while the commerce service can store verified action receipts. Implement one evidence-derived projection that web, REST and MCP share. Preserve conservative behavior where actual evidence is missing; do not simply flip fixed booleans to true.

## Subsequent bounded launch

Select one country, product type, provider/store path and fulfillment model. Reuse an existing merchant/dev store first. For a new merchant, use only a currently supported owner signup/partner-transfer route and preserve owner-held accounts/billing/KYC. Draft internal content while provider review is pending.

Implement staged planning → owner plan approval → verified store → catalog draft → supplier/source review → payment/configuration/test-checkout readiness → exact go-live approval → verified publication → monitored orders/fulfillment. Each transition declares evidence, permission, timeout/retry and human exception handling. This is not permission to implement unsupported account creation or auto-negotiate binding commercial commitments.

## Acceptance

- COM-01 through COM-04 and FLOW-02/03/04/05 apply.
- One readiness response across surfaces; old receipt history persists while current readiness can degrade after expiry/revocation.
- Evidence must match tenant, store, plan/version, resource, action, environment and freshness. A production claim cannot use test receipts.
- Missing KYC/payment/tax/shipping/checkout evidence blocks final launch and explains the exact next owner/provider action.
- External catalog drafts use the gateway. Internal drafts remain possible without provider signup.
- Batch publication binds a reviewed finite manifest; edits invalidate approval; partial success remains itemized and retries reconcile.
- Supplier outreach uses exact approved recipient/content/sending identity; no guessed supplier evidence, unauthorized contact or spending.
- A shipping adapter must produce legitimate dispatch evidence before Shopify fulfillment requires it. Label purchase, shipment record, dispatch, tracking update and delivery are separate facts.
- Completed provider effects survive later projection failure and are reconciled without duplicate execution.

## Ownership

Own launch orchestration/readiness. D owns provider adapters and connection lifecycle. B owns MCP transport, E UI, A shared schema/config. Coordinate receipt schemas before parallel work. Keep Stripe Connect behind an explicit separate product decision. Do not collect identity/bank documents in generic chat or Passport. If a missing provider makes a stage impossible, leave that stage blocked and deliver the verified earlier stage with an honest handoff.

Run business-launch, commerce, account-connection, policy/runtime and affected MCP/UI tests. Real checkout and provider behavior require their own dated evidence; doubles only prove Gluons handling.
