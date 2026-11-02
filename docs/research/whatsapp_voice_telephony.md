# WhatsApp, voice and telephony: provider research for Gluons

Researched 2026-11-02 against live vendor documentation: Meta WhatsApp Business Platform, ElevenLabs ElevenAgents, Twilio Voice.
- Facts come from official vendor pages or vendor SDK source on GitHub. Anything else is marked **UNVERIFIED**.
- Pages were read through an extraction tool, not raw HTML. Re-check an exact string against the linked page before a contract test depends on it. **Inference** marks a design conclusion drawn from documented facts, not a vendor statement.

## Blueprint conflicts

Wrong or outdated:
1. **Doc 13 s4 option A, "provision a new inbound Twilio number".** Wrong for India. Twilio states it offers no Local or Mobile India numbers. Its India guidelines list only toll-free +91800, callable only from Indian numbers and dialled `000 800 XXX XXXX`. Indian tenants need Exotel (a first-class ElevenLabs provider since 2026-06-01) or another SIP trunk. Option A still works for US and UK.
2. **Doc 13 s4 option B, carrier forwarding to a Twilio number.** Not viable for Indian tenants as designed. No Indian Twilio number can be bought, and at least Jio excludes international forwarding destinations, so the target must be an Indian DID. India's DoT also suspended USSD forwarding in 2024 (see 3.6).
3. **Doc 13 s3 step 2, "2026 unified onboarding".** One version behind. Unified onboarding is Embedded Signup v4 (Meta video, 2026-06-16). v5 shipped 2026-10-20 and v2/v3 were deprecated 2026-10-15. Build on v5, which replaces `waba_id` with an array, `messaging_account_ids`.
4. **Doc 13 s3 WABA wording.** The WABA has split into a WhatsApp account (WAAC) and a Messaging account, rolling out from 2026-09-23. Old IDs keep working. Tenant records need Messaging account ID, phone number ID and a nullable WAAC ID (see 1.3).
5. **Doc 13 s8, "no assumption of popups".** Conflicts with the Embedded Signup JavaScript flow, which opens a new window and posts results to its opener. The mobile question itself is partly settled: Meta documents mobile support (see 1.9).
6. **Doc 13 s3 step 4, "replay protection".** Meta's signature has no timestamp, so the only replay defence is deduplicating on message ID. Keep IDs at least 7 days, the retry horizon.
7. **Doc 02 step 5 and doc 07, "handle webhook retries".** ElevenLabs retries are off by default per webhook. Unless "Enable retries" is on, one failed delivery loses the event.
8. **Doc 02 step 3, "authenticated conversation-init webhook".** No signature is documented, only static request headers. A failed or timed-out webhook can stop the call starting, so this endpoint sits on the live call path (see 2.4).
9. **Doc 13 s4 verification call, PHONE-03.** `ForwardedFrom` depends on the caller's carrier and can be absent, so empty does not prove the call was not forwarded. With native Twilio import, Gluons also does not receive Twilio's voice webhook (**Inference**), so it must read `forwarded_from` from the Calls API (see 3.3, 3.6).
10. **WA-01 and doc 13 s3 demo option, "sandbox business number".** Meta has two different things. The Cloud API test number can send. The Embedded Signup sandbox account's number cannot send or receive (see 1.6).

Missing from the blueprint:
11. **Meta charges for replies.** From 2026-10-01 service messages are charged per message, with 1,000 free per phone number per month. Tech Provider clients must add their own payment method, or service messages stop. Add this as an onboarding state.
12. **Phone number is no longer a reliable customer key.** Webhook `from` and `wa_id` can be omitted for username users. Key customers on the BSUID (`user_id`) as well as phone (see 1.4).
13. **AI terms.** Section 4.7 of the WhatsApp terms bars AI providers whose AI is the primary, not incidental, functionality, as Meta decides at its sole discretion. Platform data may not train models (see 1.6).

Confirmed still correct: `register-call` has no transfers; verified caller IDs are outbound-only; post-call webhooks use HMAC; dedupe on `conversation_id` plus event type works; ElevenAgents bills per minute with LLM and telephony separate.

## 1. Meta WhatsApp Business Platform

### 1.1 Versions and prerequisites
- **Graph API:** latest is `v26.0`, introduced 2026-07-29 (v25.0 was 2026-02-18). Base URL `https://graph.facebook.com/v26.0`. Meta's samples still show v21.0 and v22.0 in places, so pin one config value.
- **Gluons role:** Tech Provider, unless it takes a credit line as a Solution Partner. Steps: business verification, then App Review with Advanced access to `whatsapp_business_messaging` and `whatsapp_business_management`, a screen recording for each, plus app icon, privacy policy and category. **Billing by role:** Meta bills a Tech Provider's clients directly. Tech Providers use business tokens only.
- **Onboarding cap:** 10 new customers in a rolling 7 days by default, 200 after Business Verification, App Review and Access Verification. Above that, apply as a Meta Business Partner.

| Embedded Signup version | Released | Status | `extras` |
|---|---|---|---|
| v2, v3 and public previews | 2023-2025 | Deprecated 2026-10-15; served as v4 | do not use |
| v4 | 2025-10-08 | Available | `{}` optional; coexistence by `featureType` |
| v5 | 2026-10-20 | Latest | `version: "v5"`; coexistence on by default |
- A request with no version, or an invalid one, is served as v4. `sessionInfoVersion` is ignored on v4 and v5.
- **UNVERIFIED:** Meta's pages disagree. The overview still says migrate to v4, the implementation sample shows `extras: { setup: {} }`, the v5 page shows only `version`, and the WhatsApp changelog's newest entry is 2026-05-12 with no v5 entry. Whether v5 needs `setup: {}` is not stated, so test both.
- Sources: https://developers.facebook.com/docs/graph-api/changelog , https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/versions , https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview , https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/get-started-for-tech-providers , https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/overview

### 1.2 Embedded Signup flow and server steps
- **Dashboard (Facebook Login for Business > Settings > Client OAuth settings):** turn on Client OAuth login, Web OAuth login, Enforce HTTPS, Embedded Browser OAuth Login, Strict Mode for redirect URIs, Login with the JavaScript SDK. List every hosting domain in Allowed domains and Valid OAuth redirect URIs (HTTPS only; results return only to listed domains).
- **Configuration:** create from template "WhatsApp Embedded Signup Configuration With 60 Expiration Token", or custom with the WhatsApp Embedded Signup variation. Keep its ID. Subscribe to the `account_update` webhook.
- **Client:** load `https://connect.facebook.net/en_US/sdk.js`, call `FB.init` with `appId`, `autoLogAppEvents: true`, `xfbml: true`, `version: "v26.0"`, then `FB.login(callback, options)` with options `{ "config_id": "<ID>", "response_type": "code", "override_default_response_type": true, "extras": { "version": "v5" } }`.
- **Two separate results** arrive and must be joined client-side: the callback and a `message` event. **Callback:** `response.authResponse.code`, only when the customer clicks Finish. **It lives 30 seconds**, so send it to the server at once.
- **`message` event on `window`:** accept only if `event.origin` ends with `facebook.com`, parse `event.data` as JSON, keep `type == "WA_EMBEDDED_SIGNUP"`.

