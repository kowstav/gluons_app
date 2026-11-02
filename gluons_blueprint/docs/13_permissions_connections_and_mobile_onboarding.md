# 13 — Mandatory permission, account-connection, and telephone-routing system

**Status:** Product and engineering specification, **not implemented**. **Build gate P0** for any live customer demo. Updated 2026-11-02.

## 0. What the previous blueprint missed

The original files mentioned OAuth, tenant RBAC, per-action policy checks, and voice integration, but did **not** provide a sufficiently actionable end-to-end implementation for a business owner to connect Gmail or WhatsApp or route calls, especially on mobile. Treat this document as overriding vague authorization language in earlier files. The entire product must follow this contract.

**Non-negotiable:** Saying `yes` in a conversation never by itself grants provider access, configures carrier routing, or authorizes an irreversible business action. Four distinct authorizations exist:

1. **Assistant → Gluons:** owner signs in to Gluons and connects ChatGPT/Claude/other assistant to its **Gluons** account using OAuth/MCP (where host capabilities allow).
2. **Gluons → provider:** owner/admin authorizes Google, Meta, ERP, etc. through the provider's own approved web consent/admin process; tokens never enter chat.
3. **Owner → automation:** separate, explicit policy allows **specific** task actions on **specific** resources with approval thresholds, budget, and trial mode. Connecting Gmail is *not* permission to mail customers.
4. **Network → AI phone agent:** an assigned number, supported telephony connection, porting, or carrier-configured forwarding delivers inbound phone calls to ElevenLabs. A chat consent cannot intercept calls to a handset.

## 1. Host capabilities and mobile reality

OpenAI's documentation as of 2026-11-02 says **custom MCP connections in ChatGPT are web-only** and full MCP write actions have plan/workspace restrictions. Do not assume that an arbitrary SMB using ChatGPT on iOS/Android can discover, install, or execute Gluons custom write tools inside the ChatGPT mobile app. This is a **blocking product dependency, not a UI detail**.

Therefore deliver *three* supported entry paths behind the same Gluons account:

- **Path A: Supported ChatGPT web connection:** host's OAuth interaction → Gluons account → provider authorization URL → owner browser → provider consent → Gluons status polling. Feature-detect tool availability and action confirmations. No assumption that ChatGPT's UI permits arbitrary HTML authorization controls.
- **Path B: Gluons-owned responsive web application / PWA:** authoritative mobile-first onboarding, authorization, approvals, pause/cancel, status, cost, and fallback assistant conversation. Works on Android/iPhone in mobile browser regardless of ChatGPT custom-MCP support. Optional normal home-screen PWA install.
- **Path C: Claude/other clients:** integrate only supported APIs and authenticated tools. Treat host support and deployment-specific UX as compatibility tests, not a universal promise.

**Don't promise universal mobile ChatGPT plugin operation before host validation.** If the user starts on ChatGPT mobile without compatible tools, provide a truthful explanation and a signed, expiring `https://app.gluons.example/connect/...` link; the owner opens it through the phone's browser. The assistant cannot self-initiate arbitrary provider access without a real host tool or authenticated Gluons session. After browser consent the owner can resume where supported, or continue in the Gluons PWA.

## 2. Owner journey: Gmail order processing

1. Owner asks to process inbox orders. Agent calls `discover_skill` then `create_automation_plan`, returns expected behavior, exact Gmail scopes, ERP prerequisites, costs, and side effects. `status=draft`.
2. `start_connection(provider=gmail, account_hint?, requested_capabilities=[read_order_messages, send_order_confirmations])` authenticates the Gluons tenant; authorization service creates a **one-time connection session** with expiry (e.g. 10 min), opaque handle, CSRF state, PKCE values, tenant/user binding, return target. **No provider tokens in assistant responses or URLs.**
3. Return `authorization_url`, `session_id`, `expires_at`, `status=requires_external_authorization` in MCP. Owner opens official Google OAuth flow in a supported mobile/system browser from Gluons' HTTPS setup screen. User chooses Google account and can decline.
4. Google redirects to the **exact registered Gluons backend callback**. Validate state, expiry, nonce where applicable, PKCE, user/tenant session, callback host, scopes and provider subject. Exchange code server-side; store encrypted tokens separate from UI/MCP. Never accept an arbitrary `return_url`.
5. Backend checks the authorized Gmail identity and scopes, stores `connection.status=connected`, expires, provider user ID, last check. A missing or insufficient scope produces `needs_additional_permission` rather than implicit escalation. Show the actual email account.
6. Configure Gmail `users.watch` / Pub/Sub, cursor handling, periodic watch renewal, full resynchronization on stale history, and webhook verification. Gmail requires `gmail.readonly` (restricted) for message body and `gmail.send` (sensitive) for replies in this design. Obtain the **minimum** necessary scopes. Google OAuth verification/security assessment may apply prior to broad public launch; for demos use authorized test users and comply with testing rules.
7. Owner configures **business action policy**: which messages qualify, which sender accounts are trusted, allowed recipients, ERP order limits, send vs draft, escalation, cost ceilings; approve plan separately. Start in replay/shadow (no external writes/sends). Then allow `supervised` or `active` under explicitly stored owner consent.
8. Runtime: source event → verify → dedupe → parse → validate → draft → approval if required → external order write → read-after-write verify → **then** send permitted confirmation (or only receipt acknowledgement) → log provider message ID. No confirmation of accepted order before ERP verification.
9. `disconnect` immediately prevents new actions; revoke at Google where supported and delete local credential reference, stop Gmail watch if authorized, mark automations `waiting_external`, quarantine pending external messages, audit. Explain irreversible emails cannot be unsent.

