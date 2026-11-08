# Review of the November 8 master context

## Overall judgment

The strongest thesis is that assistants need a durable, owner-controlled execution service with reusable domain procedures and independently verified outcomes. The current architecture already implements much of this foundation. The November 8 additions improve the product direction by focusing on onboarding, reusable business facts and fewer unnecessary approvals. Execution should extend the existing platform and establish real-world evidence before broadening the promise.

The master document is valuable as a private archive. It is not suitable as the sole agent backlog: it interleaves historical drafts, personal information, current priorities, third-party assumptions and a very broad future catalog. This package translates it into bounded, dependency-ordered work.

## Reconcile, retain, change, defer

| Master topic | Assessment | Execution consequence |
|---|---|---|
| Sections 1–2: any compatible assistant operates businesses | Retain the durable-service thesis; compatibility is per actual client/surface | Prove one supported assistant first, then a second on the same user/org; keep web fallback |
| Section 2D: one guided complete business launch | Strong north star, too many independently uncertain providers for first release | Deliver existing-business operation first; then one country/product/storefront path with explicit handoffs |
| Sections 3–4: differentiation and interviews | Discovery is useful; broad superiority and token savings remain hypotheses | Compare the same task and tools with/without Gluons; record corrections and costs |
| Section 5: paying manual pilots | Valid reported willingness-to-pay signal; separate from SaaS activation/retention | Revalidate actual workflow and system of record for one pilot; measure recurring use and invoicing separately |
| Section 6: self-service and field reps | Both plausible; field labor can erase margin | Measure guided and independent onboarding before recruiting/scaling reps |
| Section 7: pricing and market size | Pricing/market arithmetic are scenarios, not forecast or recurring revenue | Keep ledger-first costs; choose explicit pilot terms and reconcile real payments |
| Section 8: stack | Existing FastAPI/Postgres/Next.js fits; older orchestration details conflict with ADRs | Keep Postgres as execution authority; do not add a second scheduler/orchestrator without evidence |
| Section 9: permission layers | Correct and already substantially present | Regression-test all four boundaries rather than reimplementing them |
| Section 9A: identity/passport/setup | Highest-value new product work, with real account-linking and data-sharing risks | Incremental Google sign-in, typed Passport and durable connection room; passkeys follow measured need |
| Section 10: 31 expert skills | Preserve roadmap; catalog registration is not expertise or action readiness | Promote by provider family/customer demand, with expert review and per-version evidence |
| Section 11: prototypes and verification | Sound distinction; some platform descriptions lag code | Replace greenfield phases with evidence gates mapped to actual modules |
| Section 12 and Appendix C: research | Useful hypotheses, mixed samples and dated figures | Do not make market-size, adoption or savings claims in product UI from these notes |
| Sections 13–18 and Appendices D/E | Private founder, application and outreach material | Keep outside engineering repository; no automatic applications, posts, email or investor outreach |
| Section 19: uncertainties | Good source for risks but not prioritized | Convert into owner decisions, release blockers and measurable experiments |
| Appendices A/B/B2 | Rich historical specifications | Read relevant originals under `gluons_blueprint/`; retain as read-only; use current executable contracts |

## Decisions to adopt for this execution cycle

