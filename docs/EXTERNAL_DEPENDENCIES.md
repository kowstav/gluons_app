# External configuration and verification

No real provider credentials were requested through chat or used in automated
provider-double tests. Copy `.env.example` to the ignored `.env` for local secrets.
For hosted environments use Secret Manager secret versions and ADC/scoped service
accounts. Inspect configuration presence without printing values.

| Service | Required configuration | Verification still required |
|---|---|---|
| Google Gmail | Web OAuth client ID/secret, Gmail API, consent screen/test users; exact `/v1/oauth/gmail/callback` URL | Real browser consent, refresh/revoke, send/readback; restricted-scope review/security assessment before public release |
| ElevenLabs/telephony | API key, separate tool and post-call secrets, authorized agent and supported inbound number/SIP route | Genuine inbound call, route ownership/forwarding evidence, transfer/fallback, signed post-call event and shared approved order |
| WhatsApp | Approved Meta app/business onboarding, config ID, app secret, webhook token | Connector implementation, business entitlement, signatures, templates/service window, review of applicable platform terms |
| Shopify | Merchant-owned store, reviewed app distribution and app OAuth credentials | Implement and test OAuth/catalog/webhooks/fulfillment. Signup/transfer and merchant terms remain owner/provider actions |
| Payments | Eligible provider/country and merchant-owned onboarding | Implement status verification; KYC/banking/approval cannot be self-asserted |
| ERPNext | Authorized customer/sandbox endpoint and restricted API account | Simulator tests do not verify ERPNext itself |
| Models | OpenAI/Anthropic API keys, Gemini ADC/project or permitted private endpoint | Reviewed dated descriptors/prices and machine-graded per-skill model evaluation evidence; live calls and invoice reconciliation |
| Email delivery | Approved SMTP transport/domain | Deliverability and recovery tests outside local file transport |
| GCP | Separate projects, billing/region, ADC/WIF, domains/TLS, Secret Manager versions, DB roles | Explicit approval before chargeable provisioning/deployment; migrations, staging smoke, backups/restore and monitoring drills |

Obtain Google credentials in Google Cloud Console's APIs & Services/OAuth client
configuration. Authorize the exact callback and add consent-screen test users.
Gmail read and send are separate capabilities; verified-send recovery also needs
readonly access. Do not enable broad modify/delete scopes for order processing.
Official sources: [OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server),
[Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes).

Do not connect production customer data until domain/privacy/retention requirements
and provider approvals are satisfied. Skills in legal, tax, health, insurance and
other regulated domains remain administrative drafts requiring qualified review.
