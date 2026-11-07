# External configuration and verification

Never paste a secret into chat. Put local values in the git-ignored `.env`, copied from `.env.example`. Use Secret Manager versions and scoped service accounts when hosted. Setting a value here never spends money by itself, except where the table says so.

Nothing below has been tested against the real provider. Provider-double tests prove Gluons' handling only.

## Worth doing first

Google is the cheapest real proof: one OAuth client gives a live Gmail test at no cost. A model key comes next, because evaluation runs unlock model-backed skills.

## What each service needs

| Service | Where to get it | Settings | Then verify |
|---|---|---|---|
| Google Gmail | Google Cloud Console, APIs & Services: enable Gmail API, add consent test users, create a Web OAuth client with the exact `/v1/oauth/gmail/callback` URL | `GLUONS_GOOGLE_*` | Consent, refresh, revoke, send with Sent readback |
| Gmail push (optional) | Pub/Sub topic with Gmail's publisher granted, plus an authenticated push subscription | `GLUONS_GMAIL_PUBSUB_TOPIC`, `GLUONS_GMAIL_PUSH_AUDIENCE`, `GLUONS_GMAIL_PUSH_SERVICE_ACCOUNT` | Watch renewal and a real notification. Polling works without this. |
| Models | Provider consoles, or Gemini through ADC on a Google Cloud project | `.env.example` model section | Live calls, then evaluation runs per skill |
| WhatsApp | Meta for Developers: app, WhatsApp product, Embedded Signup configuration. Business verification is needed for real use. | `GLUONS_META_APP_ID`, `GLUONS_META_APP_SECRET`, `GLUONS_META_CONFIG_ID`, `GLUONS_META_WEBHOOK_VERIFY_TOKEN` | Signup popup, signed webhook, service window, template send |
| Voice | ElevenLabs workspace with an agent, and a number it can receive on. Twilio sells no India numbers (ADR-012), so use Exotel or SIP there. | `GLUONS_ELEVENLABS_API_KEY`, `GLUONS_ELEVENLABS_WEBHOOK_SECRET`, `GLUONS_ELEVENLABS_TOOL_SECRET` | One real inbound call, a signed post-call event, a shared order |
| Shopify | Shopify Dev Dashboard: an app set up from `infra/shopify/shopify.app.toml.example`, a development store, and protected customer data access for order webhooks | `GLUONS_SHOPIFY_CLIENT_ID`, `GLUONS_SHOPIFY_CLIENT_SECRET`, `GLUONS_SHOPIFY_INSTALL_URL`, optional `GLUONS_SHOPIFY_PILOT_SHOPS` | Install from Shopify, claim in Gluons, draft, webhooks, uninstall, payment status |
| Carrier tracking | Shippo API token | `GLUONS_SHIPPO_API_TOKEN` | Only needed once a dispatch connector exists |
| ERPNext | An authorized sandbox and a restricted API user | `GLUONS_ERPNEXT_BASE_URL` plus the connection flow | Write and readback against real ERPNext |
| Email delivery | An SMTP provider and a verified domain | `GLUONS_SMTP_URL` | Deliverability. Local file mail proves nothing about this. |
| GitHub | Create the remote, then push | None in this app | CI runs for the first time. Deploy stays manual. |
| Docker | Open Docker Desktop once on this machine. It did not start unattended. | None | Build the three images and run `docker compose up`, then a real ERPNext for the order skill |
| Legal pages | A privacy policy and a data deletion instructions page on your domain | Links in the Google consent screen and the Meta app | Required for Google verification and Meta App Review |
| Google Cloud | Separate projects, billing, region, domains | `terraform.tfvars` per environment | **Chargeable.** Needs explicit owner approval before any apply. |

Prefer Application Default Credentials and scoped service accounts over downloaded keys.

## Approvals only a business or person can give

- **Google:** restricted-scope verification and an annual CASA assessment before public Gmail use (ADR-021).
- **Meta:** business verification, app review, and legal review of WhatsApp terms section 4.7, which bars AI-primary providers at Meta's discretion. Meta also charges for service messages after a free tier.
- **Shopify:** public app review. Until then, pilot shops only (ADR-015).
- **Professional review:** legal, tax, health, insurance, appraisal and title skills stay administrative drafts until a qualified person reviews them.
- **KYC, banking and store ownership** stay with the merchant and provider. Gluons records status and never completes them.

Do not connect production customer data until privacy, retention and these approvals are settled.

Sources: [OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server), [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes). Provider facts are in `docs/research/`.
