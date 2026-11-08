# Founder actions, prerequisites and measurement

## Choose one concrete pilot

Before EX-02/03 live execution, record the consenting business, primary pain, inbound channel, actual system of record, geography, workflow volume, permitted data, allowed test actions, budget and owner escalation contact. Use a private operational record for identities; put only sanitized decisions in this repository.

Default candidate: an existing pilot's order intake with supervised draft creation. Prefer the channel/system the business already uses. Gmail is a reasonable first connector proof given existing code, but does not become the chosen business workflow merely because credentials are easy to obtain. A Shopify existing-merchant path is an alternative when there is an actual participating merchant and app access.

## Parallel external prerequisites

| Need | Concrete owner action | Agent can prepare before access | Blocks |
|---|---|---|---|
| Google identity/Gmail | Create correctly scoped clients/redirects and consent test users; configure secrets outside chat | Callback map, scopes, threat cases, proof script | Real sign-in/mailbox proof |
| Authoritative order system | Identify actual system/version and authorize a restricted sandbox user | Connector contract, fixtures, double/failure tests | Real order result |
| Shopify | Choose supported app distribution, configure template and dev store, approve required access | Install/webhook scripts and readiness tests | Commerce proof and public distribution |
| Meta/WhatsApp | Configure app/signup/business/test-number access and verify product eligibility | Popup/redirect/error tests and send-policy matrix | WhatsApp activation |
| Voice | Choose supported number/SIP route for pilot country and authorize test call | Fast safe callback response, reconciliation/fallback tests | Real voice route proof |
| Models | Provide one configured provider and explicit evaluation spend limit | Gold fixtures, scorer, routing/cost harness | Model-certified workflow |
| Docker | Make a working daemon available | Build definitions and clean-context review | Images/local real ERPNext if using Docker |
| Cloud | Choose project/region/domain and approve an exact reviewed resource/cost plan | Terraform validation, migration plan, IAM/callback checklist | Hosted staging and remote assistant proof |
| Customer-facing policies | Have privacy/deletion/retention and required notices reviewed for actual processing | Data inventory, draft technical descriptions, deletion tests | Public provider approval/production data onboarding |
| Billing | Define pilot recurring threshold, invoice cadence, currency and usage limits | Ledger exports/reconciliation and cost report | Real recurring-revenue validation |
| Stripe Connect | Decide whether a standalone payments-platform use case is actually needed | Decision memo and status-only interfaces | Only the selected Connect scope; not baseline Shopify/Gmail proof |
| Expert review | Select qualified reviewer for the next promoted domain | Cases, failure taxonomy and evidence packet | Expert-reviewed skill claim |

The existing `docs/EXTERNAL_DEPENDENCIES.md` provides configuration names. Recheck dated provider restrictions before use. Never send credentials to an agent in chat. Product credentials belong in approved secret storage, distinct from GitHub access.

## Measurements to collect

| Metric | Definition | Avoid |
|---|---|---|
| Activation | Eligible consenting business reaches first independently verified useful external result | Counting a connection click, draft-only result or simulator as production activation |
| Onboarding duration | Active owner time, engineering help time and provider waiting time separately | One blended duration that hides KYC/provider waiting |
| Repeated data entry | Number of already-confirmed reusable fields re-entered per onboarding | Copying sensitive fields everywhere to optimize the count |
| Completion | Correct verified outputs / eligible events; list exceptions, failures and exclusions | Treating every human escalation as autonomous success |
| Correction burden | Owner/engineer correction minutes and reason per event | Only measuring model tokens |
| Reliable resumption | Interrupted sessions/tasks resumed correctly / interruption trials | Counting browser page reload as durable recovery proof |
| Action cost | All known model/provider/channel/compute costs including failed attempts / verified results | Ignoring failed runs or using estimated rates as billed totals |
| Contribution margin | Collected recurring revenue minus attributable variable provider, infra, support and onboarding costs | Reporting setup fees as MRR or excluding founder labor |
| Repeat workflow | Incremental time/cost to activate a second workflow on the same business | Counting a second catalog click as incremental adoption |
| Retention/payment | Actual continued usage and invoices/receipts with dates | Conditional willingness to pay as collected revenue |
| Consent comprehension | Owner correctly states business/account/actions/limits and revocation path | Optimizing clicks by obscuring permissions |

Represent currency and effective price dates explicitly. Do not aggregate INR and USD without a dated, disclosed conversion. Budget alerts are not hard spend caps. A personal assistant subscription does not automatically authorize or fund backend inference; validate the actual commercial integration before assuming such funding.

## Controlled comparison

Compare a generic assistant workflow with the same task using Gluons, using the same underlying model/provider where practical. Predefine tasks, authority, time limit, evidence requirements and success criteria. Separate first-time setup from steady-state execution. Include failed attempts, human review, provider fees, support and correction time. Use counterbalanced or matched cases and report sample size/limitations. Do not deliberately expose real customers to unsafe unbounded actions to create a baseline; use authorized sandbox tasks or read-only historical replay.

## Decision checkpoints

- After first provider proof: does the result solve the selected owner's real problem?
- After 3–5 onboarding sessions: which step actually causes abandonment/help requests?
- After at least one week of supervised use: which exception classes dominate support cost?
- Before the next skill: can existing connectors satisfy demand, or is this a new integration business?
- Before field-rep expansion: do rep setup/support costs fit collected revenue?
- Before public claims: link every shipped capability, customer/payment statement and savings claim to its own evidence.

Private biography, application answers, investor targeting, relocation and alternative startup ideas remain separate founder work. This engineering package neither sends outreach nor certifies those claims.
