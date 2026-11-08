# Verification and release gates

## Audit verification ledger — November 8

Baseline implementation: `4fa7247bdc06b2ef7051f117c1c4c37d32d48b17`. Only documentation and Git publication safeguards are changed by this planning task.

| Check | Audit result |
|---|---|
| Ruff over `apps/api` | Passed in this audit |
| Traceability validator, read-only invocation | Passed: 83 rows, zero problems; 13 done / 58 partial / 12 blocked |
| Pytest collection | Completed successfully; per-file test inventory collected |
| First backend execution in restricted sandbox | Failed during PostgreSQL connection setup before first test; not an application assertion pass |
| Backend execution with permitted local process/loopback access | Passed, exit 0: all 694 tests; real local PostgreSQL and simulator/provider doubles, not real providers |
| Web typecheck | `npm run typecheck --prefix apps/web` passed, exit 0 |
| Prior full verification | November 7 ledger reports 694 backend and 2 Playwright passes, build/typecheck, lint and Terraform validation |
| Fresh GitHub checks | Backend, migrations, web typecheck/build/Playwright, secret scan and all three Terraform validations passed. Security failures below. |
| Real provider/cloud/assistant-host test | Not performed in this planning task |
| GitHub repository | Private `kowstav/gluons_app` created; main and execution package published at `0eb4d9b65e5c6668887894e1214768c6685cbc47`; origin configured |
| Documentation validation | 16 execution Markdown documents; relative links valid; all 31 catalog IDs mapped in the expansion plan; `git diff --check` passed |
| Pre-publication secret scan | Gitleaks 8.30.1, official checksum verified; initial history scan found two synthetic test fixtures. Exact finding fingerprints documented in `.gitleaksignore`; no broad test-directory exemption. Reviewed rerun: 27 commits scanned, zero remaining findings. |

## First GitHub CI result: EX-00 remains open