**Edge cases:** Google admin blocks scopes; token revoked/expired; OAuth app in Testing expiring refresh grants; Gmail history cursor 404; messages duplicated; wrong Google account selected; draft-only permission; owner switches accounts; browser doesn't return to ChatGPT.

## 3. Owner journey: WhatsApp order processing

1. Owner selects `whatsapp_business` channel. Distinguish personal WhatsApp versus Business App versus Business Platform, and assess supported eligibility. Never claim Gluons can silently read personal WhatsApp chats.
2. On Gluons' HTTPS onboarding page open the current Meta-approved Embedded Signup/business onboarding. Meta's account and signup models evolve (including 2026 unified onboarding); version the integration and verify current business/WABA/phone/messaging-account requirements. Owner may need business admin access, phone verification, and Business Platform/Meta approval; **mobile compatibility must be tested**, with desktop fallback.
3. Callback/status flow stores verified external business account/phone number IDs and granted permissions, not raw browser tokens. Complete Meta server-side account token handoff as the current official flow requires, and verify subscription to incoming messages.
4. Verify webhook challenge and `X-Hub-Signature-256` using provider app secret, replay protection, event ID dedupe, and multi-tenant phone/account routing. Encrypt sensitive data.
5. Require a separate action policy (`read_messages`, `draft_replies`, `send_replies`, `submit_orders`), customer opt-in/message-policy compliance, rate/cost limits, template approvals and human escalation. Do not confuse a provider platform connection with permission to auto-reply to customers.
6. Customer-service window and approved template logic enforced by **deterministic gateway**, not the LLM. Handle partial multichannel orders and cross-channel duplicates. Show the exact WhatsApp business number to the owner before activation.
7. On disconnect: disable webhook routing/tenant mapping, revoke available grants, block further sends, mark live tasks `waiting_external` and preserve legally required audit records subject to retention policies.

**Demo option:** Meta-approved test/sandbox business number and explicitly opted-in test contacts; if live onboarding is unavailable, show WhatsApp as `integration pending` rather than claiming it is connected.

## 4. Owner journey: telephone receptionist

A chatbot/ChatGPT permission cannot redirect incoming calls to a mobile carrier number. Explicitly support:

- **A — provision a new inbound Twilio number** → import/attach to an ElevenLabs agent via the supported native integration → test inbound and human transfer → owner decides whether to publish that number.
- **B — existing carrier number forwarded** → owner configures call forwarding using their carrier settings/dial codes, where supported; Gluons presents exact carrier-aware instructions and then runs a verification call. Carrier/handset controls might require manual changes. Gluons cannot assert forwarding succeeded until the test is observed.
- **C — hosted PBX/SIP/VoIP integration** → provider/admin authorizes route changes via that service, where supported; must verify entitlement, number ownership, fallback transfer target and reversal.

**Do not mistake Twilio verified caller ID for an inbound-capable number:** the ElevenLabs native Twilio integration requires an actual purchased/imported Twilio number for inbound. For human transfers use native Twilio or suitable SIP capabilities; ElevenLabs' advanced `register-call` route lacks built-in transfers. Configure voice, caller AI disclosure, applicable recording consent, transfer destination, business hours, agent permissions and fallback if ElevenLabs is unreachable.

**Phone setup states:** `not_configured → number_reserved → agent_configured → routing_pending → test_pending → verified → active`, with `routing_failed` and `needs_manual_carrier_step`. Never mark `active` because owner merely typed "yes".

**Demo:** real purchased test number; owner calls from another handset; ElevenLabs calls Gluons order tools; Gluons creates/verifies a sandbox ERP order; show real call ID plus ERP receipt. Label prerecorded/simulated calls separately.

## 5. Separation of connection from operational authority

Store an **integration connection** granting provider-level access, and **many narrowly scoped automation grants** for owner-approved behaviors. Reuse a connection across automations only if scopes and provider identity suffice. A new automation still needs a new authorization policy; e.g., `gmail.send` can exist while the owner forbids mass outreach.

