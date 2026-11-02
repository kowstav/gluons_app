# Test results

Results are recorded from actual runs, newest first. Provider-double tests are labelled as such and prove Gluons' handling only, not the provider integration.

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