1. **Evidence before additional breadth.** A real, reversible workflow proves more than another nominal integration. The first candidate is Gmail intake → approved ERP draft → independent ERP readback if the chosen pilot actually uses/accepts ERPNext. If the pilot uses a different authoritative system, select that supported system explicitly; do not impose ERPNext to make a demo pass.
2. **Two tracks, one foundation.** The first release runs an existing business workflow. The subsequent commerce beta launches a narrowly defined business. Share identity, setup, policy, receipt, budget and status code between them.
3. **No foundational rewrite.** Keep the modular monolith, Postgres durable jobs, action gateway, explicit RLS and existing Next.js app. Temporal, an embedded automation vendor or microservices are not prerequisites.
4. **Add one low-friction sign-in path first.** Implement Google identity using a maintained verifier/library and explicit linking to existing users. Keep verified email/MFA recovery working. Passkeys need RP/origin/challenge/recovery design; do not implement two new identity systems simultaneously before the first host flow works.
5. **Passport is structured data with provenance, not a credential wallet.** Do not store passwords, bank details or identity scans in general business facts. Prefill requires a preview and consent for the specific provider/fields/purpose; changing the Passport does not silently change provider legal records.
6. **A provider draft is still an external write.** Auto-saving an internal draft can be low-risk. Creating a draft object in Shopify/Gmail remains a provider operation through the gateway and approved authority.
7. **Reduce friction through bounded standing grants.** A broad request to build a business does not authorize all spending/messages. Batch approvals bind a finite manifest and expiry; changing payload or target requires renewed authority.
8. **Small intent interface over existing tools.** The master proposes roughly eleven high-level tools while the current server exposes 47. Add a compatible intent-oriented façade/profiles where supported; do not abruptly rename or remove existing tools. Measure discovery/token costs before claiming a benefit.
9. **Use existing providers' onboarding.** No dummy merchant accounts, password swapping or universal account-creation promises. Store setup/payment review can be pending while independent drafting continues.
10. **Separate money flows.** Customer checkout, supplier purchases and Gluons subscription billing are different systems. Stripe Connect is a separate product/business decision, not the default missing part of Shopify onboarding.
11. **Treat cancellation honestly.** Revoking an assistant binding removes that client's access. Pausing an automation stops future dispatch. Neither retracts an already-sent message nor automatically cancels separately authorized business work.
12. **Constrain claims of exactly-once behavior.** The receipt/lookup/lock design is valuable, but external providers can return ambiguous outcomes. Promise deduplication and reconciliation under stated assumptions; do not claim mathematical exactly-once delivery across arbitrary APIs.

## Necessary additions to the original plan

- A baseline commit and reproducible test evidence for every milestone.
- An explicit first pilot, system of record, workflow and target geography.
- A source-of-truth readiness service shared by web, REST and MCP.
- Contract/version ownership before parallel implementation.
- A staging run with backup restore, incident recovery and external side-effect fencing.
- Current provider access/app-review evidence, separate from implemented adapters.
- Defined customer-visible exception handling when provider/API access is unavailable.
- Data export/deletion coverage for newly introduced profile/setup/identity records, plus an explicit retention decision for audit evidence.
- Per-skill expert review records rather than generic claims of professional endorsement.
- Measurement of setup labor, support, failed attempts and provider costs alongside tokens.

## What should wait

Defer simultaneous live implementation of all 30 draft-only skills, generalized supplier sourcing/purchasing, automated banking/KYC, public app distribution without provider clearance, many countries at once, full passkey plus multiple social-login rollout, and scaled field sales before repeatable onboarding. Deferral preserves the full roadmap; it does not reclassify these features as complete or remove their future acceptance requirements.

## Risks ranked for the next release

| Priority | Risk | Mitigation and owner |
|---|---|---|
| P0 | No real external action/host/deployment evidence | D+A establish sandbox and hosted proof; G independently verifies |
| P0 | Tenant/identity/account mix-up during new sign-in/setup | B+C require authenticated binding, explicit org, RLS and negative tests |
| P0 | Duplicate or unauthorized external write under retry/revocation | D preserves gateway/reconciliation and tests crash windows; G reviews |
| P0 | False launch readiness from combined partial signals | F owns evidence-derived readiness; no manually editable verified flag |
| P1 | Provider access/review or unsupported regional route | Founder starts access requests early; D records precise blocker and allowed fallback |
| P1 | Runtime succeeds locally but fails in CI/images/cloud | A tests clean checkout, images and staged restore before pilot |
| P1 | Onboarding requires founder debugging for every customer | E instruments interruptions, owner comprehension and manual interventions |
| P1 | Subscription below total operating/support cost | G measures per-result cost; founder sets pilot terms from evidence |
| P2 | Over-parallel agents duplicate core modules or fork migrations | A owns shared files; bounded branches and contract-first integration |

This review deliberately does not revalidate immigration, investor deadlines or personal-history claims. Those are outside engineering execution and must be refreshed separately before use.