**Example allow rules**:
- `order_email_ingest`: email read of label `Orders`; no external writes in shadow mode.
- `order_submit`: authorized ERP only; max $500/order; orders with unknown SKUs always manual.
- `confirmation_send`: recipient must match verified order customer; only after externally verified order status.
- `phone_answer`: inbound test number only until routing test and trial accepted.
- `marketing_send`: separate approval; prohibit unconsented blasting or use of a transactional connection for unrelated marketing.

Policy engine runs server-side for **every side effect** with audit receipt: actor, tenant, resource, source, operation, grant ID/version, expected costs, idempotency key, decision, external transaction ID and verification timestamp. Host confirmation prompts supplement—not replace—Gluons' policy.

## 6. API contracts — implement these routes and MCP tools

Authoritative Gluons API endpoints (exact paths may evolve, but semantics are mandatory):

```
POST   /v1/connection-sessions               # signed owner/admin starts authorized external onboarding
GET    /v1/connection-sessions/{id}          # owner-scoped status; no provider tokens
GET    /v1/oauth/{provider}/callback          # exact registered provider callback, state/PKCE validated
GET    /v1/connections                       # tenant-scoped provider identity, scopes, health
POST   /v1/connections/{id}/verify            # active token/scope/identity validation
DELETE /v1/connections/{id}                  # disable/revoke, cascade dependent automation waiting state
POST   /v1/phone-routes                       # create route/provision or generate manual forwarding instructions
POST   /v1/phone-routes/{id}/verify-test      # run/verify test-call entitlement and routing
GET    /v1/phone-routes/{id}                 # status and non-secret number/routing metadata
POST   /v1/automation-policies                # explicit per-automation owner action grant
POST   /v1/automation-policies/{id}/activate  # verify permissions, scopes, trial outcome, spend cap
PATCH  /v1/automation-policies/{id}           # tighten/expand after proper fresh owner authorization
POST   /v1/automation-policies/{id}/revoke    # stop future side effects, apply queued-task fences
GET    /v1/audit/permission-events            # accountable history
```

MCP tools exposed to supported assistants:

```
connections_list()
connection_begin(provider, requested_capabilities, automation_plan_id) -> setup_url, session_id, expires_at
connection_status(session_id) -> pending|authorized|needs_action|failed|expired
connection_disconnect(connection_id) -> pending|disconnected|error
phone_route_begin(mode, number_hint) -> route_id, required_owner_steps
phone_route_status(route_id) -> state, verification_status
policy_preview(automation_id) -> proposed actions, limits, data disclosures
policy_request_activation(automation_id, mode) -> explicit owner confirmation required
policy_status(automation_id) -> approved scopes, live mode, limits
```

Require normal authentication and authorization for every route. `connection_begin` returns a link, **not** a bypass that approves itself. Approval cannot be obtained from an untrusted message, a webhook, or an unconstrained model-generated string.

## 7. Data model (minimum required tables)

- `businesses`, `memberships`, `identity_sessions` (tenant/user/roles).
- `assistant_grants` (host issuer, audience, owner, scopes, expiry; never confused with provider credentials).
- `connection_sessions` (tenant, initiator, provider, opaque nonce/state hash, PKCE verifier secure ref, expiry, single-use status, return context).
- `connections` (tenant, provider, verified external account ID and display name, granted scopes, credential vault reference, status, health timestamps).
- `provider_event_subscriptions` (connection, webhook topic IDs, cursor, renewals, replay window).
- `phone_routes` (tenant, number, provider, route mode, ownership status, test call, transfer settings, activation state).
- `automation_policies` (automation/tenant, allowed operations, resource filters, confirmation requirements, spending limits, mode, version, owner grant).
- `action_authorizations` (run ID, side effect, policy version, decision, idempotency key, approver, timestamp).
- `permission_audit_events` (immutable actor, decision, resource identity, before/after references, revocation evidence).

Postgres unique indexes on `(tenant_id, provider, external_account_id)` where appropriate; RLS or equivalent tenant guards; encrypted credential references; optimistic concurrency/version checks. The actual access tokens belong in an encrypted vault, not skill files, browser/local storage, tool results or logs.

## 8. Mobile UX product acceptance

**All flows must run in a narrow mobile browser** with touch controls and no assumption of popups, inline browsers, or automatic return to the original assistant. The Gluons page is authenticated independently and displays provider name, account identity, requested privileges, cancel and continue. A finished onboarding page says `Connected` and `Return to your assistant when supported`, with `Continue in Gluons` as a reliable option. `GET connection_status` allows the host to catch up; use bounded polling when available, otherwise next user turn.

Do not deep-link with bearer access tokens. Do not use password collection, scraping personal WhatsApp, browser automation to bypass OAuth, or direct OS microphone/phone access as a substitute for call routing. All URLs short-lived, allowlisted and owner-bound.

