# Current progress and evidence audit

Audit date: 2026-11-08. Baseline: `4fa7247bdc06b2ef7051f117c1c4c37d32d48b17` (`Harden account connections and finish credential-free work`, committed November 7).

## Finding

Gluons is a substantial locally implemented platform with deliberate fail-closed boundaries. It is neither a greenfield blueprint nor a production-verified SaaS. The bottleneck is now integration evidence, operating readiness and a coherent first-user journey. More catalog entries alone will not resolve it.

Do not report a single percentage complete. The existing matrix contains **83 requirements: 13 done, 58 partial, 12 blocked**; rows have different sizes, and `done` means repository implementation/test evidence rather than production verification.

## GitHub reconciliation

- At the start of this audit, this checkout had no Git remote. The GitHub connector returned no accessible installed repositories and no `kowstav` Gluons search result. This was an access/discovery limitation, not proof that no repository existed.
- The authenticated GitHub browser exposed [kowstav/gluons](https://github.com/kowstav/gluons), a **different, older private prototype**. Its README describes `apps/dashboard`, TypeScript runtime packages, Supabase, a deterministic browser demo, synthetic history, and unverified live adapters. This was a README/tree inspection, not a full code or CI audit of that repository. The repository listing showed an update on November 1.
- The user requested a new repository named `gluons_app`. [kowstav/gluons_app](https://github.com/kowstav/gluons_app) was created privately on November 8 for this FastAPI/PostgreSQL implementation. The older repository was left untouched.
- Do not merge the old Supabase execution engine into this Postgres control plane. Consider individual UI ideas/assets only after provenance and compatibility review.
- Publication and first GitHub CI results are recorded in [verification](06_VERIFICATION_AND_RELEASE.md); the old November 7 statement that there is no remote describes historical state.

## Evidence classification

| Label | Meaning |
|---|---|
| Source inspected | Relevant implementation exists and was reviewed in this audit; this is not comprehensive certification |
| Recorded local test | Previous run is documented in `docs/TEST_RESULTS.md` |
| Fresh local check | Command actually executed during this audit, with outcome recorded separately |
| Provider double | Gluons behavior exercised with a controlled substitute; says nothing about real provider acceptance |
| Simulator | A real local HTTP service implements only a subset of the provider |
| Provider sandbox | Actual provider test account and API, with dated evidence |
| Supervised production | Authorized real account/action, provider receipt and independent readback |

## Capability map

| Area | Current implementation/evidence | Remaining gap | Primary code/test entry points |
|---|---|---|---|
| Identity/tenancy | Email/password, email verification, TOTP/recovery, sessions, orgs, roles, forced RLS | Google identity, passkeys, production mail and recovery proof; membership/tenant regression remains mandatory | `apps/api/src/gluons/identity.py`, `authz.py`, `db/session.py`; `test_core_identity.py` |
| Assistant authorization | OAuth PKCE, resource binding, refresh rotation, independent binding revocation, CIMD/DCR, MCP | Real hosted-client installation and interruption/reconnect matrix | `oauth/service.py`, `oauth/routes.py`, `mcp/server.py`; assistant OAuth and MCP tests |
| Policy/runtime | Durable jobs, leases, retries, approvals, budgets, action exclusion, receipts/readback | Real-provider ambiguous-outcome tests, staged deployment/recovery and load evidence | `gateway.py`, `policy.py`, `queue.py`, `runtime/`; core runtime/policy and budget tests |
| Gmail | Consent, refresh, history sync, attachment intake, guarded send/readback | Configured OAuth client, real mailbox, narrow-scope verification; public rollout requirements | `connections/google.py`, `gmail_sync.py`, `connectors/gmail.py`; Gmail tests |
| Shopify | Install before Gluons sign-in, browser-bound claim, refresh locking, privacy/uninstall webhooks, draft/publication adapters | Dev-store proof, app/distribution access, storefront/checkout orchestration and shipping prerequisite | `commerce/install.py`, `commerce/service.py`, `connectors/shopify.py`; account/commerce tests |
| WhatsApp | Embedded Signup, webhook intake, deterministic send gates | Actual signup/mobile behavior, business/test number/access and current policy validation | `whatsapp/`, `connectors/whatsapp.py`; WhatsApp tests |
| Voice/phone | Country-aware route states, call verification, ElevenLabs callbacks and shared intake | Real routing and call, provider lifecycle behavior, consent/handoff verification | `telephony/`; `test_voice_gateway.py` |
| Skills | 31 contracts; order action graph; 30 draft/review workflows with constraints | Domain-specific action graphs, business-system selection, expert and real-provider evidence | `packages/skills/`, `skills/domain/`; catalog/domain/order tests |
| Business launch | Plan, approval, store binding, pause/resume/cancel, separate commerce receipts | Unified readiness projection, stage progression, payment/tax/shipping/checkout evidence, safe final publication | `launch.py`, `commerce/service.py`; business-launch/commerce tests |
| Business Passport | Basic organization fields and `business_facts` JSON exist | Typed profile/provenance, field sharing consent, freshness, resumable multi-provider setup | `db/models.py: Organization`; new modules proposed in contract guide |
| Models/economics | Four adapters, evaluated routing, usage ledger, cost/budget controls, trial/ROI measurements | Provider/model evaluations and real cost reconciliation; no certified model reported | `model_gateway.py`, `models/providers.py`, `billing.py`, `trials.py`, `reports.py` |
| Owner app | API-backed mission areas, connections, trials, launch, messages/orders, data rights | Focused onboarding usability, interrupted provider redirects, deeper browser coverage | `apps/web/src/components/console.tsx`, `operations.tsx`, `lib/api.ts` |
| Operations | Dockerfiles, Terraform environments/locks, CI/manual deploy definitions, runbooks, redacted logs | Image builds/run, GitHub CI, real cloud deployment, restore/alerts and proxy verification | `.github/workflows`, `infra/`, `docs/runbooks/` |

Paths in the final column without a prefix are beneath `apps/api/src/gluons/`; test filenames are beneath `apps/api/tests/`.

## Previous agent's checkpoint

The November 7 commit and tests support the user's supplied change summary: browser-bound Shopify installs, uninstall/privacy handling, serialized refresh, friendly OAuth errors, active connection flows, periodic connection health, deletion after a configurable grace period, network rate limiting, trial screens and Terraform lock files. These are implemented capabilities; their tests do not constitute live provider proof.

Prior recorded results: **694 backend tests passed**, **2 Playwright tests passed** (desktop Chromium and Pixel 7), web build/typecheck, Ruff, traceability and Terraform validation passed. The two browser tests are broad scenarios run in two projects; do not present that count as comprehensive independent coverage of every screen.

## Specific integration work the new plan must address

1. `launch._serialize()` always reports `payment_status=unverified` and `store_live=False`; `launch.evidence()` always reports zero verified fulfillments. Meanwhile, `commerce.record_provider_fact()` stores verified product/publication/fulfillment receipts. This conservative separation prevents false success, but needs a shared evidence-derived projection before the end-to-end launch experience can be complete.
2. `shopify.fulfillment.create` requires a verified `shipping.shipment.dispatch` receipt. No current connector produces that receipt. A carrier label or Shopify fulfillment record must not be treated as proof of physical shipment or delivery.
3. `Organization.business_facts` is not a full Passport with source, confirmation time, version and sharing consent. Reuse it only through a typed migration/projection strategy.
4. Existing assistant OAuth is not Google sign-in. A new identity provider must link to the existing Gluons user without email-based silent account merging.
5. Existing docs contain historical migration ownership for `0003`–`0006`; the actual migration chain reaches `0010`. New agents must reserve the next revision after inspecting HEAD, never reuse those historical numbers.
6. CI declares Terraform `1.17.1`, while prior local validation was documented with `1.14.0`. Pin reconciliation and the first real CI run are required; syntax validation on one toolchain does not prove another toolchain.
7. The private founder document was untracked at audit start. It must remain excluded from source publication, including future broad `git add` operations.

## Limits of this audit

This is an evidence-backed planning and focused source review, not a penetration test or proof of deployed infrastructure. No customer account, provider credential, paid operation, live message, model invocation or cloud deployment was used in the audit. Historical claims and private pilot outcomes were not independently audited. The full source material includes older duplicated blueprints, skill specifications and application drafts; newer founder priorities guide product sequencing, while existing architecture decisions and verified provider constraints govern implementation.