```json
{ "type": "WA_EMBEDDED_SIGNUP", "event": "FINISH", "data": {
    "phone_number_id": "<ID>", "is_on_biz_app": false, "messaging_account_ids": ["<ID>"], "primary_waac_ids": [],
    "business_id": "<PORTFOLIO_ID>", "page_ids": [], "catalog_ids": [], "dataset_ids": [], "instagram_account_ids": [] } }
```
- `event` is `FINISH` when `is_on_biz_app` is false, `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING` when true. Cancel: `event: "CANCEL"`, `data.current_step`. Closing the popup on the final screen counts as success. Error: `event: "ERROR"`, `data.error_message`, `error_code`, `session_id`, `timestamp` (milliseconds, as a string).
- `primary_waac_ids` stays empty until Phase 2. v4 sends `waba_id` plus further events `FINISH_ONLY_WABA` and `FINISH_GRANT_ONLY_API_ACCESS`.

Server side, all server-to-server:
1. `GET https://graph.facebook.com/v26.0/oauth/access_token?client_id=<APP_ID>&client_secret=<APP_SECRET>&code=<CODE>` returns `access_token` (the business token), `token_type`, and `expires_in` only if it expires.
2. `POST /<MESSAGING_ACCOUNT_ID>/subscribed_apps` with `Authorization: Bearer <BUSINESS_TOKEN>` and no body returns `{"success": true}`. Repeat per account.
3. `POST /<PHONE_NUMBER_ID>/register` with `{"messaging_product":"whatsapp","pin":"<6 digits>"}`, optional `data_localization_region` (for example `IN`). Skip for coexistence numbers.
4. The client adds a payment method in WhatsApp Manager.

- **Register limits:** 10 requests per number per 72 hours, then error `133016` blocks for 72 hours. **PIN:** being deprecated by cohort, but keep sending a valid 6 digits. If 2FA is already on, the existing PIN is required. Re-registering a shared number succeeds without resetting the PIN.
- **Webhook override:** `POST /<WABA_ID>/subscribed_apps` with `override_callback_uri` (max 200 chars) and `verify_token`, or `POST /<PHONE_NUMBER_ID>` with `webhook_configuration`. Precedence: number, account, app default. Template and account-level fields always go to the app default.
- **UNVERIFIED:** the business token's lifetime and refresh path. The template name implies 60 days, while the tokens page says these tokens avoid future re-authentication. Store `expires_in` if returned and alert on failure.
- Sources: https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/implementation/ , https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/version-5 , https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-customers-as-a-tech-provider , https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/registration.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/override.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/access-tokens.md

### 1.3 Account model and unified onboarding
- **Unified onboarding** is Meta's description of Embedded Signup v4: one flow for WhatsApp, Messenger and Instagram products, Phone Number First ordering, consolidated asset screens. The exchange, subscribe and register steps in the current guides are unchanged.
- **Account split:** WAAC holds the phone number, identity, profile and catalogs and is always client-owned. Messaging account holds templates, billing and webhook subscriptions, one per partner. Templates are not shared between Messaging accounts.
- **Existing IDs:** `waba_id` values stay valid and now identify Messaging accounts. No re-onboarding.
- **Phase 1 (from 2026-09-23, all businesses by mid-October):** onboarding auto-creates a Messaging account per partner, "Add phone number later" is removed, and a client can add a new partner on an existing number.
- **Phase 2 (H1 2027):** `primary_waac_ids` populated, new endpoints, `whatsapp_account` webhook topic, `messaging_account_id` required on the newest Messages API when a token reaches several accounts on a number. **Phase 3 (H1 2028):** required on all versions, and phone number IDs in paths move to WAAC IDs. Plan the work in 2027.
- **Now:** migrate `paid_messaging_account_id` to `messaging_account_id` by 2026-12-31. Moving a number between Solution Partners is unsupported; the client shares the WAAC instead.
- **UNVERIFIED:** the Embedded Signup overview still labels this beta, while the account model page calls it staged general availability.
- Sources: https://developers.facebook.com/documentation/business-messaging/whatsapp/account-model-evolution.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/account-model-evolution/onboarding.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/account-model-evolution/messaging.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/version-4.md , https://developers.meta.com/resources/videos/unified-onboarding-whatsapp/

### 1.4 Webhooks
- **Handshake:** `GET <CALLBACK>?hub.mode=subscribe&hub.challenge=<RANDOM>&hub.verify_token=<YOURS>`. If `hub.verify_token` matches, return 200 with the `hub.challenge` value as the body, otherwise a 400-level status.
- **Signature:** header `X-Hub-Signature-256: sha256=<HEX>`. Compute HMAC-SHA256 with the **app secret** as key over the payload, compare with the value after `sha256=`. Inference: use raw request bytes before JSON parsing, and a constant-time compare. The key is per app, so all tenants share it.
- **Tenant routing:** `entry[].id` (Messaging account) and `value.metadata.phone_number_id`.
- **Retries:** non-200 is retried at once, then with decreasing frequency for up to 7 days, then dropped. Dedupe is your job. The generic Graph Webhooks page says 36 hours; design for the WhatsApp page's 7 days.
- **Limits:** payloads up to 3 MB, batches up to 1000 updates but not guaranteed. Valid TLS, no self-signed certificates. mTLS optional, per app. Meta recommends mTLS over IP allowlists. **Permissions:** `whatsapp_business_messaging` for `messages` and `calls` fields, `whatsapp_business_management` for the rest.