[CI run 42462733424](https://github.com/kowstav/gluons_app/actions/runs/42462733424), November 8, commit `0eb4d9b65e5c6668887894e1214768c6685cbc47`, completed **failure** in 3m45s. Python lint/tests, migrations, web typecheck/build/Playwright, secret scanning and Terraform validation for dev/staging/prod passed. All three Docker images built. Container runtime, real ERPNext and cloud deployment remain unverified.

| Failed job | Scanner evidence |
|---|---|
| Dependency audit | pip-audit reported `pytest 8.4.2`, `PYSEC-2026-1845`, suggested fix `9.0.3`. Summary says two findings but the table repeats the same package/advisory; do not count two distinct advisories. Subsequent npm audit was skipped. |
| API image | Debian 12.11: 46 findings, 39 high and 7 critical; includes apt, GnuTLS, OpenSSL and Perl packages. |
| Web image | OS: 24 findings, 22 high and 2 critical. Node packages: 30 findings, 29 high and 1 critical; includes brace-expansion, glob, minimatch and tar. |
| Sandbox ERP image | Debian target: 46 findings, 39 high and 7 critical. This is the simulator image, not ERPNext. |

These are scanner-reported package findings, not independent exploitability conclusions or unique CVE counts. Use the complete linked run logs for exact package paths and fixed versions; distinguish bundled Node tooling from application dependencies. Downstream SBOM steps did not complete after Trivy failures.

Agent A must first inspect the pytest advisory and plugin compatibility (current dev constraint is `<9`), make compatible range/lock changes, refresh pinned base-image digests and affected runtime packages, and inspect/remove unnecessary build tooling where supported. Run pip and npm audits independently, rebuild/scan all three images, retain SBOMs and exact digests, and rerun functional/migration checks plus container startup. Do not weaken scan thresholds or use broad exemptions. G0 closes only after passing CI. The documentation-only evidence follow-up uses `[skip ci]` to avoid rerunning unchanged inputs; the failed baseline remains explicitly open.

## Required acceptance scenarios

| ID | Scenario and required result | Owner / gate |
|---|---|---|
| AUTH-01 | New Google sign-in creates/resolves correct immutable issuer/subject identity; forged/expired/wrong-audience/replayed response rejected | B, G2 |
| AUTH-02 | Matching email from another identity provider never silently merges accounts; explicit authenticated linking required | B, G2 |
| AUTH-03 | Two businesses and two assistants: each token is bound to intended org; role downgrade/removal and token reuse enforced | B+G, G1/G2 |
| AUTH-04 | Interrupted outer assistant OAuth plus inner login resumes the correct request; state/nonce/PKCE/callback swapping rejected | B, G2 |
| AUTH-05 | Existing email/MFA, recovery, session revocation and owner succession continue working | B+G, every identity release |
| SETUP-01 | A setup URL alone cannot read private data, bind accounts, approve actions or select another user's org | C+G, G2 |
| SETUP-02 | Expired, reused, cross-browser/cross-org and forged provider callbacks fail safely; original durable plan remains recoverable | B+C+D, G2 |
| SETUP-03 | Passport import/share preview binds fields/provider/purpose/version; unconfirmed or changed data cannot be silently shared | C, G2 |
| SETUP-04 | Profile/setup data appears in export and is handled by deletion; retained audit fields have explicit policy | C+G, G2 |
| FLOW-01 | One authorized event produces a single external draft/order and independent matching readback | D, G1 |
| FLOW-02 | Deliver duplicate/out-of-order events at least ten times; verify one intended effect, durable dedupe and no multiplied charges | D+G, G1 |
| FLOW-03 | Crash before write, after write/before response, and after response/before projection; recover by lookup/reconciliation, not blind resend | D+G, G1 |
| FLOW-04 | Provider accepts write but readback is delayed/ambiguous; remain pending/manual review, never fabricate success or resend without proof | D+G, G1 |
| FLOW-05 | Revoke provider, role or policy; pause/cancel/kill switch during queued execution; no newly unauthorized dispatch occurs | A+D+G, G1 |
| FLOW-06 | Concurrent actions near budget ceiling cannot exceed the approved envelope; currencies/prices/version mismatches block | A+G, G1 |
| FLOW-07 | Provider outage/rate-limit/token expiry: bounded retry, visible next action, health/reconnect, recovery without duplicates | D, G1 |
| UI-01 | New and returning owner can connect, confirm identity, review exact grant, inspect receipt, pause and revoke on mobile/desktop | E+G, G2 |
| UI-02 | Close browser/chat midway; resume after sign-in/redirect without duplicate provider signup or loss of plan | E+G, G2 |
| UI-03 | Keyboard navigation, focus/error announcements, readable status, zoom and narrow width; owner can explain current authority | E+G, G2 |
| MCP-01 | Actual supported host discovers/authenticates tools, starts task, polls and pauses; expired/revoked token rejected | B, G1 |
| MCP-02 | Second host sees same authorized task after its own link; revoking first host leaves second independent | B+G, G2/G4 |
| COM-01 | REST/MCP/web readiness agree for missing, stale, sandbox, failed and revoked evidence | F+G, G3 |
| COM-02 | Pending KYC, incomplete shipping/tax evidence, unapproved catalog changes or missing checkout blocks go-live | F, G3 |
| COM-03 | Batch publication partial failure reports item receipts; retry skips proven effects and changed payload needs fresh approval | F+D, G3 |
| COM-04 | Carrier label, Shopify fulfillment record, dispatch and delivery remain distinct; no producer means dispatch gate stays blocked | F+D+G, G3 |
| OPS-01 | Clean image starts without local `.venv`/files, migrations run under intended roles, HTTPS callbacks and real SMTP work | A, G1 hosted pilot |
| OPS-02 | Worker restart/lost wake/outbox replay drains durable queue; multi-worker execution preserves action exclusion | A+G, G1 |
| OPS-03 | Backup restored into isolated environment; retained grants/receipts/audit intact; restored jobs fenced from live effects until reconciled | A+G, G4 |
| OPS-04 | Logs/exports/artifacts contain no provider tokens, passwords, raw authorization codes or unnecessary PII | A+G, every release |
| MODEL-01 | Unknown/failing model cannot run a gated skill; invalid output, injection and runaway retries trigger bounded fallback/human handoff | G, before model-backed activation |
| SKILL-01 | Draft-only contract refuses activation; new skill needs graph, action allowlist, domain cases, expert review and real provider evidence | G+D, G5 |

Do not satisfy all these rows with one giant UI test. Use focused service/security tests, concurrency/fault-injection integration tests and a small number of genuine owner browser journeys. Real-provider tests must be explicitly separated and require a safe configured account and authorized action scope.

## Evidence record

For every gate, record commit SHA; application/skill/model/provider API versions; timestamp; environment; masked account/resource identifiers; input fixture; expected result; actual receipt and independent query; assertions; logs/artifact reference; reviewer; known gaps. Store sensitive raw provider results in approved private evidence storage, not GitHub Actions artifacts by default. Commit only redacted summaries with stable references.

## Proposed initial release thresholds

These are proposed acceptance targets, not observed performance or universal guarantees. Confirm them before starting the experiment; do not change thresholds afterward to hide failures.

- All unauthorized/cross-tenant/approval-bypass tests fail closed; any actual unauthorized effect blocks release.
- Zero duplicate externally confirmed effects in the defined retry/crash test matrix.
- All provider writes shown as complete have a matching verified receipt; ambiguous outcomes remain explicit.
- Initial supervised pilot: at least 50 representative events over at least seven days, with >=95% reaching either the correct verified result or the correct explicit human exception within the agreed service window. Report successful autonomous completion separately so escalation cannot inflate automation success. Small samples do not establish production reliability.
- Every owner in an initial 3–5 person usability trial can identify the selected business, current automatic authority and stop control without coaching; report counts, task durations and all failures.
- A defined retry/model budget, queue-lag threshold and owner escalation route exist before unattended processing. Set numerical latency/cost targets from the selected workflow's baseline rather than inventing a universal number.

## Release process

1. Integrator verifies dependency-ready package, current base and scope.
2. Run relevant checks and the gate matrix; preserve evidence class.
3. Build immutable images; record digests, migrations and configuration version.
4. Prepare exact staging deployment plan/cost envelope and obtain existing required owner authorization before provisioning; do not interpret this document as approval to spend.
5. Use synthetic/non-sensitive fixtures and explicitly authorized sandbox accounts first.
6. Rehearse restore and incident/kill-switch paths; test logs and alerts actually arrive.
7. Canary one authorized tenant/workflow with bounded volume and supervision.
8. Reconcile receipts, costs and exceptions; expand only after the gate passes.
9. On regression: disable affected capability, fence jobs, preserve receipts, reconcile in-flight operations and roll back compatible code/skill version. Prefer forward schema repair for destructive migrations; never assume database downgrade safely undoes real-world effects.
