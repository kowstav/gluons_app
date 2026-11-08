# Agent A — integration and release

Read the package index, audit, execution plan, working agreement, `CLAUDE.md`, `docs/ARCHITECTURE_DECISIONS.md`, CI/deployment workflows and runbooks. Your first package is EX-00, followed by shared schema integration and EX-09. You coordinate integration; do not rebuild other agents' features.

## Deliverables

1. Baseline ledger tied to HEAD, clean-checkout reproduction and GitHub CI triage. Reconcile local/CI toolchain versions and pinned action/image/package availability without blanket upgrades.
2. A current interface/schema ownership ledger with reserved next migration and one Alembic head. Review typed profile/setup/identity additions, RLS/grants, backfill, export/deletion and retention.
3. Shared config/`.env.example` and dependency changes required by accepted packages.
4. Built API/web/worker images, real Compose smoke evidence, then an exact staging deployment plan with resource/cost/identity details. Execute cloud changes only under owner authorization.
5. Hosted callback/TLS/CORS/proxy/SMTP, worker wake/outbox/restart, metrics/alerts and backup/restore evidence.

## Files

Own shared files listed in the working agreement: migrations/models/session policy, config/locks, CI, Docker, Terraform and central status docs. Consume focused patches from B/C/D/F rather than racing them. The current migration head is `0010` at the audited baseline; discover actual HEAD before reserving the next number.

## Acceptance

- CI jobs exercise the intended restricted Postgres role and actual API browser fixtures; builds do not rely on this machine's installed files.
- Credentials and the private founder context remain excluded from source/history/artifacts.
- Migration upgrade on empty and populated data works; single head and schema consistency checked. Never silently rewrite old migrations for new behavior.
- Staging tests validate real IAM/network/callback behavior; `terraform validate` alone is not enough.
- Restore into an isolated environment preserves audit/receipt integrity and prevents restored jobs from issuing duplicate live writes before reconciliation.
- Review `OPS-01` through `OPS-04`, `FLOW-05/06` and changed schema cases in the verification guide.

## First steps and boundaries

Publication is complete. Begin with the first CI failures documented in [verification](../06_VERIFICATION_AND_RELEASE.md): pytest advisory/constraint compatibility, patched base images/OS packages, web-image Node tooling/dependencies, and independently executed npm audit. All three images build, but scans fail and SBOM steps do not complete. Preserve thresholds, make tested fixes and rerun full CI before closing EX-00/G0. Then prove container runtime.

Refresh the audit ledger and GitHub state. Resolve actual failures in small commits with evidence. Do not disable secret/dependency scans or loosen tenancy to get green CI. Do not provision all environments before one staging deployment is proved. Do not merge the older `kowstav/gluons` runtime into this repository. Keep deployment manual and separately authorized. Handoff exact image/commit/migration/config versions and remaining gates.
