# Commerce integration verification — 2026-11-02

Official provider documentation was checked before implementation. No real Shopify,
Gmail supplier, payment or fulfillment account was used in these tests.

## Implemented boundary

- Owner-browser-bound, expiring one-time Shopify authorization sessions, exact
  lowercase myshopify.com validation, callback HMAC/timestamp/state/session checks,
  encrypted expiring offline access/refresh tokens, provider-read store identity and
  actual granted scopes. The confidential standalone-app flow documents state/HMAC
  and expiring offline tokens, not PKCE; no unsupported PKCE behavior is invented.
- Manual shop entry is limited to `GLUONS_SHOPIFY_PILOT_SHOPS`. This is not an
  App Store approved distribution flow. Merchant signup and Partner transfer remain
  provider-owned handoffs.
- `shopify.product.create_draft` uses the common action gateway, scopes, grants and
  budgets. It creates only DRAFT products and verifies identity, handle, title,
  vendor, description and draft status from a separate read. Missing lookup after
  ambiguous creation requires reconciliation, not another write.
- Raw-body webhook HMAC validation, connection-account routing, authoritative order
  readback and durable content-based dedupe. Unsigned delivery IDs cannot bypass
  body dedupe. Other topics are quarantined; no webhook creates a purchase, refund,
  publication, physical shipment or irreversible account transition.
- Shopify Payments `activated` is reported as provider setup completion only. It
  never certifies KYC, current payment acceptance, settlement or live checkout.
  Test-paid order records can prove a labelled test payment. Fulfillment/tracking
  records do not prove physical delivery.
- Supplier source records remain reliability-unverified. First inquiry needs an
  exact owner web approval including recipient, text, represented entity and sender
  connection, plus a separate signed automation policy. Sending uses the existing
  Gmail adapter and independent Sent-mail readback, with no claimed delivery proof.

## Remaining verification/dependencies

Real merchant consent, refresh/revocation, approved app distribution, webhook
subscriptions, sandbox catalog reads/writes and real supplier email need configured
accounts and tests. Current product staging does not implement theme publication,
tax/shipping configuration, provider-approved checkout, carrier/3PL physical
fulfillment or merchant KYC. These remain explicit launch gates. No code can
legitimately infer those facts from a model or an owner checkbox.

App uninstall is irreversible under Shopify's API and is not silently performed
when a Gluons connection is locally fenced. The owner can remove the app in Shopify
admin. A dedicated explicitly authorized uninstall operation would need its own
reviewable request and recovery disclosure.

## Sources

- [Standalone authorization](https://shopify.dev/docs/apps/build/authentication-authorization/authenticate-standalone-apps)
- [Webhook verification](https://shopify.dev/docs/apps/build/webhooks/verify-deliveries)
- [productCreate](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productCreate)
- [ShopifyPaymentsAccount](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/ShopifyPaymentsAccount)
- [appUninstall](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/appUninstall)

Tests in `apps/api/tests/test_commerce_integrations.py` use real PostgreSQL tenant
policies and explicitly labelled HTTP provider doubles, not live provider sandboxes.