```json
{ "object": "whatsapp_business_account", "entry": [ { "id": "<MESSAGING_ACCOUNT_ID>", "changes": [ { "field": "messages", "value": {
    "messaging_product": "whatsapp",
    "metadata": { "display_phone_number": "<NUMBER>", "phone_number_id": "<ID>" },
    "contacts": [ { "profile": { "name": "<NAME>" }, "wa_id": "<PHONE>", "user_id": "<BSUID>" } ],
    "messages": [ { "from": "<PHONE>", "from_user_id": "<BSUID>", "id": "wamid.<...>", "timestamp": "<UNIX_STRING>", "type": "text", "text": { "body": "<TEXT>" } } ]
} } ] } ] }
```
- **Message ID:** `messages[].id` (`wamid.` prefix) is the dedupe key. **Statuses** come under the same field as `value.statuses[]`: `id`, `status` (`sent`, `delivered`, `read`, `played`, `failed`), `timestamp`, `recipient_id`, `recipient_user_id`, `pricing`, `errors[]` (`code`, `title`, `message`, `error_data.details`, `href`).
- **`pricing`:** `pricing_model` (`PMP`), `type` (`regular`, `free_customer_service`, `free_entry_point`, `free_group_customer_service`), `category` (`service`, `utility`, `marketing`, `authentication`, others). `billable` is deprecated. `conversation` is omitted from v24.0 unless in a free entry point window.
- **Partner events:** field `account_update`, `event` includes `PARTNER_ADDED` (`waba_info.waba_id`, `owner_business_id`, `solution_id`), `PARTNER_APP_INSTALLED`, `PARTNER_APP_UNINSTALLED`, `PARTNER_REMOVED`, `ACCOUNT_RESTRICTION`, `DISABLED_UPDATE`.
- **BSUID:** `<ISO2>.<up to 128 alphanumerics>`, for example `US.13491208655302741918`. Unique per portfolio and user, and it changes if the user changes number. In webhooks since April 2026.
- **Omitted phone:** `from` and `wa_id` appear only if you exchanged a message or call with the user in the last 30 days on that number, or they are in your contact book. To send without a phone, omit `to` and set `recipient` to the BSUID. `user_changed_number` and `user_changed_user_id` system messages carry `previous_user_id`.
- **UNVERIFIED:** the response deadline is not documented, so acknowledge fast and process asynchronously. Older Meta docs described signing an escaped-Unicode form of the payload; current pages do not, and raw bytes avoid the issue.
- Sources: https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview.md , https://developers.facebook.com/docs/graph-api/webhooks/getting-started , https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/text.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/status.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/account_update.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids/

### 1.5 Sending, templates, media
`POST https://graph.facebook.com/v26.0/<PHONE_NUMBER_ID>/messages` with `Content-Type: application/json` and `Authorization: Bearer <BUSINESS_TOKEN>`.

```json
{ "messaging_product": "whatsapp", "recipient_type": "individual", "to": "<E164>", "type": "text", "text": { "body": "<max 4096 chars>" } }
{ "messaging_product": "whatsapp", "recipient_type": "individual", "to": "<E164>", "type": "template", "template": { "name": "<NAME>", "language": { "code": "<CODE>" },
  "components": [ { "type": "body", "parameters": [ { "type": "text", "text": "<VALUE>" } ] } ] } }
```
- **Response:** `messaging_product`, `contacts[].input`, `contacts[].wa_id`, `messages[].id`, and `messages[].message_status` for paced templates. It means accepted, not delivered.
- **Customer service window:** opens for 24 hours on a user message or call, and resets on each one. Inside it any type is allowed. Outside it only approved templates, else error `131047`. **TTL:** 30 days, 10 minutes for authentication templates. Order is not guaranteed; to enforce it, wait for `delivered`.
- **Throughput:** 80 mps per number by default, automatic upgrade to 1,000 (`131057` during upgrade). Errors: `130429` throughput, `131056` pair rate, `131026` undeliverable, `131049` marketing limit, `131042` payment problem, `133010` not registered, `368` restricted. **Messaging limits:** new portfolios reach 250 unique recipients outside a window per rolling 24 hours, then 2,000, 10,000, 100,000, unlimited. New portfolios cap at 2 numbers, 20 once verified or at the 2,000 limit.
- **Templates:** categories `authentication`, `marketing`, `utility`. Names lowercase alphanumeric and underscores, max 512. Review is automatic, up to 24 hours. Only `APPROVED` sends. Updates arrive on `message_template_status_update`. Limit 250 per Messaging account, up to 6,000 for verified portfolios with an approved display name, 100 creations per hour.
- **Media:** take the ID from the webhook, `GET /<MEDIA_ID>` with a bearer token (optional `phone_number_id`) for `url`, `mime_type`, `sha256`, `file_size`, `id`, then GET `url` with the same bearer header. URLs expire after **5 minutes**. Webhook media IDs expire after **7 days**, uploads after 30. A failed download returns 404, so refetch the URL. Size limits: audio and video 16 MB, images 5 MB, documents 100 MB.
- **UNVERIFIED:** exact `message_status` values. The pair limit of one message per 6 seconds came from a search excerpt of Meta's docs and is not on the throughput page.
- Sources: https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/media.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/throughput.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits , https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes.md

### 1.6 Policy and test numbers
- **Opt-in:** you need the person's number and opt-in permission that names the business and states they are opting in. It can be general rather than WhatsApp-specific, and the method is the business's choice. The page does not say a customer-initiated message counts.
- **Opt-out and automation:** requests must be honoured on or off WhatsApp. Automation is allowed inside the window, but a prompt, clear escalation path is required, such as human transfer or a phone number (policy updated 2026-09-23).
- **AI (terms section 4.7, modified 2026-09-23):** AI providers may not use the platform where AI is the primary rather than incidental functionality, and Meta decides. An AI provider may act as your Solution Provider, but platform data may not build or train models, apart from narrow exclusive fine-tuning. **Inference:** position Gluons as a business's own customer-service automation, and require no-training terms from LLM and voice vendors.
- **Cloud API test number:** Get Started auto-creates a test WhatsApp account, Messaging account and phone number, with relaxed limits and no payment method for templates. Replying to the `hello_world` test message opens the window. The temporary user token expires within hours.
- **Embedded Signup sandbox:** claimed at App Dashboard > WhatsApp > Quickstart > Testing Integrations. It exercises onboarding and token exchange, but its number cannot send or receive. Sandbox accounts expire, and deletion is permanent.
- **UNVERIFIED:** the recipient cap on test numbers. Older Meta docs said 5; current pages give no number.
- Sources: https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in.md , https://whatsappbusiness.com/policy/ , https://www.facebook.com/legal/Meta-Terms-for-WhatsApp-Business-Platform , https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/about-the-platform , https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview.md

### 1.7 Personal app, Business app, Business Platform, coexistence

