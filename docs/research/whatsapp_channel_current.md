# WhatsApp Business channel — implementation and verification, 2026-11-02

The implementation uses official Graph REST endpoints behind `GuardedClient`; it
does not install the archived Node SDK or scrape personal WhatsApp. Real Meta
onboarding, messages, delivery and billing have **not** been tested.

## Documentation evidence and limitation

Current Meta documentation URLs for Embedded Signup, Graph webhooks and message
sending were requested during this implementation but returned HTTP 429 or were
inaccessible. The prior research file contains newer signup/account-model notes;
those have not been silently treated as verified runnable behavior here.

Meta's own archived SDK documentation confirms the GET challenge and raw-body
HMAC-SHA256 signature contract: [webhook start](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/).
Meta's API collection documents Business Platform resources, but is not a
substitute for testing a currently approved app:
[official Meta collection](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api).
Recheck [Embedded Signup](https://developers.facebook.com/docs/whatsapp/embedded-signup/)
and [Graph webhook setup](https://developers.facebook.com/docs/graph-api/webhooks/getting-started)
with the actual approved app before enabling its automated flow.

## Supported boundary

- `POST /v1/whatsapp/sessions` requires an authenticated owner/admin browser and
  CSRF. The setup is expiring, owner/session bound and cancellable using the shared
  connection-session cancellation endpoint. Personal accounts are refused.
- Automated Embedded Signup is disabled. A local operator may call
  `gluons.whatsapp.service.bind_from_environment` only after external approved
  onboarding. `GLUONS_WHATSAPP_ACCESS_TOKEN` is read from the local process/Secret
  Manager, never REST, MCP or chat. Existing Meta app ID/secret and verification
  token settings are required. Successful checks against `/me/permissions`, the
  selected account's `/phone_numbers` and `/subscribed_apps` are mandatory; if a
  token/account type does not support those checks, linking fails closed.
- The selected Graph version remains `GLUONS_META_GRAPH_VERSION` (currently the
  existing repository default `v24.0`). This is **not a claim that v24.0 is the
  latest version** or that it is approved for this application. Pin the app's
  actually tested supported version before a real deployment.
- New links show the provider identity and require separate owner confirmation.
  Linking or confirmation never signs an automation action policy.
- `GET/POST /v1/webhooks/whatsapp` verify the challenge/signature. POST bounds bytes
  and object counts, validates provider timestamps, resolves tenant from a confirmed
  phone/account mapping, and transactionally enqueues a deduplicated inbox record.
  Dedupe records are retained indefinitely, exceeding Meta's seven-day retry window.
- Worker handler `whatsapp.process` calls `process_event(tenant_id,inbox_id)`.
  Inbound messages and thread windows are persisted. Disconnect quarantines queued
  events. Enabled order automations require matching messaging connection and
  `whatsapp_ingest_enabled=true`; supervised/live also require an active signed
  `whatsapp.message.ingest` grant for that connection. Shadow runs use the shared
  order engine with zero provider writes. Incoming text never changes permission.
- Media remains a pointer until an authorized tenant read. Downloads require a
  received-message association, fixed provider hosts, no redirects, permitted MIME,
  streaming byte limit and provider hash verification. Captions and attachments are
  untrusted data; no automatic document or image interpretation is claimed.
- `whatsapp.message.send` is a registered R2 gateway action. Its connector independently
  checks the current confirmed account, exactly one known customer, stored WhatsApp
  opt-in, suppression, and a signed-inbound-derived 24-hour window. Outside that
  window only an exact provider-approved template/language with matching bounded
  body parameters is permitted. Rich media/button templates are unsupported.
- POST acceptance is unverified. A persisted opaque callback binds signed delivery
  events to the exact approved message/recipient. `verify` waits for delivered/read
  evidence and never issues a second POST. Missing outcomes are ambiguous and require
  human reconciliation. Meta does not expose message-content readback: the receipt
  attests delivery status/recipient, not an independent readback of message content.

## Remaining release gates

Approved app distribution/Embedded Signup, verified business entitlement, actual
Graph permission behavior with the selected token type/version, consent/retention
and terms review, test contacts and real signed delivery events, channel-aware
order confirmation graph integration, and cost reconciliation with actual billing.
No cost is fabricated from message acceptance or provider-double tests.

## Local verification evidence

`tests/test_whatsapp.py`: **18 passed**, with real isolated PostgreSQL and explicit
Graph HTTP provider doubles. Coverage includes raw signature/challenge, durable
dedupe, tenant/phone/account mapping, stale events, owner/session binding, personal
account refusal, revoked/reconfirmed identity, send-only read refusal, suppression
and service-window gates, template approval, bounded media and media authority,
shared shadow order intake, and approved send through the gateway with repeated
verification waits and exactly one POST before signed delivered status. Ruff passed
for the module, connector, routes and tests. This is not live Meta verification.
