# Gluons research: Shopify, Stripe, ERPNext, LLM providers, licenses
Checked 2026-11-02 against official sources. All prices are USD per 1M tokens on that date. `{shop}` and similar are placeholders.
Method: Stripe docs, Anthropic pricing/versioning, PyPI JSON, GitHub license data, npm metadata, and ERPNext/Frappe/google-genai/openai source files were read as raw text. Shopify, Gemini API, and n8n pages came through a summarizing fetch tool, so spot-check any Shopify value you hard-code. Google Agent Platform prices and retirement dates were read from the rendered page.
Size figures are computed from PyPI wheel metadata (no package was downloaded or installed). Installed size is typically 2-3x the compressed figure. Target interpreter is the project venv, CPython 3.13.3 on Windows, plus Linux x86_64.

## 0. Blueprint conflicts
| # | Blueprint claim or implied assumption | Verdict | Current fact |
|---|---|---|---|
| 1 | Doc 14 Route A: merchants "install/authorize Gluons' Shopify app" (one app, many tenants, not necessarily public) | WRONG for custom distribution | API Terms s2.3.20: "Custom Applications may not be installed by more than one Merchant." Custom distribution means one store, or stores in one Plus org, plus dev stores. Many unrelated merchants require public distribution (listed or unlisted), and both go through app review. See 1.7 |
| 2 | Not covered: consequences of going public | MISSING, affects product | App Store requirements: s1.2.1 "must use Shopify App Pricing or the Shopify Billing API for any app charges"; s2.3.1 install must start on a Shopify surface and must not ask for manual entry of a myshopify domain. A `/connections` "enter your store" field fails review, and Stripe-billed Gluons fees for App Store merchants conflict with 1.2 |
| 3 | Doc 14 Route B: build a client transfer store through Admin APIs | PARTLY WRONG | Client transfer stores accept only free and partner-friendly apps. "Custom and draft apps can't" be installed, so an unreviewed Gluons app cannot configure one. A forum staff reply suggests otherwise; the official page wins until tested |
| 4 | Doc 14 demo: "real Shopify dev or client transfer sandbox" | NARROW IT | Use a dev store. Dev stores accept custom-distribution apps but can never be transferred or go live |
| 5 | Doc 14: `fulfillmentCreate` needs fulfillment-order scopes and fulfill-and-ship permission | CONFIRMED, add approval | One of `write_assigned_fulfillment_orders`, `write_merchant_managed_fulfillment_orders`, `write_third_party_fulfillment_orders`, plus user permission `fulfill_and_ship_orders`. All three scopes are labeled "Shopify approval required"; declaring one before approval fails app version creation with an `app_access` error. Plan lead time |
| 6 | Doc 14: Stripe Connect "is not automatically" a Shopify gateway | UNDERSTATED | Shopify: "Stripe isn't listed as a separate third-party payment gateway", and where Shopify Payments is supported "Stripe won't be available for you to activate". Payments Apps API is limited to approved partners. Do not model Stripe as a Shopify checkout route |
| 7 | Doc 14: MCP tools return short-lived setup URLs | CONSTRAINT | Stripe Account Links are single use, expire in minutes, and must not be sent outside the platform app. Return a Gluons `/setup/{session}` URL and mint the Account Link only after the owner authenticates. Hosted onboarding works only in real browsers, not embedded web views |
| 8 | Doc 03: "subscription + metered billing" from a payment provider | UPDATE | Stripe now marks the Billing Meters guide "Not Recommended" for new integrations and points to Metronome. Metronome lacks Connect, Stripe Dashboard and Workflows support and has limited Checkout. Billing Meters stay fully supported for existing integrations. Decision needed, see 2.5 |
| 9 | Doc 11: Activepieces `selective_embed` of pieces | UNREALISTIC from Python | License claim holds. Pieces are TypeScript npm packages that need Node. Reclassify as `sidecar` or `reference_only`, see 5.3 |
| 10 | Doc 02: "Vertex Gemini" | RENAMED | Product is now Gemini Enterprise Agent Platform. `genai.Client(enterprise=True, ...)` is current; `vertexai=True` is a legacy flag that still works |
| 11 | Doc 11: Docling `embed` | CAUTION | License holds, but `pip install docling` pulls torch and on Linux the default CUDA stack: about 3.4 GB compressed. Use `docling-slim` extras or CPU torch |
| 12 | Doc 11: Browser Use `optional` | CONSTRAINT | Its exact pins conflict with current `anthropic`, `google-genai` and `mcp`. Run it in its own environment, see 5.2 |
| 13 | Brief: `pgserver` for local Postgres | OUTDATED | Last release 2024-06, wheels stop at cp312, no sdist. It will not install on the project's Python 3.13. Use `pixeltable-pgserver` |
| 14 | Doc 14: no general public API for client-transfer stores | CONFIRMED | Dev Dashboard only. New since doc 14: dev stores can be scripted with `shopify store create dev` (CLI 4.8, changelog 2026-09-09). No GraphQL mutation is documented |
| 15 | Doc 11 license column | ALL CONFIRMED | No listed license differs. Note `elevenlabs-python` has no LICENSE file; MIT comes only from `pyproject.toml` and PyPI metadata |
| 16 | Doc 11 s.C: n8n and Windmill restrictions | CONFIRMED | See 5.4. The AI-agent wording is in n8n's support article, not only the FAQ |

