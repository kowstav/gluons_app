# Gluons execution package

Prepared 2026-11-08 (Asia/Kolkata). Implementation baseline: `4fa7247bdc06b2ef7051f117c1c4c37d32d48b17`. This is a delivery plan, not a claim that the proposed work has shipped.

The next outcome is **one repeatable, owner-authorized business workflow with a real provider receipt, understandable controls, measured operating cost, and recovery after interruption**. Preserve the complete product vision while releasing capabilities only when their evidence supports them.

## Read in this order

Publication is complete in [kowstav/gluons_app](https://github.com/kowstav/gluons_app); the owner made the repository public on November 9. EX-00 security fixes were merged into main at `cf5a383`. See the [verification ledger](06_VERIFICATION_AND_RELEASE.md) for dated evidence and remaining hosted and provider gates.

1. [Current progress and evidence](01_PROGRESS_AUDIT.md): what exists, what GitHub contains, what is unverified.
2. [Master-context review](02_MASTER_PLAN_REVIEW.md): reconciliation of the November 8 proposal with the implemented product.
3. [Execution plan](03_EXECUTION_PLAN.md): outcomes, dependency order, work packages, release gates and sequencing.
4. [Architecture and interface guidance](04_ARCHITECTURE_AND_CONTRACTS.md): proposed additions, existing modules to extend, state and identity boundaries.
5. [Agent working agreement](05_AGENT_WORKING_AGREEMENT.md): file ownership, integration rules, commands and handoff format.
6. [Verification and release gates](06_VERIFICATION_AND_RELEASE.md): executable acceptance scenarios and evidence requirements.
7. [Founder actions and measurement](07_FOUNDER_ACTIONS_AND_MEASUREMENT.md): external prerequisites, economics, pilot and business decisions.
8. [Provider source checks](08_PROVIDER_SOURCE_CHECKS.md): primary references, checked facts and unresolved assumptions.

EX-01 preparation: [Gmail provider-proof notes](EX01_PROVIDER_PROOF.md). The isolated gateway-backed self-test and read-only collector are implemented on [draft PR #2](https://github.com/kowstav/gluons_app/pull/2), with provider-double tests and CI passing. Review and integration are pending; no real Gmail send or provider evidence has been obtained.

## Give an agent one brief

| Brief | Responsibility | Earliest start |
|---|---|---|
| [A — Integration and release](agents/A_INTEGRATION_AND_RELEASE.md) | Baseline, shared schema, CI, deploy evidence, final integration | Immediately |
| [B — Identity and assistants](agents/B_IDENTITY_AND_ASSISTANTS.md) | Sign-in improvements, linking, OAuth host proof | After baseline; existing-flow host testing can start first |
| [C — Passport and setup API](agents/C_PASSPORT_AND_SETUP.md) | Business profile, setup sessions, next-action contracts | After schema/interface agreement |
| [D — Provider workflow](agents/D_PROVIDER_WORKFLOW.md) | First real workflow, connection lifecycle, receipts | Harness immediately; live run after access/authorization |
| [E — Owner experience](agents/E_OWNER_EXPERIENCE.md) | Connection room, mobile recovery, approvals and status | UX states immediately; API integration after contracts |
| [F — Commerce launch](agents/F_COMMERCE_LAUNCH.md) | Unified readiness, bounded launch stages, fulfillment prerequisites | Readiness design first; complete launch after first-workflow gate |
| [G — Quality, skills and economics](agents/G_QUALITY_SKILLS_AND_ECONOMICS.md) | Independent acceptance, model evals, skill promotion, metrics | Immediately on current baseline |

Use at most three implementation lanes plus one integration/review lane at a time. These are role briefs, not a requirement to run seven agents simultaneously. This package does not automatically launch agents or authorize paid/cloud/provider operations.

## Start an agent with this prompt

> Read `CLAUDE.md`, applicable `AGENTS.md`, `docs/execution/README.md`, the current progress audit, working agreement, and your assigned brief. Refresh Git status and the relevant implementation before coding. Complete only the next dependency-ready work package in that brief. Preserve existing authorization, receipt and tenancy boundaries. Coordinate shared-file changes with the integrator. Report the base/head commits, changes, exact checks/results, evidence class, blockers and next owner. Do not claim live verification from provider doubles. Do not push the private founder master file.

The private master document remains local and git-ignored. The engineering requirements necessary to work from another checkout are captured here without reproducing the founder biography, application exports or private outreach drafts. The original blueprint remains unchanged.
