# How owners connect Gmail, Shopify and WhatsApp

An owner never gives Gluons a password for another service. They sign in on the provider's own page, and the provider gives Gluons a scoped token. That token is encrypted in the vault and never reaches a model, a log, a URL or an assistant.

Connecting an account grants nothing. Before any automation can use it, the owner must:

1. Confirm the displayed account is the right one.
2. Sign a policy for each automation, naming exactly what it may do. The provider's grant is a ceiling on that policy, never a substitute.

Every connection is started and finished by a signed-in owner or admin in Gluons Web. An assistant can create a setup link, but only the owner's browser can complete it.

## Signing in to Gluons itself

| What | How |
|---|---|
| Account | Email and password, with the email address verified first |
| Second step | Authenticator app, with single-use recovery codes |
| Wrong passwords or codes | Lock the account for 15 minutes after 8 failures. With two-step on, signing in again does not reset the count. |
| Floods from one network | Sign-in, registration, password reset and OAuth endpoints are limited per address |
| Sessions | Server-side and revocable, with an HttpOnly cookie and a CSRF token on every change |
| ChatGPT, Claude and other assistants | OAuth with PKCE. Each install can be revoked on its own, and some actions stay on Gluons Web only. |

## Gmail

The owner chooses **Gmail** on the Connections page, reviews the requested access, and continues to Google's own consent screen.

| Step | What happens |
|---|---|
| Consent | Google's page, with PKCE and a one-time state bound to that browser session. It expires after the connection window. |
| Access requested | Read mail (`gmail.readonly`). Sending (`gmail.send`) only if the owner ticks it. Plus the account's verified email. |
| Back in Gluons | The owner confirms the displayed address. Intake from chosen labels needs a separate signed grant. |
| Afterwards | Gluons renews access in the background with the refresh token. |
| Disconnect | Gluons revokes the token at Google, and the automations using it pause |

**What changes for you:** Google expires refresh tokens after 7 days while the consent screen is in **Testing**, so live use needs the screen published. Reading mail is a restricted scope: public launch needs Google's verification and a yearly CASA security assessment (ADR-021). A Google Workspace admin may also have to allow the app.

## Shopify

There are two ways in. Both end with a store bound to exactly one Gluons business, which the owner then confirms.

**1. From Shopify, the normal path.** Shopify requires this for any public or unlisted app.

1. The merchant installs Gluons from the App Store listing or an install link. Shopify opens the Gluons app URL with a signed request.
2. Gluons checks the signature and sends the merchant to Shopify's approval screen. That browser gets a 10-minute cookie, and only that browser can finish the install, so nobody can lure an owner into completing an install of someone else's store.
3. The merchant approves, and Shopify returns to Gluons. The token is sealed and held for 30 minutes. The browser gets a one-time, HttpOnly claim cookie.
4. The merchant signs in to Gluons, or creates an account, and chooses **Connect to** their business. The claim works once, only in that browser.

**2. A pilot shop typed into Gluons.** This is only for shops an operator approved in `GLUONS_SHOPIFY_PILOT_SHOPS`, such as a development store or a custom-distribution pilot. The owner starts in Gluons, approves in Shopify, and comes back to Gluons.

| Topic | Behaviour |
|---|---|
| Tokens | An offline token that expires after 1 hour, with a refresh token that rotates on every use and lasts 90 days. Refreshes for one store are serialized, because a refresh retires the old token. |
| Opening the app again | Does not issue a new token while the store is connected, which would retire the live one |
| One store, one business | A second business cannot claim a store that is already connected |
| Uninstall in Shopify | The `app/uninstalled` webhook closes the connection, pauses its automations and emails the owner |
| Privacy webhooks | `customers/data_request` tells the owner what Gluons holds. `customers/redact` deletes that customer's order evidence. `shop/redact` erases store data, but only once the app has been uninstalled. |
| Disconnect in Gluons | Stops Gluons' side at once. Removing the app from the store is done in Shopify admin, because Shopify treats uninstall as irreversible. |

**What changes for you:** create the app in the Shopify Dev Dashboard and set it up from `infra/shopify/shopify.app.toml.example`. Request protected customer data access before subscribing to order webhooks. Use a development store to test. A public or unlisted app needs Shopify's review, and a custom-distribution app serves one merchant (ADR-015).

## WhatsApp Business

The owner chooses **Connect WhatsApp Business** and finishes Meta's own Embedded Signup window, version 5. They pick or create their business and number there.

| Step | What happens |
|---|---|
| Popup | Meta's window, opened from a direct tap. Gluons never sees the Meta login. |
| Code | Meta returns a code that lives 30 seconds. Gluons exchanges it, subscribes the app to the account's webhooks and registers the number. A number shared with the WhatsApp Business app (coexistence) is not re-registered. |
| Checks | A malformed answer is refused before any call. One number serves one business, and a failed step leaves no connection. |
| Back in Gluons | The owner confirms the displayed number, then adds a payment method in WhatsApp Manager. |
| Personal WhatsApp | Not supported. Gluons only connects the Business Platform. |
| Expiry | Meta may issue a business token that expires, and no API renews it. Gluons warns the owner 7 days ahead and marks the connection when it lapses. |
| Disconnect | Stops Gluons' side at once. Removing Gluons from the business is done in Meta Business settings. |

Without the Meta app configured, a local operator can still bind an already onboarded number with `gluons whatsapp-bind`, which reads the token from the environment.

**What changes for you:** a Meta app with the WhatsApp product and an Embedded Signup configuration. You also need business verification, App Review for advanced access, and the app's webhook URL (`/v1/webhooks/whatsapp`) and verify token in the App Dashboard. Have someone review WhatsApp terms section 4.7 before launch. Meta charges for messages after a free tier.

## When access lapses

A health check runs every 6 hours:

- **Gmail:** renews access and confirms it is still the same account.
- **Shopify:** renews a refresh token within 14 days of lapsing.
- **WhatsApp:** warns before expiry.

A refused grant marks the connection **Needs reauth**, pauses dependent automations at their next action, and sends the owner one urgent email. Reconnecting the same account keeps its history.

## Provider settings to register

| Provider | Setting | Value |
|---|---|---|
| Google | Authorized redirect URI | `<API URL>/v1/oauth/gmail/callback` |
| Google | Gmail push endpoint (optional) | `<API URL>/v1/webhooks/gmail` |
| Shopify | App URL | `<API URL>/v1/commerce/shopify/install` |
| Shopify | Allowed redirection URL | `<API URL>/v1/commerce/shopify/callback` |
| Shopify | Webhooks and compliance webhooks | `<API URL>/v1/commerce/shopify/webhooks` |
| Meta | Webhook callback URL | `<API URL>/v1/webhooks/whatsapp` |
| ElevenLabs | Post-call webhook | `<API URL>/v1/webhooks/elevenlabs` |

Google and Meta app review also ask for a privacy policy and a data deletion instructions page on your domain. Those are legal texts for you to approve.