## 1. Shopify
### 1.1 API version
Latest stable: `2026-10` (released 2026-10-01, usable until 2027-10-16). Also supported: `2026-07`, `2026-04`, `2026-01`. Next: `2027-01`. Versions ship quarterly and last at least 12 months. Endpoint: `POST https://{shop}.myshopify.com/admin/api/2026-10/graphql.json` with header `X-Shopify-Access-Token: <token>`.
### 1.2 Authorization
Embedded apps use token exchange (`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, `subject_token` is an App Bridge ID token). Standalone or API-only apps, which is the Gluons backend, use the authorization code grant. Stores in your own organization can use client credentials. Managed installation is the default for CLI-scaffolded apps; scopes go in `shopify.app.toml` under `[access_scopes]`.

Gluons flow:
1. Redirect to `https://{shop}/admin/oauth/authorize?client_id=..&scope=<comma list>&redirect_uri=..&state=<nonce>`. `redirect_uri` must exactly match the Dev Dashboard or TOML value. Add `grant_options[]=per-user` only for an online token.
2. Callback receives `code, hmac, shop, state, timestamp`. Validate in order: (a) `state` equals the stored nonce; (b) remove `hmac`, sort remaining params, join as `key=value` with `&`, HMAC-SHA256 with the client secret, hex digest, constant-time compare; (c) `shop` matches `^[a-zA-Z0-9][a-zA-Z0-9\-]*\.myshopify\.com$` (both anchors are required).
3. `POST https://{shop}/admin/oauth/access_token`, form-encoded, `Accept: application/json`, body `client_id, client_secret, code, expiring=1`.
4. Response: `access_token, scope, expires_in, refresh_token, refresh_token_expires_in`. Compare `scope` to what was requested, because merchants can change it. `write_*` includes `read_*`.

Token lifetimes:
| Token | Lifetime | Notes |
|---|---|---|
| Expiring offline | 1 hour (`expires_in: 3600`) | Read the response value, do not hard-code |
| Refresh token | 90 days at issue (`refresh_token_expires_in: 7776000`) | Can shorten with use, see rotation |
| Non-expiring offline | None | Until uninstall or secret revocation. Not allowed for public apps after the deadlines below |
| Online | 24 hours or admin logout | Adds `associated_user`, `associated_user_scope`; no refresh token |
| Client credentials, own org | 24 hours (`expires_in: 86399`) | Fails with `shop_not_permitted` outside your org |
| Application-only (client assertion) | Up to 1 hour for public apps, 24 hours for others | No refresh token |

Refresh: `POST https://{shop}/admin/oauth/access_token` with `client_id, client_secret, grant_type=refresh_token, refresh_token`. Store the new pair. The presented refresh token dies at the earliest of: newer token used, new token acquired via grant, 30 days after first use, or original expiry. Getting a new expiring token retires older ones for that app and store. Retry timeouts, 429 and 5xx with the same token. On 401 `{"error":"invalid_request"}`, clear tokens and re-authorize. Serialize refresh per shop (design advice, not Shopify text).

Deadlines: new public apps since 2026-04-01 must use expiring tokens. Existing public apps: 2027-01-01 delisted and blocked from new installs if non-compliant; 2027-01-15 admin warnings to merchants; 2027-04-01 affected Admin API requests return 403. Custom apps and merchant-created apps are excluded.

Application-only tokens, useful for background jobs on public apps: `POST https://{shop}.myshopify.com/admin/oauth/access_token`, `grant_type=client_credentials`, `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`, `client_assertion=<JWT>`, `client_id`. JWT header `alg` RS256 or ES256 plus `kid`. Claims `iss` and `sub` = client ID, `aud` = the store's token URL on its permanent domain, `iat`, `exp` at most 300 s after `iat`, `jti` unique across all stores. Do not send the client secret or `scope`. Register the public key as a JWK in Dev Dashboard, Settings, Credentials, Public keys.
### 1.3 Webhooks
Verify: HMAC-SHA256 over raw body bytes with the app client secret, base64-encode, constant-time compare to `X-Shopify-Hmac-SHA256`. Capture the body before any parser. Reject with 401.
Headers: `X-Shopify-Topic`, `X-Shopify-Hmac-SHA256`, `X-Shopify-Shop-Domain`, `X-Shopify-Webhook-Id`, `X-Shopify-Triggered-At`, `X-Shopify-API-Version`, `X-Shopify-Event-Id`, optional `X-Shopify-Name`.
Dedupe on `X-Shopify-Webhook-Id` (unique per delivery). `X-Shopify-Event-Id` is shared by deliveries from one merchant action, so use it to correlate, not dedupe.
Delivery: 1 s connect timeout, 5 s total. Only 2xx is success, 3xx counts as failure. 8 retries over 4 hours, then Admin API subscriptions are auto-deleted. Order is not promised; add a reconciliation job.
Compliance topics: `customers/data_request`, `customers/redact`, `shop/redact`. Mandatory for App Store apps. Act within 30 days; `customers/redact` arrives 10 days after the request and `shop/redact` 48 hours after uninstall. Return 2xx, or 401 on bad HMAC. Declare via `[[webhooks.subscriptions]]` with `compliance_topics` and `uri` in `shopify.app.toml`.
### 1.4 GraphQL operations
| Operation | Scope | Key facts |
|---|---|---|
| `productSet(input, synchronous=true, identifier)` | `write_products` | Prefer for upsert. 2048 variants per product by default. `synchronous: false` returns `ProductSetOperation`; poll `productOperation` |
| `productCreate(product, media)` | `write_products` | Creates only the initial variant; add more with `productVariantsBulkCreate`. `input` deprecated |
| `publishablePublish(id, input: [PublicationInput!]!)` | `write_publications` (alias `write_channels`) | Use this. Input has `publicationId`, optional `publishDate` (online store only). List publications with `read_publications` |
| `productPublish` | `write_publications` | Deprecated: "Use publishablePublish instead." |
| `collectionCreate(collection)` | `write_products` | `input` deprecated. Not on Starter or Retail plans. Unpublished by default, so follow with `publishablePublish` |
| `fulfillmentCreate(fulfillment, message)` | See row 5 in section 0 | `FulfillmentInput`: `lineItemsByFulfillmentOrder` (required), `notifyCustomer` (default false), `originAddress`, `trackingInfo`. Since 2024-10, order apps cannot fulfill orders assigned to another fulfillment service app |
| `orders(first, after, query, sortKey, ...)` | `read_orders` | 60 days only unless `read_all_orders` is added; partner-org apps request that under API access requests. Filters include `test:true`, `financial_status:paid`, `updated_at:>2026-10-01` |

Test orders: `Order.test` is true for orders made with the Bogus Gateway or a provider in test mode. Status fields: `displayFinancialStatus`, `displayFulfillmentStatus`.
`@idempotent(key: ...)` exists on inventory and refund mutations and is required there from `2026-04`.
### 1.5 Protected customer data
Level 1: customer data without name, address, phone, email. Level 2: any of those four, each requested per field. Request in Partner Dashboard, Apps, API access requests, Protected customer data access, then submit for review. Select a distribution method first, even for dev stores. Public apps need review at both levels. Custom apps have both levels "always available". Apps installed only on dev stores need no review.
Without approval, fields are redacted; GraphQL returns HTTP 200 with an `errors` entry and `null` values, so handle partial data.
Level 1 needs: data minimization, purpose limits, merchant privacy agreements, consent and opt-out handling, retention limits, encryption at rest and in transit. Level 2 adds: encrypted backups, separate test and production data, data-loss prevention, restricted staff access, strong staff passwords, access logs, incident response policy.
### 1.6 Dev stores vs client transfer stores
| | Dev store | Client transfer store |
|---|---|---|
| Purpose | Build and test apps | Build for a client, then hand over |
| Created by | Dev Dashboard or `shopify store create dev --name --plan --demo-data --feature-preview --country` | Dev Dashboard only: Stores, Create store, Client transfer store |
| Limit | 250 per organization | Unlimited |
| Transfer | Never | Client email accept, pending expires after 7 days |
| Payments | Test gateway or provider test mode only | Same; test shipping labels only. Deactivate Shopify Payments and Balance before transfer or it fails |
| Password page | Always on | Cannot be removed |
| Apps | Custom-distribution apps allowed | Free and partner-friendly only; custom and draft blocked |
| API creation | CLI only; no GraphQL mutation documented | None documented |
### 1.7 Connecting a merchant-owned store
- **Custom distribution, per merchant.** Dev Dashboard, app Home, Distribution card, Select distribution method, Custom distribution, Manage custom install link, enter the store domain, Generate link, send it to the owner. The card appears only for Partner-organization apps. Distribution choice is permanent. No Shopify app billing. Each merchant needs a separate app and credentials. No API generates links (staff and community forum). Suitable for a small pilot only.
- **Public distribution, unlisted or listed.** Requires App Store registration (one-time fee) and review for both. An unlisted app is hidden from search and installed from its listing link. Then s2.3 applies: start from a Shopify surface, OAuth first, no domain entry, redirect to app UI after approval. Bind the install to a Gluons organization after the owner signs in to Gluons (design advice).
- **Avoid.** A merchant-org Dev Dashboard app means the merchant shares credentials, which conflicts with doc 14. Legacy admin-created custom apps cannot be created since 2026-01-01; existing ones keep working.

Suggested path: dev store for the demo, custom distribution for first pilots, unlisted public app before onboarding unrelated merchants at scale.
### 1.8 Shopify Payments
Country list source: `help.shopify.com/en/manual/payments/shopify-payments/supported-countries`. 41 listed today: AU, AT, BG, BE, CA, CN, HR, CY, CZ, EE, DK, FI, FR, DE, GI, GR, HK, HU, IE, IT, JP, LV, LI, LT, LU, MT, MX, NL, NZ, NO, PL, PT, RO, SG, SI, ES, SE, CH, AE, GB, US. India is not listed. Treat this as data with a checked date, not code.
Read status: `query { shopifyPaymentsAccount { activated onboardable country defaultCurrency } }`. `activated`: setup complete. `onboardable`: can be onboarded. Scope: `read_shopify_payments_accounts` (no approval label) or `read_shopify_payments` (approval required). Use for the `payments.verified` gate. Third-party transaction fees apply even with Shopify Payments active. US stores with Shopify-managed sales tax cannot use third-party providers.

Sources: https://shopify.dev/docs/api/usage/versioning ; https://shopify.dev/docs/apps/build/authentication-authorization ; https://shopify.dev/docs/apps/build/authentication-authorization/authenticate-standalone-apps ; https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens ; https://shopify.dev/docs/apps/build/authentication-authorization/access-token-types/offline-access-tokens ; https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/token-exchange ; https://shopify.dev/docs/apps/build/authentication-authorization/application-only-access-tokens ; https://shopify.dev/docs/apps/build/authentication-authorization/app-installation ; https://shopify.dev/changelog/posts/migrate-to-expiring-offline-access-tokens-before-january-2027 ; https://shopify.dev/docs/apps/build/webhooks/verify-deliveries ; https://shopify.dev/docs/apps/build/webhooks/delivery-structure ; https://shopify.dev/docs/apps/build/webhooks/ignore-duplicates ; https://shopify.dev/docs/apps/build/compliance/privacy-law-compliance ; https://shopify.dev/docs/api/usage/access-scopes ; https://shopify.dev/docs/api/admin-graphql/latest/mutations/productSet ; https://shopify.dev/docs/api/admin-graphql/latest/mutations/productCreate ; https://shopify.dev/docs/api/admin-graphql/latest/mutations/publishablePublish ; https://shopify.dev/docs/api/admin-graphql/latest/mutations/productPublish ; https://shopify.dev/docs/api/admin-graphql/latest/mutations/collectionCreate ; https://shopify.dev/docs/api/admin-graphql/latest/mutations/fulfillmentCreate ; https://shopify.dev/docs/api/admin-graphql/latest/queries/orders ; https://shopify.dev/docs/api/admin-graphql/latest/objects/Order ; https://shopify.dev/docs/api/admin-graphql/latest/queries/shopifyPaymentsAccount ; https://shopify.dev/docs/api/admin-graphql/latest/objects/ShopifyPaymentsAccount ; https://shopify.dev/docs/apps/launch/protected-customer-data ; https://shopify.dev/docs/apps/launch/distribution ; https://shopify.dev/docs/apps/launch/distribution/select-distribution-method ; https://shopify.dev/docs/apps/launch/shopify-app-store/app-store-requirements ; https://shopify.dev/docs/apps/build/stores/client-transfer-stores ; https://shopify.dev/docs/apps/build/stores/development-stores ; https://shopify.dev/changelog/create-and-delete-dev-stores-in-shopify-cli ; https://www.shopify.com/legal/api-terms (updated 2026-10-07) ; https://help.shopify.com/en/manual/apps/about-apps ; https://help.shopify.com/en/manual/payments/shopify-payments/supported-countries ; https://help.shopify.com/en/manual/payments/third-party-providers ; https://help.shopify.com/en/manual/payments/third-party-providers/payment-gateway-availability ; https://shopify.dev/docs/apps/build/payments

UNVERIFIED (Shopify):
- Whether Shopify managed installation works for a standalone app; the docs do not say. Use the authorization code grant.
- How the expiring-token deadlines treat custom-distribution apps from Partner orgs. The page excludes "custom apps" without defining this case. Use `expiring=1` anyway.
- Whether unlisted public apps can avoid Shopify Billing. Forum staff say public apps must use it and exemptions are rare, discretionary, via Partner Support. No official page covers it. Confirm with Partner Support before pricing.
- The Help Center says unlisted apps can be installed "directly from the developer's website"; App Store requirement s2.3.1 says Shopify surfaces only. Treat s2.3.1 as binding until confirmed.
- What `shopifyPaymentsAccount` returns on a shop that never onboarded, and whether any API lists third-party gateways by country.
- Whether `@idempotent` applies to `productSet` or `fulfillmentCreate`, and its key retention. Not shown on the pages read.

## 2. Stripe
### 2.1 Version
Current: `2026-10-28.endive` (no breaking changes). Major release `2026-09-30.endive` carried breaking changes. Preview channel: `2026-10-28.preview`. Header: `Stripe-Version`. `stripe` Python 16.1.0 pins `2026-10-28.endive` (`stripe/_api_version.py`). Webhook endpoints have their own version; match it to the SDK.
Breaking in Endive that touches Gluons: `payment_method_types` removed from Checkout Sessions, Payment Intents and Setup Intents; v2 Account Links `configurations` removed; v2 capabilities gain `rejected`; errors now appear for eventually due requirements.
### 2.2 Accounts v2 vs v1
Stripe describes v2 as highly configurable and v1 as limited. Accounts v2 is generally available for Connect users. Use v2 for a new platform. Stay on v1 for OAuth connected accounts, recipient service agreements, `treasury`, and `card_issuing_*`. Stripe discourages maintaining both indefinitely. A v2 account ID also works on v1 endpoints.
Create: `POST https://api.stripe.com/v2/core/accounts` with `Authorization: Bearer <secret key>`, `Stripe-Version: 2026-10-28.endive`, and JSON body:
`{"contact_email":"..","display_name":"..","dashboard":"full","identity":{"country":"us","entity_type":"company","business_details":{"registered_name":".."}},"configuration":{"merchant":{"capabilities":{"card_payments":{"requested":true}}}},"defaults":{"currency":"usd","responsibilities":{"fees_collector":"stripe","losses_collector":"stripe"}},"include":["configuration.merchant","identity","requirements","defaults"]}`
v2 returns null for many properties unless named in `include`. `dashboard` is `full`, `express`, or `none`. `express` requires both collectors to be `application`. `dashboard: "full"` with Stripe as collector matches doc 14's rule that merchants hold their own accounts. Stripe's v1 onboarding page says dashboard type is permanent, so treat it as fixed on v2 too.
### 2.3 Hosted onboarding
v2: `POST /v2/core/account_links` with `{"account":"acct_..","use_case":{"type":"account_onboarding","account_onboarding":{"return_url":"..","refresh_url":".."}}}`. Do not send `configurations`.
v1: `POST /v1/account_links` with `account, refresh_url, return_url, type=account_onboarding, collection_options[fields]=eventually_due|currently_due`.
Links are single use and expire in minutes. Message apps that preview URLs consume them. `refresh_url` must mint a new link. `return_url` proves nothing and carries no state, so retrieve the account afterward. Live mode requires HTTPS.

| Need | Accounts v1 | Accounts v2 |
|---|---|---|
| Can charge | `charges_enabled` | `configuration.merchant.capabilities.card_payments.status == "active"` |
| Can pay out | `payouts_enabled` | `configuration.merchant.capabilities.stripe_balance.payouts.status == "active"` |
| Details sent | `details_submitted` | No direct field found; check capabilities |
| What is due | `requirements.currently_due`, `eventually_due`, `past_due`, `pending_verification`, `current_deadline`, `errors` | `requirements.entries[]`, with `impact.restricts_capabilities.capability` and `errors` |
| Why blocked | `requirements.disabled_reason` | Capability `status_details` codes |
| Status values | `active`, `inactive`, `pending` on `capabilities` | `active`, `pending`, `restricted`, `unsupported`, `rejected` |
| Event | `account.updated` | `v2.core.account[configuration.merchant].capability_status_updated` |

`disabled_reason` values: `action_required.requested_capabilities`, `listed`, `other`, `platform_paused`, `rejected.fraud`, `rejected.incomplete_verification`, `rejected.listed`, `rejected.other`, `rejected.platform_fraud`, `rejected.platform_other`, `rejected.platform_terms_of_service`, `rejected.terms_of_service`, `requirements.past_due`, `requirements.pending_verification`, `under_review`.
v2 rejection codes in `status_details`: `rejected_fraud`, `rejected_incomplete_verification`, `rejected_listed`, `rejected_other`, `rejected_platform_fraud`, `rejected_platform_other`, `rejected_platform_terms_of_service`, `rejected_terms_of_service`. Older API versions show `restricted` instead, so a `restricted` check alone misses rejection on new versions.
### 2.4 Webhooks
Header: `Stripe-Signature: t=<unix>,v1=<hex>[,v1=<hex>][,v0=<hex>]`, on one line. Ignore everything except `t` and `v1`. Signed payload: `<t>.<raw body>` exact bytes. HMAC-SHA256 with the endpoint `whsec_` secret. Compare to every `v1` in constant time. During secret rolls there can be several `v1` values for up to 24 hours.
Tolerance: SDKs default to 5 minutes (300 s). Never use 0; it disables the check. Each retry gets a new timestamp and signature.
Delivery: up to 3 days with backoff in live mode, 3 tries over hours in sandboxes. No ordering guarantee. Dedupe on event `id`, and for rare double events on `data.object.id` plus `type`. Return 2xx before heavy work.
Destinations: up to 16; create with `POST /v2/core/event_destinations`. `events_from: ["@self"]` for your account, `["@accounts"]` for connected accounts. Thin events for Accounts v2 objects need a "Your account" destination. Thin and snapshot need separate endpoints. Python thin handling: `StripeClient.parse_event_notification(body, sig_header, secret)`.
### 2.5 Usage billing
Billing Meters still work, but the guide is headed "Not Recommended" for new integrations. Billing Meters support Connect, Checkout and the Dashboard, and usage shows up at invoice time. Metronome is recommended for new integrations, with real-time usage and prepaid credits, but has no Connect, Dashboard or Workflows support and limited Checkout. Gluons charging its own tenants is the case to evaluate.
Create a meter: `POST /v1/billing/meters` with `display_name, event_name, default_aggregation[formula]=sum|count|last, customer_mapping[event_payload_key]=stripe_customer_id, customer_mapping[type]=by_id, value_settings[event_payload_key]=value`.
Send usage: `POST /v1/billing/meter_events` with `event_name` (max 100 chars), `payload[stripe_customer_id]`, `payload[value]`, `identifier` (max 100 chars, uniqueness enforced over a rolling period of at least 24 hours, so keep Gluons' own append-only ledger), `timestamp` (past 35 days to 5 minutes ahead).
Limits: 1000 calls/s per account, 1 concurrent call per customer per meter, sandbox counts toward global limits, and `Stripe-Account` requests get the normal 100 ops/s. High volume: session `POST /v2/billing/meter_event_session` (token lasts 15 minutes), then `POST https://meter-events.stripe.com/v2/billing/meter_event_stream` with up to 100 events per request, 10,000 requests/s live only.
Errors arrive asynchronously as thin events `v1.billing.meter.error_report_triggered` and `v1.billing.meter.no_meter_found`. Report before invoice finalization; accepted late events may never be billed.
Stripe on Shopify: see row 6 in section 0. For other providers, use the Shopify gateway page filtered by country. Gluons cannot build its own gateway: the Payments Apps API is limited to approved partners and the docs call the program invitation only.

Sources: https://docs.stripe.com/api/versioning ; https://docs.stripe.com/changelog ; https://docs.stripe.com/changelog/endive ; https://docs.stripe.com/changelog/endive/2026-09-30/removes-configurations-parameter-from-v2-core-account-links-api ; https://docs.stripe.com/changelog/endive/2026-09-30/rejected-accounts-v2-capabilities ; https://docs.stripe.com/connect/accounts-v2 ; https://docs.stripe.com/api/v2/core/accounts/create ; https://docs.stripe.com/api/v2/core/account-links/create ; https://docs.stripe.com/connect/hosted-onboarding ; https://docs.stripe.com/connect/track-account-onboarding ; https://docs.stripe.com/api/accounts/object ; https://docs.stripe.com/webhooks ; https://docs.stripe.com/billing/subscriptions/usage-based/implementation-guide ; https://docs.stripe.com/billing/subscriptions/usage-based/recording-usage-api ; https://docs.stripe.com/billing/subscriptions/usage-based/compare-metronome ; https://docs.stripe.com/api/billing/meter-event/create ; https://docs.stripe.com/api/v2/billing/meter-event-stream/create ; https://help.shopify.com/en/manual/payments/third-party-providers/payment-gateway-availability ; https://shopify.dev/docs/apps/build/payments/payments-extension-review

UNVERIFIED (Stripe):
- Whether standalone Stripe can be activated on Shopify in countries outside the Shopify Payments list. Shopify's page does not name any.
- Metronome pricing, and whether a platform can mix Metronome with Connect through custom work. Stripe's matrix shows Connect as unsupported.

## 3. Frappe and ERPNext REST
### 3.1 Auth and resources
`Authorization: token <api_key>:<api_secret>`. Alternative `Authorization: Basic base64("<api_key>:<api_secret>")`. OAuth: `Authorization: Bearer <token>`. Keys: User record, API Access, Generate Keys. Use a dedicated API user; its roles apply. Send `Accept: application/json` and `Content-Type: application/json`.
List: `GET /api/resource/Sales%20Order?fields=["name","status"]&filters=[["customer","=","X"]]&limit_start=0&limit_page_length=50&order_by=modified%20desc`. URL-encode the JSON values.
Single document: `GET`, `PUT` (changed fields only), or `DELETE` on `/api/resource/Sales%20Order/{name}`. Create: `POST /api/resource/Sales%20Order` with a JSON body.
Default list is 20 records with `name` only. Responses wrap in `{"data": ...}`; method calls in `{"message": ...}`. Failures carry `exc`, `exc_type`.
Filters: JSON array of `[field, operator, value]`, ANDed; `or_filters` has the same shape and is ORed. Operators from `frappe/database/operator_map.py`: `=`, `!=`, `<`, `>`, `<=`, `>=`, `in`, `not in`, `like`, `not like`, `between`, `is`, `timespan`, plus nested-set operators.
HTTP codes from `frappe/exceptions.py`: `ValidationError` 417 (includes `MandatoryError`, `LinkValidationError`, `UniqueValidationError`, `TimestampMismatchError`), `NameError` and `DuplicateEntryError` 409, `PermissionError` 403, `AuthenticationError` 401, `DoesNotExistError` 404.
`/api/v2` also exists (`/api/v2/document/<doctype>`, `/api/v2/document/<doctype>/<name>/method/<method>/`). The develop branch adds `/api/v2/mcp`. Bare `/api` is v1.
### 3.2 Sales Order
`reqd` fields, identical on `version-15` and `version-16`: `naming_series`, `customer`, `order_type` (default Sales), `company`, `transaction_date` (default Today), `currency`, `conversion_rate`, `selling_price_list`, `price_list_currency`, `plc_conversion_rate`, `items`, `status` (default Draft). Items: `item_code`, `item_name`, `qty`, `uom`, `conversion_factor`. `rate` is optional.
`validate_delivery_date` also throws "Please enter Delivery Date" when `order_type` is Sales and `skip_delivery_note` is false, unless each item has a date.
Example body: `{"customer":"Acme","company":"Gluons Demo","transaction_date":"2026-11-02","delivery_date":"2026-11-09","po_no":"SHOP-1001","custom_gluons_idempotency_key":"<hash>","items":[{"item_code":"SKU-1","qty":2,"rate":10.0}]}`
Submit (Sales Order `is_submittable: 1`): `PUT /api/resource/Sales%20Order/{name}` with `{"docstatus":1}`, or `POST /api/method/frappe.client.submit` with `{"doc":<full doc>}`. Cancel: `POST /api/method/frappe.client.cancel` with `doctype`, `name`.
### 3.3 Idempotency
No native key. `po_no` is plain Data with no `unique` flag. `validate_po` rejects a repeat only for the same customer and `po_no` at `docstatus < 2`, only when `skip_delivery_note` is false, and only while Selling Settings `allow_against_multiple_purchase_orders` is off. Each tenant controls that setting, so it is a guard, not a guarantee.
Recommended: add a unique Custom Field once per site with `POST /api/resource/Custom%20Field` and body `{"dt":"Sales Order","fieldname":"custom_gluons_idempotency_key","label":"Gluons Idempotency Key","fieldtype":"Data","unique":1,"read_only":1,"no_copy":1,"insert_after":"po_no"}`.
`dt` and `fieldtype` are required; the `custom_` prefix is only auto-added when `fieldname` is omitted. Then: `GET` with `filters=[["custom_gluons_idempotency_key","=","<hash>"]]`; if absent, `POST`; on `UniqueValidationError` (417), re-read. Set the key to `sha256(tenant + source + external_order_id)`. Use the same pattern for Delivery Note and Sales Invoice.
### 3.4 Version, Docker, license
Current major is v16: `frappe_docker` pins `v16.53.0` in `pwd.yml` and `example.env`; Frappe is at v16.54.0. GitHub's "Latest" badge sits on a v15 backport (`v15.124.3`, 2026-10-30), so ignore the badge. v15 is still patched.
Quickstart: `git clone https://github.com/frappe/frappe_docker`, `cd frappe_docker`, `docker compose -f pwd.yml up -d`.
Wait for the `create-site` container, then open port 8080 and sign in with the README's demo login. Eleven services, `mariadb:11.8`, `redis:6.2-alpine`. Docker and Compose v2 required. The README calls it "Disposable demo only", and custom apps cannot be installed into it. Production steps are in `docs/`.
RAM and disk: the frappe_docker dev guide says allocate at least 4 GB RAM to Docker. Bench wiki minimum is 4 GB RAM, 40 GB disk, 2 CPU for one site with up to 30 users; 32 GB, 160 GB, 8 CPU for 5-10 sites with 10-30 users. Frappe's self-hosting handbook cites 16 CPU, 16 GB, 500 GB SSD.
Licenses: ERPNext GPL-3.0 (`license.txt`), Frappe MIT, frappe_docker MIT. Calling REST from a separate service fits doc 11's boundary; shipping a Frappe app into ERPNext needs legal review.

Sources: https://docs.frappe.io/framework/user/en/api/rest ; https://docs.frappe.io/framework/user/en/guides/integration/rest_api/token_based_authentication ; https://github.com/frappe/erpnext/blob/version-16/erpnext/selling/doctype/sales_order/sales_order.json ; https://github.com/frappe/erpnext/blob/version-16/erpnext/selling/doctype/sales_order/sales_order.py ; https://github.com/frappe/erpnext/blob/version-16/erpnext/selling/doctype/sales_order_item/sales_order_item.json ; https://github.com/frappe/frappe/blob/version-16/frappe/exceptions.py ; https://github.com/frappe/frappe/blob/version-16/frappe/database/operator_map.py ; https://github.com/frappe/frappe/blob/version-16/frappe/model/document.py ; https://github.com/frappe/frappe/blob/develop/frappe/client.py ; https://github.com/frappe/frappe/blob/develop/frappe/api/v2.py ; https://github.com/frappe/frappe/blob/develop/frappe/custom/doctype/custom_field/custom_field.py ; https://github.com/frappe/frappe_docker ; https://github.com/frappe/frappe_docker/blob/main/docs/05-development/01-development.md ; https://github.com/frappe/bench/wiki/OS-and-Hardware-Specifications ; https://frappe.io/handbook/engineer-self-hosting ; https://github.com/frappe/erpnext/releases

UNVERIFIED (ERPNext):
- That the minimal POST body above auto-fills `naming_series`, `currency`, rates, `item_name`, `uom`, and `conversion_factor`. Run against a real site before relying on it.
- `PUT {"docstatus":1}` as a submit: inferred from `check_docstatus_transition` and `update_doc`; the REST page does not document submission.
- Docker-specific production RAM and disk. No official frappe_docker table was found. The bench wiki page is old.

## 4. LLM providers
Prices below are standard tier on 2026-11-02.
### 4.1 Anthropic Messages API (`anthropic` 2.3.0)
`POST https://api.anthropic.com/v1/messages` with `x-api-key`, `content-type: application/json`, `anthropic-version: 2026-10-21`. Prior version `2023-06-01` still exists. SDK 2.0.0+ uses the new version by default.
Breaking on `2026-10-21`: `temperature`, `top_p`, `top_k` return 400 on every model, even at default values. A `document` block directly in an `assistant` message, or `text` with non-empty `citations` in a `user` message, also returns 400. Strip sampling params from any shared Gluons adapter.
| Model id | Input | 5m cache write | 1h cache write | Cache read | Output | Batch in/out |
|---|---|---|---|---|---|---|
| `claude-fable-5-1` | 10.00 | 12.50 | 20.00 | 0.25 | 50.00 | 5.00 / 25.00 |
| `claude-opus-5-5` (docs' default start) | 4.00 | 5.00 | 8.00 | 0.20 | 20.00 | 2.00 / 10.00 |
| `claude-sonnet-5-5` | 2.00 | 2.50 | 4.00 | 0.10 | 10.00 | 1.00 / 5.00 |
| `claude-haiku-5-5`, prompt up to 100K | 0.10 | 0.125 | 0.20 | 0.01 | 0.50 | 0.05 / 0.25 |
| `claude-haiku-5-5`, prompt over 100K | 0.50 | 0.625 | 1.00 | 0.05 | 2.50 | 0.25 / 1.25 |

All four have 1M context and 128K max output. Legacy still served: `claude-fable-5`, `claude-opus-5` (5/25), `claude-opus-4-8`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-5` (2/10), `claude-sonnet-4-6` (3/15), `claude-haiku-4-5` (1/5). US-only `inference_geo` adds 1.1x. IDs are dateless pinned snapshots.
Structured output is GA, no beta header: `output_config: {format: {type: "json_schema", schema: {...}}}`, plus `strict: true` on tools. Unsupported in schemas: recursion, `minimum`, `maximum`, `multipleOf`, `minLength`, `maxLength`, `additionalProperties` other than false. Python helper: `client.messages.parse(output_format=Model)`. Check `stop_reason` for `refusal` and `max_tokens` before parsing.
Usage: `input_tokens` (uncached tail only), `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `cache_creation.ephemeral_5m_input_tokens`, `cache_creation.ephemeral_1h_input_tokens`. Total input = `cache_read_input_tokens + cache_creation_input_tokens + input_tokens`. Billing code must not treat `input_tokens` as the total.
Sources: https://platform.claude.com/docs/en/about-claude/pricing ; https://platform.claude.com/docs/en/models/overview ; https://platform.claude.com/docs/en/api/versioning ; https://platform.claude.com/docs/en/api/migration-2026-10-21 ; https://platform.claude.com/docs/en/build-with-claude/structured-outputs ; https://platform.claude.com/docs/en/build-with-claude/prompt-caching
Also from Anthropic's bundled API reference (cached 2026-10-06, not re-fetched): Opus 5.5 and Sonnet 5.5 reject forced `tool_choice` (`any`, `tool`) with 400, and `{type:"disabled"}` thinking with 400. Use `auto` with `strict: true`, or structured outputs.
### 4.2 OpenAI (`openai` 3.42.1)
Responses API (`POST https://api.openai.com/v1/responses`) is recommended for all new projects. Chat Completions "remains supported". Assistants API was shut down 2026-08-26. Docs moved to `developers.openai.com/api/docs`.
| Model id | Input | Cached | Output | Over 272K input (in/cached/out) |
|---|---|---|---|---|
| `gpt-6-astra` (docs' default) | 10.00 | 1.00 | 50.00 | 20.00 / 2.00 / 75.00 |
| `gpt-6.1-sol` | 2.00 | 0.10 | 10.00 | 4.00 / 0.20 / 15.00 |
| `gpt-6.1-terra` | 1.00 | 0.05 | 5.00 | 2.00 / 0.10 / 7.50 |
| `gpt-6-luna` | 0.10 | 0.01 | 0.50 | 0.20 / 0.02 / 0.75 |

All four: 1.05M context, 128K max output. Batch and Flex are about 50% of Standard; Fast (formerly Priority) about 2x. `gpt-6.1-sol` serves both endpoints; effort `low`, `medium` (default), `high`, `xhigh`, `max`. Terra and Luna also accept `none`.
Structured outputs: Responses uses `text: {format: {type: "json_schema", name, schema, strict: true}}`; Chat Completions uses `response_format`. Python: `client.responses.parse(text_format=Model)`, read `response.output_parsed`. Handle `type == "refusal"` items and `status == "incomplete"` with `incomplete_details.reason == "max_output_tokens"`.
Usage (Responses): `input_tokens`, `input_tokens_details.cached_tokens`, `input_tokens_details.cache_write_tokens`, `output_tokens`, `output_tokens_details.reasoning_tokens`, `total_tokens`. Chat Completions: `prompt_tokens`, `completion_tokens`, `total_tokens`, `prompt_tokens_details.{cached_tokens,cache_write_tokens,audio_tokens,image_tokens,text_tokens}`, `completion_tokens_details.{reasoning_tokens,audio_tokens,text_tokens,accepted_prediction_tokens,rejected_prediction_tokens}`. With `previous_response_id`, prior input is billed again as input.
Sources: https://developers.openai.com/api/docs/models ; https://developers.openai.com/api/docs/pricing ; https://developers.openai.com/api/docs/models/gpt-6.1-sol ; https://developers.openai.com/api/docs/guides/migrate-to-responses ; https://developers.openai.com/api/docs/guides/structured-outputs ; https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_usage.py ; https://github.com/openai/openai-python/blob/main/src/openai/types/completion_usage.py
### 4.3 Gemini on Google Cloud (`google-genai` 2.34.0)
Vertex AI is now branded Gemini Enterprise Agent Platform; old URLs still resolve. Client: `genai.Client(enterprise=True, project="..", location="global")`. Env: `GOOGLE_GENAI_USE_ENTERPRISE`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`. `vertexai=True` and `GOOGLE_GENAI_USE_VERTEXAI` still work; `enterprise` wins on conflict.
| Model id | Lifecycle | Input | Cached | Output | Retirement |
|---|---|---|---|---|---|
| `gemini-4-argon` | 12-month, promo | 2.00, then 4.00 from 2026-12-01 | 0.10, then 0.20 | 10.00, then 20.00 | 2027-10-13 or later |
| `gemini-3.8-flash` | 12-month, promo | 0.75, then 1.50 from 2027-01-01 | 0.075, then 0.15 | 3.75, then 7.50 | 2027-09-02 or later |
| `gemini-3.5-flash` | 12-month | 1.50 | 0.15 | 9.00 | 2027-05-19 or later |
| `gemini-3.5-flash-lite` | 12-month | 0.30 | 0.03 | 2.50 | 2027-07-21 or later |
| `gemini-3.1-flash-lite` | 12-month | 0.25 (audio 0.50) | 0.025 | 1.50 | 2027-05-07 or later |
| `gemini-3.1-pro-preview` | Preview | 2.00; over 200K 4.00 | 0.20; 0.40 | 12.00; 18.00 | Not listed |

Prices are the global endpoint; non-global adds 10%. "12-month" means the Agent Platform table of models available at least 12 months after release. Avoid: `gemini-3.6-flash` retires 2026-11-19; `gemini-3.7-flash` retires 2027-01-28; `gemini-2.5-pro`, `-flash`, `-flash-lite` list retirement 2026-10-20 on Agent Platform. Gemini 4 charges for retrievable usage on completions over 32K even on timeouts, from 2026-11-13. Budget tables must use the post-promo prices. `gemini-3.8-flash`: 1,048,576 context, 65,536 output; use `thinking_level` (`low`, `medium` default, `high`), not integer budgets.
`response.usage_metadata`: `prompt_token_count`, `candidates_token_count`, `thoughts_token_count`, `cached_content_token_count`, `tool_use_prompt_token_count`, `total_token_count`, `traffic_type`, plus `prompt_tokens_details`, `candidates_tokens_details`, `cache_tokens_details`, `tool_use_prompt_tokens_details`.
ADC: local `gcloud auth application-default login` (add `--impersonate-service-account=<email>` to test a service account). Cloud Run: attach a service account; ADC falls through to the metadata server. Lookup order: `GOOGLE_APPLICATION_CREDENTIALS`, local ADC file, metadata server. Do not set `GOOGLE_APPLICATION_CREDENTIALS` if you want the login credentials.
Sources: https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing ; https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions ; https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash ; https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start ; https://ai.google.dev/gemini-api/docs/pricing ; https://ai.google.dev/gemini-api/docs/models ; https://github.com/googleapis/python-genai ; https://docs.cloud.google.com/docs/authentication/application-default-credentials
UNVERIFIED (Gemini): the exact IAM role for Cloud Run callers (commonly `roles/aiplatform.user`); whether the rebrand changed any IAM names. Check before granting.
### 4.4 OpenAI-compatible endpoints
Use the `openai` SDK with `base_url`. Keep a per-provider capability table instead of assuming parity.
| | vLLM 0.32.0 (Apache-2.0) | Ollama server v0.40.13 (MIT), Python client 0.6.3 |
|---|---|---|
| Base URL | `http://localhost:8000/v1` | `http://localhost:11434/v1/`; cloud `https://ollama.com/v1` |
| API key | Matches server `--api-key` or `VLLM_API_KEY`. Covers only `/v1`, `/v2`, `/inference`; `/invocations` is unprotected | Required by clients, ignored locally, any string works. Cloud uses `OLLAMA_API_KEY` |
| Endpoints | `/v1/chat/completions`, `/v1/completions`, `/v1/responses` (with retrieve and cancel), `/v1/embeddings`, audio | Same family plus `/v1/models`. `/v1/responses` is stateless: no `previous_response_id` or `conversation` |
| Structured output | `response_format` (`json_object`, `json_schema`, `structural_tag`, `text`), `structured_outputs` via `extra_body` | `response_format` and JSON mode on chat |
| Unsupported | `suffix` ignored, `user` ignored, `image_url.detail` | Chat: `tool_choice`, `logprobs`, `logit_bias`, `user`, `n`, image URLs (base64 only) |
| Quirks | Vendor params go in `extra_body`. Server applies the model's `generation_config.json` unless `--generation-config vllm` | No context-size param; set `PARAMETER num_ctx` in a Modelfile |

Sources: https://docs.vllm.ai/en/stable/serving/online_serving/openai_compatible_server.html ; https://docs.ollama.com/api/openai-compatibility

## 5. Licenses and dependencies
### 5.1 Python packages
Size columns are compressed dependency closure, Linux / Windows, in MB. Evidence: L = LICENSE file via GitHub API, P = PyPI metadata. "Pure" means a `py3-none-any` wheel, which runs on CPython 3.13 on both platforms.
| Package | Version (date) | License | CPython 3.13 win / linux | Closure MB | Notes |
|---|---|---|---|---|---|
| `mcp` | 2.3.0 (2026-10-02) | MIT (L, P) | Pure | 10 / 16 | Uses `httpx2`; adds `pywin32` on Windows |
| `ortools` | 9.15.6755 (2026-01-14) | Apache-2.0 (L, P) | cp313 wheels both | 60 / 48 | `protobuf<6.34,>=6.33.1`. No release since January |
| `markitdown` | 0.1.9 (2026-10-21) | MIT (L, P) | Pure | 58 / 42; `[all]` 134 / 115 | Base already pulls `magika` and `onnxruntime` |
| `docling` | 2.145.0 (2026-10-29) | MIT (L, P) | Pure; deps have wheels | 3396 / 342 | See below |
| `invoice2data` | 1.0.1 (2026-08-01) | MIT (L `LICENSE.md`, P) | cp313 wheels both | 3 / 1 | Small; PDF text extractors are separate |
| `elevenlabs` | 2.75.0 (2026-10-28) | MIT (P, `pyproject.toml`) | Pure | 6 / 6 | No LICENSE file at repo root; keep a copy of the metadata in the SBOM |
| `browser-use` | 0.13.14 (2026-10-26) | MIT (L) | Pure | 60 / 62 | See 5.2 |
| `langgraph` | 1.2.19 (2026-10-30) | MIT (L, P) | Pure | 14 / 8 | |
| `presidio-analyzer` | 2.2.366 (2026-10-27) | MIT (L, P) | Pure | 85 / 48 | Repo is `data-privacy-stack/presidio`. Default `en_core_web_lg` is a separate spaCy download, MIT, about 560 MB at 3.6.0 |
| `google-genai` | 2.34.0 (2026-10-29) | Apache-2.0 (P) | Pure | 11 / 10 | Requires Python >=3.11 |
| `anthropic` | 2.3.0 (2026-10-23) | Apache-2.0 (P) | Pure | 6 / 6 | Uses `httpx2`, not `httpx` |
| `openai` | 3.42.1 (2026-11-01) | Apache-2.0 (P) | Pure | 6 / 6 | Uses `httpx2`, not `httpx` |
| `twilio` | 9.12.0 (2026-10-12) | MIT (P) | Pure | 6 / 4 | |
| `stripe` | 16.1.0 (2026-10-29) | MIT (P) | Pure | 3 / 3 | |
| `pgserver` | 0.1.4 (2024-06-08) | Apache-2.0 (L) | NO: cp39-cp312 only, no sdist | n/a | Abandoned; do not use |
| `pixeltable-pgserver` | 0.7.1 (2026-10-29) | Apache-2.0 (P, repo badge) | cp313 wheels both | 25 / 30 | Maintained fork. Build notes reference PostgreSQL 18 and build pgvector. Recommended for local dev |
| `pgembed` | 0.2.0 (2026-03-18) | Apache-2.0 (README only; PyPI blank) | cp313 wheels both | 68 / 14 | PostgreSQL 17. Extensions are separate packages |
### 5.2 Weight and conflicts
**Docling.** `docling` is now a meta-package for `docling-slim[standard]`, which requires `torch` and `torchvision`. `torch` 2.15.0 wheel: 539 MB on Linux, 127 MB on Windows. The default Linux wheel adds `nvidia-cudnn-cu13` (537 MB), `nvidia-cublas` (439 MB), `nvidia-nccl-cu13` (305 MB), `triton` (286 MB). A slimmer route is `docling-slim[format-pdf,format-office,models-onnxruntime,feat-ocr-rapidocr-onnx]`, though that pulls `onnxruntime-gpu` on Linux and Windows (433 / 324 MB). Weights download separately and carry their own licenses: `docling-project/docling-layout-heron` Apache-2.0; `docling-project/docling-models` tagged `cdla-permissive-2.0` and `apache-2.0` with no per-file mapping; `ibm-granite/granite-docling-258M` Apache-2.0. Record each in the SBOM.
**Browser Use.** Non-extra pins include `anthropic<2.0.0`, `google-genai==1.65.0`, `mcp==2.1.1`, `openai<4.0.0`, `httpx==0.28.1`, `pydantic==2.13.5`, `requests==2.33.0`, `posthog==7.7.0`. These cannot coexist with `anthropic` 2.3.0, `google-genai` 2.34.0 or `mcp` 2.3.0. Run it as a separate process with its own environment, and review `posthog` telemetry. This comes from metadata, not an install test.
**Two HTTP stacks.** `anthropic`, `openai` and `mcp` use `httpx2`; `google-genai`, `elevenlabs` and `browser-use` use `httpx`. Shared transports, proxies and test mocks must cover both.
### 5.3 Sidecars and Activepieces
**VROOM** v1.15.0 (2026-03-12), BSD-2-Clause (L). Run `ghcr.io/vroom-project/vroom-docker:v1.15.0`; vroom-express on 3000; set `VROOM_ROUTER=osrm`; expects OSRM on 5000.
**OSRM** v26.11.0 (2026-11-01), BSD-2-Clause (L). Self-host a regional extract. Planet car profile needs about 123 GiB RAM to serve and 415 GiB to extract (2021 figures); both scale roughly with input size.
OSM data is ODbL, separate from OSRM's code license. Attribution to OpenStreetMap is required wherever routes or maps are displayed.
**Activepieces** (0.92.3-hotfix.2, 2026-10-28). Root `LICENSE`: `packages/ee/` and `packages/server/api/src/app/ee` fall under `packages/ee/LICENSE`, which allows production use only with an Activepieces Enterprise license and forbids distribution. Everything else is "MIT Expat". Pieces live under `packages/pieces/community/*` and publish to npm (for example `@activepieces/piece-shopify` 0.11.2, whose npm metadata has no `license` field, so the root LICENSE governs).
Reuse from Python: not realistic in-process. Pieces are TypeScript built on `@activepieces/pieces-framework`, so options are: run Activepieces as a sidecar and call flows or MCP endpoints; run a small Node worker; or hand-port logic with MIT notices kept. For Shopify, Stripe and ERPNext, write typed Python adapters, because Gluons needs tenant isolation, idempotency and approval gates that pieces do not provide. Pin sidecar configs so no `ee` path is built.
### 5.4 Restricted engines
**n8n** (2.45.7, `LICENSE.md`): Sustainable Use License limits use to "internal business purposes" and free non-commercial distribution; `.ee.` files need an enterprise license. Blueprint restriction HOLDS: n8n's support article says a commercial license is needed when people outside your organization can build or configure workflows, including through an AI agent acting for them, and even if customers never see the n8n UI. n8n's License FAQ marks collecting users' own third-party credentials inside n8n as not allowed under this license. Keep `reference_only`.
**Windmill** (v1.855.1, `LICENSE`): default AGPLv3. `python-client/`, `deno-client/`, `go-client/`, `powershell-client/` and OpenAPI files are Apache-2.0. `enterprise`-flag code is proprietary. Community Edition images and binaries may not be sold, resold, served as a managed service, modified or wrapped without agreement, and building a commercial feature on them needs a license. Blueprint restriction HOLDS. Keep `reference_only`.

Sources: https://pypi.org/pypi/{package}/json for each row ; https://github.com/modelcontextprotocol/python-sdk ; https://github.com/google/or-tools ; https://github.com/microsoft/markitdown ; https://github.com/docling-project/docling ; https://docling-project.github.io/docling/usage/model_catalog/ ; https://huggingface.co/docling-project/docling-layout-heron ; https://huggingface.co/docling-project/docling-models ; https://huggingface.co/ibm-granite/granite-docling-258M ; https://github.com/invoice-x/invoice2data ; https://github.com/elevenlabs/elevenlabs-python ; https://github.com/browser-use/browser-use ; https://github.com/langchain-ai/langgraph ; https://github.com/data-privacy-stack/presidio ; https://github.com/explosion/spacy-models/releases/tag/en_core_web_lg-3.6.0 ; https://github.com/orm011/pgserver ; https://github.com/pixeltable/pixeltable-pgserver ; https://github.com/Ladybug-Memory/pgembed ; https://github.com/VROOM-Project/vroom ; https://github.com/VROOM-Project/vroom-docker ; https://github.com/Project-OSRM/osrm-backend ; https://github.com/Project-OSRM/osrm-backend/wiki/Disk-and-Memory-Requirements ; https://www.openstreetmap.org/copyright ; https://github.com/activepieces/activepieces/blob/main/LICENSE ; https://github.com/activepieces/activepieces/blob/main/packages/ee/LICENSE ; https://registry.npmjs.org/@activepieces/piece-shopify/latest ; https://github.com/n8n-io/n8n/blob/master/LICENSE.md ; https://docs.n8n.io/privacy-and-security/sustainable-use-license ; https://docs.n8n.io/n8n-community-license/community-license/license-faq ; https://support.n8n.io/article/can-i-use-your-license-for-my-use-case ; https://github.com/windmill-labs/windmill/blob/main/LICENSE

UNVERIFIED (licenses and packages):
- Whether self-hosted Activepieces Community Edition exposes pieces as MCP servers (the README says contributed pieces become MCP servers; hosted vs self-hosted was not confirmed), and any supported way to run one piece outside Activepieces (none found).
- Licenses of NVIDIA packages pulled by Linux `torch`, of OCR model files, and exact Docling weight sizes. Check before bundling in an image.
- CPU-only `torch` 2.15.0 from `https://download.pytorch.org/whl/cpu` (usual route, not confirmed for this release), and the current OSRM image tag at `ghcr.io/project-osrm/osrm-backend` (source is a 2022 community comment).
- `pixeltable-pgserver` PostgreSQL version: 18 is inferred from a build comment, and the package is built for Pixeltable, so its API may change without notice.
- Closure sizes come from a simple resolver using latest versions, not an actual install, and ignore version conflicts.