| | WhatsApp (personal) | WhatsApp Business app | Business Platform (Cloud API) |
|---|---|---|---|
| API access | None | None unless coexistence | Yes |
| Onboarding | Must be deleted from WhatsApp before registering | Embedded Signup coexistence, app 2.24.17+ | Embedded Signup |
- **Coexistence:** the same number runs in the app and Cloud API. Opt-in on v4 with `featureType: "whatsapp_business_app_onboarding"`, on by default in v5. Opt out on v5 with `features: [{ "name": "whatsapp_business_app_onboarding", "enabled": false }]`. Without opt-in on v4, an app number is rejected as ineligible.
- **Flow:** the owner scans a QR code in the Business app. Skip `/register`. The existing account converts to a Messaging account and keeps its ID, with no per-partner account. Messages sent from the app stay free; API messages follow Cloud API pricing.
- **Sync:** `POST /<PHONE_NUMBER_ID>/smb_app_data` with `{"sync_type":"smb_app_state_sync"}`, then `{"sync_type":"history"}`, each once, within **24 hours** of onboarding. History covers 180 days and excludes groups. Error `2593109` means the business declined. **Webhooks to add:** `history`, `smb_app_state_sync`, `smb_message_echoes`, `account_update`.
- **Limits:** fixed 20 mps. Companion devices are unlinked (Windows and WearOS unsupported). In 1:1 chats, disappearing and view-once messages and live location are disabled. Broadcast lists go read-only. Groups, calls and catalog are not on Cloud API. Only user messages open a window. `/deregister` is blocked; the business disconnects in the app. **Inference:** on a coexistence number, voice stays in the app, so voice AI needs a separate telephony number.
- **UNVERIFIED:** supported countries for coexistence, and how a personal number moves to the Business app.
- Sources: https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users/ , https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/phone-numbers.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/version-4-public-preview.md

### 1.8 Pricing
- Per delivered message since 2025-07-01, by category and the recipient's market. **Changed 2026-10-01:** service and in-window utility messages are charged. Each number gets 1,000 free delivered service messages per month, no rollover. Service rates equal utility and authentication rates by market. In-window utility `pricing.type` moves from `free_customer_service` to `regular`.
- **Free entry point:** Click to WhatsApp ad replies are free for up to 7 days. **Volume tiers:** utility and authentication only, by portfolio.
- **India:** Messaging accounts must move to INR by 2026-12-31, and non-INR accounts stop delivering from 2027-01-01. Meta scopes this to eligible Solution Providers and direct businesses, so confirm the effect on Tech Provider clients.
- Sources: https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages.md

### 1.9 Mobile browsers
- **Documented:** Meta calls Embedded Signup compatible with desktop and mobile, and its changelog of 2022-10-06 says it supports mobile web browsers with an adapted layout. Desktop fallback is no longer the default assumption.
- **Still test:** the JS flow opens a new window and posts to its opener, so it needs a real user tap and same-browser return. Test iOS Safari, Android Chrome, and in-app browsers including links opened from ChatGPT.
- **Hosted Embedded Signup** avoids your own JS. It links to a Meta page, IDs arrive on `account_update` `PARTNER_ADDED`, and the business token comes from the System User Access Tokens API. It cannot be customised, and mobile return behaviour is not documented.
- **UNVERIFIED:** iOS Safari popup blocking, in-app webviews, and the Coexistence QR step when the only device is the phone running the Business app.
- Sources: https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview.md , https://developers.facebook.com/documentation/business-messaging/whatsapp/changelog , https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/hosted-es.md

## 2. ElevenLabs ElevenAgents

### 2.1 API basics and agents
- **Base:** `https://api.elevenlabs.io`. Residency hosts: `api.us.elevenlabs.io`, `api.eu.residency.elevenlabs.io`, `api.in.residency.elevenlabs.io`, `api.sg.residency.elevenlabs.io`. Auth header `xi-api-key`.
- **Python SDK:** `elevenlabs` **2.75.0** on PyPI, **MIT**, Python `>=3.8,<4.0`. Releases are near weekly, so pin it.
- **Create:** `POST /v1/convai/agents/create` with `conversation_config` (required), `platform_settings`, `workflow`, `name`, `tags`. Returns `agent_id`. **Update:** `PATCH /v1/convai/agents/{agent_id}`, optional query `branch_id`, all body fields optional including `version_description`. All agents are versioned.
- **Key paths:** `conversation_config.agent.prompt.prompt`, `.llm`, `.tool_ids`, `.built_in_tools`, `.knowledge_base`; `conversation_config.agent.first_message`, `.language`; `conversation_config.tts.voice_id`, `.model_id`.
- Sources: https://elevenlabs.io/docs/api-reference/agents/create.md , https://elevenlabs.io/docs/api-reference/agents/update.md , https://pypi.org/pypi/elevenlabs/json , https://elevenlabs.io/docs/changelog

### 2.2 Server tools and dynamic variables
Create with `POST /v1/convai/tools`, then add the returned ID to `conversation_config.agent.prompt.tool_ids`.

```json
{ "tool_config": { "type": "webhook", "name": "<NAME>", "description": "<WHEN TO CALL>", "response_timeout_secs": 20,
    "api_schema": { "url": "https://<HOST>/voice/tools/{tool}", "method": "POST", "content_type": "application/json",
      "request_headers": { "X-Gluons-Key": { "secret_id": "<SECRET_ID>" }, "X-Gluons-Tenant": { "variable_name": "secret__tenant_token" } },
      "path_params_schema": {}, "query_params_schema": {}, "request_body_schema": {} } } }
```
- **Header value types:** plain string, `{ "secret_id" }`, `{ "variable_name" }`, `{ "env_var_label" }`. `api_schema.auth_connection` attaches OAuth2, Basic or Bearer connections.
- **Secrets:** `POST /v1/convai/secrets` with `{ "type": "new", "name": "...", "value": "..." }` returns `{ "type": "stored", "secret_id": "...", "name": "..." }`. A secret in use cannot be deleted.
- **Other fields:** `response_timeout_secs` default 20, range 5 to 300. `execution_mode`: `immediate`, `post_tool_speech`, `async`. `assignments` writes response values into variables by `value_path`.
- **Tool calls are not signed.** No signature is documented, so authenticate by secret header and treat tool input as untrusted.
- **Variables:** syntax `{{name}}`, case-sensitive, string, number or boolean. `system__` is reserved, including `system__caller_id`, `system__called_number`, `system__call_sid`, `system__conversation_id`.
- **`secret__` prefix:** keeps a value out of the LLM and limits it to headers. The conversations API redacts it, but **post-call webhooks receive the original**, so treat those payloads as secret-bearing. **Placeholders:** set `dynamic_variable_placeholders`, because previews do not run initiation webhooks.
- Sources: https://elevenlabs.io/docs/api-reference/tools/create.md , https://elevenlabs.io/docs/eleven-agents/customization/tools/webhook-tools.md , https://elevenlabs.io/docs/api-reference/workspace/secrets/create , https://elevenlabs.io/docs/eleven-agents/customization/personalization/dynamic-variables.md

