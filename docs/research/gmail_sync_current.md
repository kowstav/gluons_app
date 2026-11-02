# Gmail ingestion implementation and verification — 2026-11-02

This module implements Gluons' side of Gmail/PubSub contracts. Tests use provider
doubles with real PostgreSQL storage; no real Gmail mailbox or Pub/Sub subscription
has been verified here.

Verification: `GLUONS_TEST_CLUSTER=gmail-tests python -m pytest
apps/api/tests/test_gmail_sync.py -q` passed **18 tests** on real embedded
PostgreSQL with provider doubles. Ruff passed for the service, routes and test
module. The suite checks page checkpointing/404 resync, duplicate shared order
runs, encrypted attachments and partial-order prevention, signed read-grant
tamper/expiry/cancellation/tenant fences, shadow no-write behavior, owner-only
watch setup, JWT audience/issuer/email/expiry checks, notification routing/dedupe,
intake status and streamed response limits.

## Checked official contracts

- Gmail [push notifications](https://developers.google.com/workspace/gmail/api/guides/push):
  `users.watch` takes a Pub/Sub topic and labels, returns history/expiry, must be
  renewed at least every seven days; daily renewal is recommended. Topic publisher
  access for `gmail-api-push@system.gserviceaccount.com` is an operator prerequisite.
- Gmail [synchronization](https://developers.google.com/workspace/gmail/api/guides/sync)
  and [history.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list):
  history may expire before a week; a 404 requires full resynchronization.
- Pub/Sub [authenticated push](https://docs.cloud.google.com/pubsub/docs/authenticate-push-subscriptions):
  Google signs the bearer JWT. Verify signature, audience, issuer, expiry and the
  configured push service account email plus `email_verified`, independently of
  the unauthenticated JSON payload.

## Implemented boundaries

`POST /v1/connections/{id}/gmail/intake` requires an owner browser session and
explicit label IDs. It signs a separate, expiring mailbox-read/attachment grant
bound to tenant, connection and automation. Connecting Gmail alone grants no
intake. Automation action policies remain independently mandatory for ERP writes
or email sending. Only the shared multichannel order skill currently consumes it.

Shadow intake uses read-only API polling. It cannot create a provider watch.
An owner may explicitly request watch setup after supervised/live activation and
configuration of the topic, audience, push service account and exact subscription.
Maintenance only renews watches for current, valid signed grants on active
supervised/live automations. Paused/cancelled/disconnected/emergency-stopped tasks
receive no new ingestion. Expired/revoked watches are never presented as healthy.

`POST /v1/webhooks/gmail` validates RS256 against Google's fixed JWKS endpoint;
the selected key cannot come from the request. It checks audience, allowed issuer,
expiry, issued-at, subject and exact verified service-account email. The body has a
16 KiB limit. Exact database subscription/account mappings resolve tenants;
request-body tenant IDs provide no authority. Pub/Sub message IDs are deduplicated
per registered mapping. Notification history IDs never become trusted cursors.

Reconciliation processes a bounded page per job. It transactionally enqueues
message work before advancing history/page state. Full resync captures a provider
history snapshot before listing pages, then replays history from that snapshot so
messages arriving during listing are caught. A stale cursor resets to full resync.
An interrupted page retains its prior cursor. Duplicate jobs/events do not create
duplicate shared order runs.

Message/attachment reads have byte, MIME-depth, part-count, attachment-count and
decoded-size limits. Attachments are envelope-encrypted in the existing vault with
purpose `gmail_attachment`, referenced by tenant-scoped Artifact rows; raw bytes
and credential references do not leave public endpoints. This is bounded DB-backed
storage, not a claim that Cloud Storage evidence retention has been deployed.
Supported attachments use the existing local, bounded document extractor and
remain untrusted data. Unsupported, oversized or unsafe attachments create a
durable owner notification and `needs_review` message; no partial order run starts.

## Worker integration

Register these handlers in the shared worker:

- `gmail.sync`: `sync_connection(job.tenant_id, job.payload['connection_id'])`
- `gmail.ingest`: `ingest_message(job.tenant_id, job.payload['connection_id'], job.payload['message_id'])`
- `gmail.maintain`: `maintain()`; schedule every 300 seconds for polling and daily
  watch renewal. Database queue retries retain failed work and eventually expose
  the existing dead-letter incident.

Provider JWTs and mailbox OAuth credentials never appear in inbox payloads, logs,
MCP results or public connection serializers. An existing provider watch may
continue notifying until expiry after local disable; local routing and new work
are fenced immediately. Delivery to a real Google subscription remains a required
provider configuration and staging test.
