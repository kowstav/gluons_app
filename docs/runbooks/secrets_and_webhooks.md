# Secrets and webhooks

Secrets live in Secret Manager when hosted, and in the git-ignored `.env` locally. Never put one in chat, a ticket, a tfvars file or a log. To check that a value exists, ask Secret Manager for its metadata, not its payload.

## Rotating a secret

Add a new secret version, then redeploy so services read it. Disable the old version only after the new one works.

| Secret | What rotation affects |
|---|---|
| `GLUONS_VAULT_KEK_B64` (local) or the KMS key (hosted) | Every stored provider token. Rotate through KMS. Never replace the local key without re-encrypting, or stored tokens become unreadable. |
| `GLUONS_SIGNING_KEY_B64` | Signed policies, approval decisions and Gmail intake grants stop verifying. Plan a re-signing pass with owners. Do not rotate casually. |
| `GLUONS_META_APP_SECRET` | WhatsApp webhook signatures. Rotate in Meta first, then deploy at once, because Meta signs with the new value straight away. |
| `GLUONS_META_WEBHOOK_VERIFY_TOKEN` | Only the initial webhook handshake. |
| `GLUONS_ELEVENLABS_WEBHOOK_SECRET` | Post-call signatures. A rotation can miss a call, and `POST /v1/phone-routes/{id}/reconcile/{conversation_id}` recovers it. |
| `GLUONS_ELEVENLABS_TOOL_SECRET` | Every route's derived tool header. Rebind each route with `gluons phone-bind` and update the header in ElevenLabs. |
| `GLUONS_SHOPIFY_CLIENT_SECRET` | OAuth and webhook signatures for every store. |
| Google OAuth client secret | New consents and token refresh. Existing refresh tokens survive if the client ID stays the same. |

## Webhook endpoints

Every one rejects an unsigned body before touching tenant data.

| Provider | Path | Proof |
|---|---|---|
| Meta | `/v1/webhooks/whatsapp` | `X-Hub-Signature-256` HMAC over the raw body |
| ElevenLabs | `/v1/webhooks/elevenlabs` | `ElevenLabs-Signature` HMAC with a timestamp window |
| ElevenLabs tools | `/v1/voice/{route_id}/...` | Per-route `X-Gluons-Voice-Secret`, plus a read back of the conversation |
| Gmail | `/v1/webhooks/gmail` | Google-signed OIDC token for the configured audience and service account |
| Shopify | `/v1/commerce/shopify/webhooks/{connection_id}` | `X-Shopify-Hmac-SHA256` over the raw body |

A signature proves who sent an event, not that its content is safe. Inbound content stays untrusted data.

ElevenLabs tool calls and initiation webhooks carry static secrets, not signatures (ADR-014). Meta and ElevenLabs do not retry forever, so a long outage needs reconciliation from the provider's records.