### 2.3 Knowledge base
- **Create:** `POST /v1/convai/knowledge-base/text` (`text`, `name`), `/url` (`url`, `name`, `enable_auto_sync`), `/file` (multipart `file`). Files max 20 MB: PDF, TXT, DOCX, HTML, EPUB, Markdown.
- **Attach:** update `conversation_config.agent.prompt.knowledge_base` with `[{ "type": "text|url|file|folder", "name": "...", "id": "...", "usage_mode": "auto|prompt" }]`.
- **RAG:** `POST /v1/convai/knowledge-base/{id}/rag-index` with `model` (the docs use `e5_mistral_7b_instruct`), poll until `SUCCEEDED` or `FAILED`, and enable RAG on the agent. Documents under 500 bytes go straight into the prompt. Documents can be reused across agents, so keep tenant isolation explicit. A document in use cannot be deleted without force.
- Sources: https://elevenlabs.io/docs/eleven-agents/customization/knowledge-base/manage-documents.md , https://elevenlabs.io/docs/api-reference/knowledge-base/create-from-text , https://elevenlabs.io/docs/api-reference/knowledge-base/compute-rag-index , https://elevenlabs.io/docs/eleven-agents/customization/knowledge-base/rag

### 2.4 Conversation initiation webhook
- **When:** new inbound Twilio voice, Exotel, SIP trunk, WhatsApp and Twilio SMS conversations without initiation data. Outbound only if the request omitted `conversation_initiation_client_data`. Not for widget, SDK or Preview sessions.
- **URL:** workspace via `PATCH /v1/convai/settings` with `conversation_initiation_client_data_webhook: { url, request_headers }`, or per agent via `platform_settings.workspace_overrides.conversation_initiation_client_data_webhook`.
- **Enable:** `platform_settings.overrides.enable_conversation_initiation_client_data_from_webhook: true`. Allow each override field under `platform_settings.overrides.conversation_config_override`.
- **Request (POST):** `{ "caller_id": "<E164>", "called_number": "<E164>", "agent_id": "<ID>", "call_sid": "<CA...>", "conversation_id": "<ID>" }`. SIP may add `call_id`, `sip_headers`. On WhatsApp, `caller_id` and `called_number` hold the user ID and phone number ID, and `call_sid` is empty.

```json
{ "type": "conversation_initiation_client_data", "dynamic_variables": { "business_name": "<VALUE>" },
  "conversation_config_override": { "agent": { "prompt": { "prompt": "<OPTIONAL>" }, "first_message": "<OPTIONAL>", "language": "<OPTIONAL>" }, "tts": { "voice_id": "<OPTIONAL>" } } }
```
- **Response rules:** must return **every** custom dynamic variable the agent defines. `system__` values cannot be set. Max 256 KB. **Failure:** Twilio's dial period is used to fetch the data, so a slow reply delays the answer. A failed or timed-out webhook can prevent the conversation starting.
- **Authentication:** static headers only (HTTPS, secrets manager). No signature is documented. **UNVERIFIED:** the timeout value is not published. Load-test it and keep the handler free of slow dependencies.
- Sources: https://elevenlabs.io/docs/eleven-agents/customization/personalization.md , https://elevenlabs.io/docs/eleven-agents/customization/personalization/twilio-personalization.md , https://raw.githubusercontent.com/elevenlabs/elevenlabs-python/main/src/elevenlabs/types/conversation_initiation_client_data_config_input.py , https://raw.githubusercontent.com/elevenlabs/elevenlabs-python/main/src/elevenlabs/types/agent_workspace_overrides_input.py

### 2.5 Post-call webhooks
- **Register:** `POST /v1/workspace/webhooks` with `{ "settings": { "auth_type": "hmac", "name": "...", "webhook_url": "https://...", "request_headers": {} } }` returns `webhook_id`, `webhook_secret`.
- **Attach:** workspace via `PATCH /v1/convai/settings` (`webhooks`), per agent via `platform_settings.workspace_overrides.webhooks`: `post_call_webhook_id`, `events`, `transcript_format`, `exclude_transcript`. `send_audio` is deprecated.
- **`events`:** `transcript`, `audio`, `call_initiation_failure`, `answering_machine_detection`, `unredacted_transcript`, `unredacted_audio`. **Payload `type`:** `post_call_transcription`, `post_call_audio`, `call_initiation_failure`, plus `post_call_transcription_otel` for OpenTelemetry format. Top level: `type`, `event_timestamp`, `data`.
- **Signature:** header `ElevenLabs-Signature: t=<unix>,v0=<hex>`. `v0` is hex HMAC-SHA256 with the webhook secret over `<t>.<raw_body>`. The SDK rejects timestamps older than **30 minutes**, and does not reject future ones.
- **SDK helper:** `elevenlabs.webhooks.construct_event(rawBody=<str>, sig_header=<header>, secret=<secret>)` returns a dict and raises `BadRequestError`. It compares with `!=`, so a hand-rolled check should use `hmac.compare_digest`. A non-numeric `t` raises `ValueError`.
- **Delivery:** return HTTP 200. Retries are **off by default**. When on, up to 5 retries at about 30 s, 2 min, 8 min and 30 min plus jitter, on 5xx, 429, 408, connection errors and timeouts. Other 4xx are not retried. Max 100 queued retries per webhook. **Audio:** chunked, 5-minute timeout. Over 50 MiB is delivered once. Queued audio retries cap at 400 MiB.
- **Auto-disable:** 10 or more consecutive failures when the last success was over 7 days ago or never. Admins get an email, and re-enabling is manual. **Dedupe:** retries are identical to the original, so key on `conversation_id` plus `type`.
- **`data` identity:** `agent_id`, `agent_name`, `conversation_id`, `status`, `user_id`, `branch_id`, `version_id`, `environment`, `has_audio`, `conversation_initiation_client_data`. **`data.transcript[]`:** `role`, `message`, `time_in_call_secs`, `tool_calls[]`, `tool_results[]`.
- **`data.metadata`:** `start_time_unix_secs`, `call_duration_secs`, `cost`, `charging`, `termination_reason`, `feedback`, `deletion_settings`, `authorization_method`. **`data.analysis`:** `call_successful`, `transcript_summary`, `evaluation_criteria_results`, `data_collection_results`.
- **`call_initiation_failure` `data`:** `agent_id`, `conversation_id`, `failure_reason` (`busy`, `no-answer`, `unknown`), `metadata` (`type` `twilio` or `sip`, plus `body`).
- **Call fields:** `GET /v1/convai/conversations/{conversation_id}` documents `metadata.phone_call` with `type` (`twilio`, `sip_trunking`, `exotel`), `direction`, `agent_number`, `external_number`, `call_sid`, `stream_sid`; `metadata.cost_fiat` (USD); `charging.llm_price`, `llm_charge`, `call_charge`, `platform_charge`, `dev_discount`.
- **UNVERIFIED:** whether `metadata.phone_call` and `cost_fiat` appear in the webhook, since its example omits them. Capture `call_sid` from the initiation webhook, or fetch the conversation. The unit of `metadata.cost` is not stated.
- Sources: https://elevenlabs.io/docs/eleven-agents/workflows/post-call-webhooks.md , https://elevenlabs.io/docs/eleven-api/resources/webhooks , https://raw.githubusercontent.com/elevenlabs/elevenlabs-python/main/src/elevenlabs/webhooks_custom.py , https://elevenlabs.io/docs/api-reference/webhooks/create.md , https://elevenlabs.io/docs/api-reference/conversations/get.md , https://elevenlabs.io/docs/eleven-agents/customization/integrations/custom_channel

