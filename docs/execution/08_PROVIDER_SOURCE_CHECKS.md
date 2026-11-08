# Provider source checks

Checked November 8, 2026 for planning. These sources support design boundaries; they do not prove Gluons account eligibility or a working integration. Re-open the relevant documentation when implementing or releasing. Existing dated ADR/provider notes remain useful context, but details not listed as freshly checked below were not independently revalidated in this audit.

| Topic | Fresh source | Consequence |
|---|---|---|
| OpenAI remote MCP authorization | [Authentication](https://developers.openai.com/plugins/build/auth) | Use protected-resource/authorization metadata, PKCE S256, resource validation and actual configured callback/client metadata. Current docs describe CIMD and DCR; verify the selected flow against the target host rather than hard-coding old setup assumptions. |
| ChatGPT identity | [Client ID eligibility](https://developers.openai.com/siwc/request-client-id) | Offered to selected commercial partners. Do not make it a launch dependency or assume Gluons has access. |
| MCP authorization revision | [2026-07-28 authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) | The `latest` authorization URL resolved to this revision during the audit. The master also cites a 2025 revision; preserve compatibility only where tested and declare supported protocol/client combinations. |
| Google identity vs API access | [Google Identity Services overview](https://developers.google.com/identity/gsi/web/guides/overview) | Google distinguishes authentication from authorization. Sign-in is not permission to read/send Gmail. |
| Gmail scope selection | [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes) | Choose the narrowest needed scopes and confirm sensitive/restricted classification and applicable verification before public rollout. |
| Shopify installed-app authority | [App authentication](https://shopify.dev/docs/apps/build/authentication-authorization) | Admin API access is on behalf of an authorizing merchant and limited to approved scopes; offline tokens support background work. This is not general merchant signup authority. |
| Shopify distribution | [Select distribution method](https://shopify.dev/docs/apps/launch/distribution/select-distribution-method) | Distribution is an explicit provider decision; the documentation warns it cannot be changed after selection. Confirm the exact organization/store route before configuring it. |
| Stripe Connect onboarding | [Hosted onboarding](https://docs.stripe.com/connect/hosted-onboarding) | Provider-hosted collection depends on country/business/capabilities; reuse/prefill can reduce repeated data entry. A return redirect alone must not be treated as completed verification. |

## Claims requiring a fresh release-time check

- Actual ChatGPT/Claude/Gemini client availability, plans/workspace controls, mobile support and callback behavior. A local protocol test does not prove any host integration.
- Shopify current API version, install configuration (`use_legacy_install_flow`), offline refresh semantics, public/custom distribution limits, protected customer data, fulfillment permissions and supported partner-transfer route.
- Meta Embedded Signup version/response shape, popup behavior under referrer policy, messaging windows/templates/prices, product eligibility and business/app review.
- ElevenLabs during-call conversation availability, tool/post-call authentication and reconciliation, permitted telephony routes and human handoff in the target country.
- Gmail public verification/security-assessment applicability for the exact scopes and handling; test-mode limits; real SMTP deliverability.
- Stripe/Shopify payment architecture and the country's actual provider availability; billing product choice and pricing.
- Model identifiers, SDK compatibility, rates and evaluation quality. Do not copy dated rates or model/version strings into a new release without checking them.
- Upstream package/image/action SHAs, licenses and installability. Candidate repository lists do not establish compatible versions or SaaS license permission.

## Source-record format for future agents

For each provider behavior relied on, record the exact official page, checked date, selected API/SDK version, relevant constraint, affected code/config/tests and what remains to be tested against the real provider. Keep short paraphrases; do not paste whole documentation pages. If current docs conflict with an ADR, prepare a narrow replacement ADR and tests before changing behavior.