## 9. P0 acceptance tests — all required before claiming 'works'

| ID | Test | Must pass |
|----|------|-----------|
| AUTH-01 | ChatGPT web MCP OAuth with test account | Correct tenant, scope, audience; no cross-account access |
| AUTH-02 | Unsupported ChatGPT mobile MCP | Redirect to Gluons mobile web fallback; no false claim of in-chat installation |
| AUTH-03 | Gmail connect from iOS/Android browser | Real provider consent, correct mailbox, return/status works |
| AUTH-04 | OAuth declined, expired, CSRF altered, reused callback | No connection; visible recoverable error |
| AUTH-05 | Gmail read authorized but send unapproved | Reads allowed; customer email send blocked |
| AUTH-06 | Provider scope revoked mid-task | New external writes blocked; task `waiting_external`; owner informed |
| AUTH-07 | Wrong Gmail/WhatsApp business selected | External identity displayed and must be confirmed before live activation |
| AUTH-08 | Shared Gmail connection reused for new automation | No new provider login if scopes suffice; **new action-policy approval required** |
| AUTH-09 | Customer email says 'ignore policy' | No expanded permissions; safe escalation |
| AUTH-10 | External webhook duplication / retries | Exactly one external transaction or message given same idempotency key |
| WA-01 | Official Meta sandbox/onboarding, test business | Verify account and webhook signature, legal messaging window |
| WA-02 | Personal WhatsApp number with unsupported integration | Clearly refused; offer supported business onboarding path |
| PHONE-01 | Real inbound call to test Twilio number | ElevenLabs agent responds; real call ID and event receipt |
| PHONE-02 | Unforwarded personal mobile number | Remains `routing_pending`; doesn't claim AI answers inbound calls |
| PHONE-03 | Manual forwarding or PBX route verified | Test call reaches intended agent; human transfer/fallback works |
| PHONE-04 | Caller asks not to be recorded / hang up | Respect applicable disclosure/consent/retention policy |
| POL-01 | Owner cancels automation while work queued | New writes blocked; in-flight irreversible actions honestly reported |
| POL-02 | Owner approval in chat but no provider OAuth | No provider actions; connection remains unconfigured |
| POL-03 | Owner connects account but never activates workflow | No automatic replies, order submissions or calls |

For the YC demo, demonstrate AUTH-03, AUTH-05, POL-02, POL-03, PHONE-01 and one real ERP sandbox receipt. Other scenarios must have automated staging tests and the UI must honestly show integration readiness.

## 10. Cloud implementation details and work assignment

1. **Identity engineer:** Gluons OIDC/MCP OAuth server, callback registration, JWT verification, per-tenant session binding, PKCE, state/nonce, refresh/revoke and account-switch detection.
2. **Integration engineer:** `GoogleConnectionProvider`, `MetaBusinessConnectionProvider`, ERP credentials; maintain versioned provider scope tables and webhooks; monitor verification and expiration.
3. **Telephony engineer:** `PhoneRouteProvider` using Twilio/ElevenLabs; provisioning, import, forwarding instructions, test-call verification, transfer and failover.
4. **Workflow/security engineer:** policy evaluator invoked before every tool-side effect; approval state machine; budget limits; idempotency and revocation fences.
5. **Full-stack engineer:** responsive connect and recovery pages, approval summary, statuses, account identities, disconnect controls; host-fallback screens.
6. **QA engineer:** all 19 P0 tests, real provider sandbox OAuth and incoming call, negative and mobile tests; record real evidence rather than simulated status labels.

**Dependencies:** Production workflow activation is blocked until identity, provider connection, action policy and routing (if voice) are verified. A code generator or temporary demo runner **must not bypass these**. If a provider cannot be authorized for a demo, use a properly labeled sandbox or keep that feature disabled. No invented auth 'success' screens.

## Sources / compatibility watchlist (recheck before rollout)

- OpenAI MCP plugin OAuth: https://developers.openai.com/plugins/build/auth
- OpenAI mobile MCP limitation / plan permissions: https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt
- Gmail API scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
- Gmail server-side OAuth: https://developers.google.com/workspace/gmail/api/auth/web-server
- Gmail notifications and watch renewal: https://developers.google.com/workspace/gmail/api/guides/push
- Meta 2026 onboarding changes: https://developers.meta.com/resources/videos/unified-onboarding-whatsapp/
- Meta account model changes: https://developers.meta.com/resources/videos/whatsapp-account-model-evolution/
- ElevenLabs native Twilio routing: https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/native-integration
- ElevenLabs advanced route lacks transfers: https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/register-call

**Readiness truth:** This document is a mandatory implementation contract. As of its creation, the provided Gluons repository has no real completed Google, Meta or telephone OAuth/number-routing implementation. The included reference API is simulation only.