### 2.6 Phone numbers, transfers, SIP, outbound
- **Import:** `POST /v1/convai/phone-numbers`. Twilio body requires `phone_number`, `label`, `sid` (`AC...` or `SK...`), `token`; optional `provider` (`twilio`), `agent_id`, `region_config`, `enable_sms`, `account_auth_token` (required for API Key imports). Returns `phone_number_id`. API keys are recommended. **Assign later:** `PATCH /v1/convai/phone-numbers/{phone_number_id}` with `agent_id`. `supports_inbound` and `supports_outbound` are deprecated.

| | Native Twilio import | `POST /v1/convai/twilio/register-call` |
|---|---|---|
| Inbound routing | ElevenLabs configures the number | Your server gets Twilio's webhook and returns the TwiML string |
| Transfers | Yes | **None**, because ElevenLabs has no Twilio credentials |
| Audio setup | Automatic | Set TTS and input to mu-law 8000 Hz |
| Sees `ForwardedFrom` | No (**Inference**) | Yes (**Inference**) |
- **`register-call` body:** `agent_id`, `from_number`, `to_number`, `direction` (default `inbound`), `conversation_initiation_client_data`. Numbers do not appear in the dashboard.
- **`transfer_to_number`:** in `conversation_config.agent.prompt.built_in_tools`, `params.system_tool_type: "transfer_to_number"`, `params.transfers[]` of `{ "transfer_destination": { "type": "phone", "phone_number": "<E164>" }, "condition": "<WHEN>", "transfer_type": "conference" }`. **`transfer_type`:** `conference` (default, Twilio or SIP trunk), `blind` (native Twilio only, JSON editor), `sip_refer` (SIP trunk with REFER). Only SIP REFER accepts `{ "type": "sip_uri", "sip_uri": "..." }`. `post_dial_digits` and the warm `agent_message` need native Twilio. The LLM supplies `reason`, `transfer_number`, `client_message`, `agent_message`.
- **`transfer_to_agent`:** `params.system_tool_type: "transfer_to_agent"`, `params.transfers[]` with `agent_id`, `condition`, `delay_ms`, `transfer_message`, `enable_transferred_agent_first_message`. The LLM supplies `agent_number` (zero-indexed). Realtime models transfer only to Realtime models.
- **SIP inbound:** `sip:<E164>@sip.rtc.elevenlabs.io:5060;transport=tcp` or `:5061;transport=tls` (TLS 1.2+). UDP is experimental. Always put an identifier before `@`. Codecs G.722, PCMU, PCMA. Digest auth recommended; ACL needs IP allowlisting. **SIP other:** static IP endpoints `sip-static.rtc.elevenlabs.io` and `sip-static.rtc.<region>.residency.elevenlabs.io` are Enterprise only. Import body: `phone_number`, `label`, `provider: "sip_trunk"`, `inbound_trunk_config`, `outbound_trunk_config` (`address` required). Twilio Elastic SIP is listed as compatible.
- **Outbound:** `POST /v1/convai/twilio/outbound-call` with `agent_id`, `agent_phone_number_id`, `to_number`, optional `conversation_initiation_client_data`, `telephony_call_config` (`ringing_timeout_secs` default 60, `twilio_call_recording_enabled`, `twilio_machine_detection`). Returns `success`, `message`, `conversation_id`, `callSid`. SIP: `POST /v1/convai/sip-trunk/outbound-call` returns `sip_call_id`. Exotel: `POST /v1/convai/exotel/outbound-call`.
- **India path, Exotel:** import with Account SID, API key and token, region `api.exotel.com` or `api.in.exotel.com`, Voicebot applet ID, E.164 number. Inbound needs a Voicebot applet on the ExoPhone. Transfers need a Connect applet straight after it, at `https://api.elevenlabs.io/v1/convai/exotel/connect-applet`. India residency uses `wss://api.in.residency.elevenlabs.io/v1/convai/conversation/exotel`. Enablement takes 1 to 2 business days.
- **Regional Twilio routing:** default `us1`; also `ie1`, `au1`, `br1`, `jp1`, `sg1`. Must match the Twilio Edge setting, and non-US regions need a regional API key. A wrong region makes transfers fail.
- **ElevenLabs native WhatsApp** also exists, but numbers on another provider cannot be imported, so do not plan on running it beside Gluons' Meta app (**Inference**).
- **UNVERIFIED:** which Twilio number fields native import rewrites, and the Exotel API import body.
- Sources: https://elevenlabs.io/docs/api-reference/phone-numbers/create.md , https://elevenlabs.io/docs/api-reference/phone-numbers/update.md , https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/native-integration.md , https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/register-call.md , https://elevenlabs.io/docs/api-reference/twilio/register-call.md , https://elevenlabs.io/docs/api-reference/twilio/outbound-call.md , https://elevenlabs.io/docs/api-reference/sip-trunk/outbound-call.md , https://elevenlabs.io/docs/eleven-agents/customization/tools/system-tools/transfer-to-number.md , https://elevenlabs.io/docs/eleven-agents/customization/tools/system-tools/agent-transfer.md , https://elevenlabs.io/docs/eleven-agents/phone-numbers/sip-trunking.md , https://elevenlabs.io/docs/eleven-agents/phone-numbers/telephony/exotel , https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/regional-routing.md , https://elevenlabs.io/docs/agents-platform/whatsapp

### 2.7 Pricing, retention, recording, consent
- **Pricing:** `$0.080` per call minute on every plan, burst `$0.160` per minute (up to 3x the concurrency limit), `$0.003` per text message. Free: 15 minutes, 4 concurrent calls. Business: $990, 12,375 minutes, 40 concurrent. Silence over 10 seconds gets a 95% discount. **Billed separately:** LLM at cost, deducted from credits. Twilio or the SIP provider bills you directly, and ElevenLabs adds no telephony fee.
- **Retention:** default 2 years. `platform_settings.privacy.retention_days` takes a positive number, `-1` unlimited, `0` scheduled deletion. Related: `delete_transcript_and_pii`, `delete_audio`, `apply_to_existing_conversations`, `conversation_history_redaction`. **Recording:** `platform_settings.privacy.record_voice`, on by default. Twilio recording is separate: `twilio_call_recording_enabled`, default false.
- **Zero retention:** `platform_settings.privacy.zero_retention_mode: true`, for select enterprise customers. No recordings or PII transcripts are stored, so post-call webhooks are the only record. MCP is unavailable.
- **Consent:** no built-in feature. ElevenLabs requires telling users they are talking to AI, before the interaction, and that calls may be recorded and shared. A verbal notice at call start is an accepted method. The deployer is solely responsible for local law.
- Sources: https://elevenlabs.io/pricing/agents , https://elevenlabs.io/docs/help-center/product/eleven-agents/how-much-does-eleven-agents-cost.md , https://elevenlabs.io/docs/eleven-agents/customization/privacy/retention.md , https://elevenlabs.io/docs/eleven-agents/customization/privacy/audio-saving.md , https://elevenlabs.io/docs/eleven-agents/customization/privacy/zrm.md , https://elevenlabs.io/docs/eleven-api/resources/zero-retention-mode.md , https://elevenlabs.io/docs/eleven-agents/legal/disclosure-requirement.md , https://raw.githubusercontent.com/elevenlabs/elevenlabs-python/main/src/elevenlabs/types/privacy_config_input.py

