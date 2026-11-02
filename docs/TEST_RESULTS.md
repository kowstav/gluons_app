# Test results

Results are recorded from actual runs, newest first. Provider-double tests are labelled as such and prove Gluons' handling only, not the provider integration.

## Integrated build (2026-11-02)

All runs are from one commit after merging the agent branches with the GPT checkpoint.

| Check | Result |
|---|---|
| Backend suite, real embedded PostgreSQL as the restricted role, real HTTP ERP simulator | **674 passed**, 0 failed, 0 skipped, 488 s |
| Playwright against the real API and PostgreSQL, desktop Chromium and Pixel 7 | **2 passed**, 72 s |
| `next build` and `tsc --noEmit` | Passed |
| Ruff across `apps/api` | Passed |
| Traceability | 83 requirements, 0 problems: 10 done, 63 partial, 10 blocked, 0 not started |
| `terraform fmt -check` and `validate` for dev, staging and prod | Passed offline |
| `docker compose config --quiet` | Passed. No image was built. |

The browser tests now also cover the orders and messages panels, a real paged data download and the assistant over `/v1/chat`.

Before merging, GPT's tree passed 382 tests alone. The first merged run had 2 failures, and later runs found more. None were hidden or skipped:

- **Real defect:** the gas hazard pattern missed the plain phrase "smell gas". Fixed.
- **Real defect:** owner consent to an assist session accepted a principal with no browser session. It now requires one.
- **Stale expectations:** tests that assumed a representative could create automations, and tests that called consent without a session, were updated to the stricter rules.
- **Test isolation:** WhatsApp signup tests shared one phone number, which links globally. Each now has its own.
- **Missing settings:** two new settings were absent from `.env.example`, caught by `test_every_setting_is_documented`.

A shared test cluster left dirty by an earlier interrupted run failed to start in time, so runs used fresh clusters through `GLUONS_TEST_CLUSTER`.

Provider tests use labelled doubles. No real Gmail, Meta, ElevenLabs, ERPNext, Shopify, payment or model provider has been exercised.

## Continuation checkpoint (2026-11-02)

- Integrated backend suite: **269 passed**, exit 0. Real embedded PostgreSQL,
  restricted tenant role and independent HTTP ERP simulator. Includes identity,
  policy, concurrency, restart recovery, OAuth/MCP, catalog, launch and model tests.
- Added Gmail HTTP adapter contracts: **2 passed**, exit 0. These pure provider-double
  tests require no database. They prove guarded writes, independent Sent readback,
  recipient mismatch rejection and no blind resend after ambiguous lookup.
- **271 backend tests collected** after adding those two contracts.
- Next.js production compilation, TypeScript and standalone output: passed.
- Playwright against isolated real API/PostgreSQL: **2 passed** (desktop Chromium
  and Pixel 7 mobile), 46.5 seconds. Real registration/file-mail verification,
  organization creation, skill discovery, shadow trial, pause/resume/cancel,
  knowledge, budget, connection configuration status and audit flows. No mocked UI.
- Ruff across `apps/api`: passed.
- Traceability: **83 requirements, 0 validation problems**. Partial status is retained
  where full blueprint functionality or provider evidence is missing.
- `docker compose config --quiet`: passed. Containers were not built/run.
- Terraform **1.14.0**, checksum verified against HashiCorp's release manifest;
  Google provider **7.46.2**, signed and locked: `fmt`, `init -backend=false`,
  and `validate` passed. No plan/apply or cloud deployment performed.

Initial continuation runs found an expired-approval gate bug and same-key
receipt/audit lock inversion; both were fixed and covered by the passing backend
suite. Two inherited tests were repaired: the persistent-outage test now injects
its single-use failure before each execution path, and maker-checker uses an
approver role so it reaches that check instead of failing earlier on permissions.

The desktop sandbox denies loopback for PostgreSQL and Terraform's local provider
plugin. Database/browser/schema tests were run with local process/network access.
A redundant later PostgreSQL startup collided in the shared test cluster; the
two pure Gmail adapter tests were isolated from that unnecessary database fixture
and passed. No failed database startup is counted as an integration pass.

No real Gmail, Meta, ElevenLabs, ERPNext, Shopify, payment or model-provider sandbox
verification is claimed. Model/Google HTTP doubles validate Gluons contracts only.

## Baseline before any implementation (2026-11-02)
Blueprint package, Python 3.13.3 on Windows:

- `pytest` in `gluons_blueprint/`: 10 passed, 1 failed. The failure is `test_permission_spec_has_all_four_layers`, caused by the test reading a UTF-8 file with the Windows default encoding, not by the specification.
- `validate_pack.py`: passed (31 skills, 31 x 6 scenario manifests).
- `scripts/audit_repo_registry.py`: passed (24 candidates, 31 mappings; no upstream code audited).
- The reference API and demo were simulations with no real authentication, persistence or integrations.