## 3. Twilio Voice

### 3.1 Numbers API and test credentials
- **Search:** `GET https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/AvailablePhoneNumbers/{CountryCode}/Local.json`, also `/TollFree.json` and `/Mobile.json` where offered. Filters: `AreaCode`, `Contains`, `InRegion`, `InPostalCode`, `InLocality`, `VoiceEnabled`, `SmsEnabled`, `ExcludeAllAddressRequired`, `ExcludeLocalAddressRequired`, `ExcludeForeignAddressRequired`, `PageSize`. **Result fields:** `phone_number`, `friendly_name`, `address_requirements` (`none`, `any`, `local`, `foreign`), `beta`, `capabilities`.
- **Purchase:** `POST .../IncomingPhoneNumbers.json` (form-encoded) with `PhoneNumber` or `AreaCode` (US and Canada only), optional `VoiceUrl`, `VoiceMethod`, `StatusCallback`, `AddressSid`, `BundleSid`, `IdentitySid`, `TrunkSid`, `FriendlyName`. Returns `sid` (`PN...`). `TrunkSid` overrides voice URLs.
- **Test credentials:** only `POST IncomingPhoneNumbers`, `POST Messages`, `POST Calls` and Lookup; anything else returns 403. No charges, no real numbers, `Url` not fetched, TwiML not run, **no webhooks**. They cannot test an inbound path.
- **Purchase magic numbers:** `+15005550006` valid, `+15005550000` unavailable (21422), `+15005550001` invalid (21421). `AreaCode=533` none available (21452). **Call magic numbers:** `From` `+15005550006` valid, `+15005550001` invalid (21212), others unverified (21210). `To` `+15005550001` invalid (21217), `+15005550002` cannot route (21214), `+15005550003` no international permission (21215), `+15005550004` blocked (21216).
- Sources: https://www.twilio.com/docs/phone-numbers/api/availablephonenumberlocal-resource , https://www.twilio.com/docs/phone-numbers/api/availablephonenumber-resource , https://www.twilio.com/docs/phone-numbers/api/incomingphonenumber-resource , https://www.twilio.com/docs/iam/test-credentials

### 3.2 Webhook signature validation
- **Form POST:** take the exact URL Twilio called, including query string. Sort POST parameters by name, append each name then value with no delimiter. Compute HMAC-SHA1 with the primary **Auth Token** as key, Base64-encode, compare to `X-Twilio-Signature`. For repeated names the SDK sorts and de-duplicates values.
- **JSON body:** Twilio appends `bodySHA256=<hex SHA-256 of the raw body>` to the URL. Do not treat JSON fields as parameters. The signature covers the URL alone. Check the hash, then the signature.
- **Port:** Twilio drops the port for voice callbacks over HTTPS. The Python SDK tries with and without. **URL handling:** do not decode or re-encode it. Behind Cloud Run or a proxy, rebuild the public URL rather than the internal one (**Inference**).
- **Implementation:** Twilio says to use its SDK validator, not your own: `twilio.request_validator.RequestValidator(auth_token).validate(url, params_or_raw_body, signature)`. The parameter set evolves, so never allowlist fields before validating.
- Sources: https://www.twilio.com/docs/usage/webhooks/webhooks-security , https://www.twilio.com/docs/usage/security , https://raw.githubusercontent.com/twilio/twilio-python/main/twilio/request_validator.py

### 3.3 Verifying a call happened
- **Single call:** `GET https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/Calls/{CallSid}.json`. **List:** `GET .../Calls.json`. Case-sensitive filters `To`, `From`, `ParentCallSid`, `Status`, `StartTime`, `StartTime<`, `StartTime>`, `EndTime`, `EndTime<`, `EndTime>`. `PageSize` default 50, max 1000.
- **Fields:** `sid`, `parent_call_sid`, `from`, `to`, `status`, `direction`, `start_time`, `end_time`, `duration`, `forwarded_from`, `caller_name`, `phone_number_sid`, `trunk_sid`, `answered_by`, `price`, `price_unit`. **Values:** `status`: `queued`, `ringing`, `in-progress`, `completed`, `busy`, `failed`, `no-answer`, `canceled`. `direction`: `inbound`, `outbound-api`, `outbound-dial`, `trunking-terminating`, `trunking-originating`.
- **Consistency:** eventually consistent, so retry before concluding. Records are retrievable for 13 months, older via Bulk Export.
- **Voice Insights:** `GET https://insights.twilio.com/v1/Voice/{CallSid}/Summary` needs Advanced Features. Insights data covers the last 30 days.
- **Recipe (Inference):** take `call_sid` from the initiation webhook, fetch the call with the tenant's credentials, and require `direction=inbound`, `to` equal to the tenant's number, and a terminal `status` with `duration`. Then match `conversation_id` from the post-call webhook.
- Sources: https://www.twilio.com/docs/voice/api/call-resource , https://www.twilio.com/docs/voice/voice-insights/api/call/call-summary-resource , https://www.twilio.com/docs/voice/voice-insights

### 3.4 Regulatory requirements
- **Mechanism:** a Regulatory Bundle groups end-user data, documents and addresses. API `https://numbers.twilio.com/v2/RegulatoryCompliance/Bundles`, fields `FriendlyName`, `Email`, `StatusCallback`, `IsoCountry`, `NumberType` (`local`, `mobile`, `national`, `toll-free`), `EndUserType` (`individual`, `business`), `RegulationSid`. Submit with `Status=pending-review`. Statuses include `draft`, `pending-review`, `in-review`, `twilio-approved`, `twilio-rejected`. **Timing:** Twilio aims for 72 hours; some regions can take weeks. Buying a regulated number without a valid `BundleSid` fails with error `21649`.
- **India:** no Local or Mobile numbers. Toll-free +91800: individuals and businesses, address outside India, no documents listed. Domestic callers only, dialled `000 800 XXX XXXX`. Outbound to India must use non-Indian numbers, and voice OTP is not permitted. Commercial calls need recipient consent or TRAI may treat them as unsolicited.
- **UK:** Local and National need a valid UK address. Mobile and Toll-Free accept a valid address anywhere. Individuals supply government ID, plus address proof for Local and National. Businesses list no documents. The contact phone must be a real mobile, not a CPaaS number.
- **US:** Twilio's changelog scopes regulated numbers to most numbers outside the US and Canada. STIR/SHAKEN via Trust Hub is optional and helps outbound answer rates.
- **UNVERIFIED:** current US rules, because Twilio's US regulatory guideline URL returned 404. Query the Regulations resource by country, number type and end-user type before coding each market.
- Sources: https://help.twilio.com/articles/22550748886043-Does-Twilio-offer-Local-or-Mobile-United-Arab-Emirates-UAE-or-India-phone-numbers , https://help.twilio.com/articles/40728263317915-India-Toll-Free-Voice-Number-Not-Working-and-Not-Receiving-Calls , https://www.twilio.com/en-us/guidelines/in/regulatory , https://www.twilio.com/en-us/guidelines/in/voice , https://www.twilio.com/en-us/guidelines/gb/regulatory , https://www.twilio.com/en-us/guidelines/us/voice , https://www.twilio.com/docs/phone-numbers/regulatory/api/bundles , https://www.twilio.com/docs/phone-numbers/regulatory/faq , https://www.twilio.com/docs/phone-numbers/regulatory/getting-started/console-create-new-bundle , https://www.twilio.com/en-us/changelog/regulated-number-bundle-provisioning-requirement

### 3.5 Verified caller ID and SIP Domains
- **Purchased (`IncomingPhoneNumbers`):** inbound and outbound. **Verified caller ID (`OutgoingCallerIds`):** outbound caller ID only, cannot receive calls through Twilio or be assigned to an ElevenLabs agent.
- **Add caller ID:** `POST /2010-04-01/Accounts/{AccountSid}/OutgoingCallerIds.json` with `PhoneNumber`, optional `FriendlyName`, `CallDelay` (0 to 60), `Extension`, `StatusCallback`. Returns a 6-digit `validation_code` and `call_sid`. Twilio calls the number and the person enters the code, in English only. The callback gets `VerificationStatus`, `OutgoingCallerIdSid`. Trial accounts can only call verified numbers.
- **SIP Domain:** `POST https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/SIP/Domains.json`, `DomainName` ending in `sip.twilio.com`. A request to `sip:user@<name>.sip.twilio.com` authenticates, then Twilio fetches TwiML from `voice_url`. `voice_fallback_url`, `voice_status_callback_url`, `SipRegistration` also exist. Map an IP access control list or credential list, or traffic is blocked.
- **Inference:** a SIP Domain brings PBX or softphone traffic into TwiML. Elastic SIP Trunking, attached to a number by `TrunkSid`, is the separate product ElevenLabs lists as compatible.
- Sources: https://www.twilio.com/docs/voice/api/outgoing-caller-ids , https://www.twilio.com/docs/voice/api/verifying-caller-ids-scale , https://support.twilio.com/hc/en-us/articles/223180048-How-to-Add-and-Remove-a-Verified-Phone-Number-or-Caller-ID-with-Twilio , https://www.twilio.com/docs/voice/sip/api/sip-domain-resource

### 3.6 Carrier forwarding
Twilio documents no carrier dial codes; its forwarding articles cover forwarding from a Twilio number. Codes come from carriers:

| Carrier | Forward all | Conditional | Off | Source |
|---|---|---|---|---|
| Verizon | `*72` + number | `*71` + number (no answer) | `*73` | Carrier FAQ |
| AT&T home phone | `*72`, number, `#` | | `*73#` | Carrier support |
| T-Mobile | `**21*1<number>#` | | `##21#` | Carrier page |
| Airtel | `21*<number>#` as rendered | `67*` busy, `61*` no answer, `62*` unreachable | `##21#`, `##67#`, `##61#`, `##62#` | Carrier blog |
| Vi | `**21*<number>#` | `*67*` busy, `*61*` no answer | `##21#` | Carrier blog |
| Jio | MyJio app, handset menu | | App | Carrier FAQ |
- **UNVERIFIED, GSM codes:** 21 unconditional, 67 busy, 61 no reply, 62 not reachable appear in a 3GPP change request and a secondary summary; TS 22.030 itself was not read. Leading `*` count varies by carrier, and the Airtel and Vi text may have lost asterisks in rendering.
- **Jio limits:** free with an active plan, but excludes BSNL numbers, your own Jio number and **international numbers**. **UNVERIFIED:** a search excerpt of a Jio FAQ, no longer reachable, listed `*401*<number>`, `*403`, `*405`, `*402`.
- **DoT suspension, UNVERIFIED:** an order dated 2024-03-28 stopped USSD forwarding (`*401#`) from 2024-04-15 until further notice. Seen only through search excerpts, as the DoT PDF now returns 404. Current status and effect on `**21*` codes are unknown. Prefer carrier apps and handset menus in Indian instructions.
- **Twilio side:** webhook parameter `ForwardedFrom` and Calls field `forwarded_from` are set only for forwarded calls, and the value depends on the caller's carrier. Not all carriers send it. A matching value is proof of forwarding; an empty value proves nothing. **Lookup call forwarding** (`Fields=call_forwarding`) is no substitute: private beta, major UK carriers, unconditional only.
- **Inference, verification design:** a known test caller dials the business number. Accept the route only if Twilio shows an inbound call to the tenant's number, in the time window, from that caller, recording `forwarded_from` when present. This keeps `needs_manual_carrier_step` honest. Whether a carrier preserves the original `From` on a forwarded call is **UNVERIFIED**, so test per carrier.
- **Sequencing:** Meta advises disabling call forwarding and IVR on fixed lines for voice OTP, so complete WhatsApp number verification before turning forwarding on.
- Sources: https://www.twilio.com/docs/voice/twiml , https://www.twilio.com/docs/voice/api/call-resource , https://www.twilio.com/docs/lookup/v2-api/call-forwarding , https://help.twilio.com/articles/223179908-Setting-Up-Call-Forwarding , https://www.verizon.com/support/call-forwarding-faqs/ , https://www.att.com/support/article/home-phone/KM1052856/ , https://www.t-mobile.com/support/plans-features/self-service-short-codes/ , https://www.airtel.in/blog/airtel-app/call-forwarding-in-airtel/ , https://www.myvi.in/blog/how-to-activate-and-deactivate-call-forwarding-code-on-vi , https://www.jio.com/help/faq/mobile/services/hd-voice/how-can-i-activate-call-forwarding-service/ , https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/phone-numbers.md
