# Deployment status — 2026-11-02

No cloud resources provisioned, no production deployment and no paid provider
actions performed by this continuation.

Terraform CLI validation passed with checksum-verified Terraform 1.14.0 and signed
Google provider 7.46.2. The provider dependency lock is committed. This verifies
configuration syntax/schema; it does not verify GCP permissions, credentials,
connectivity, migration execution, provider quotas or a deployed application.

- Local API/worker/PostgreSQL and owner app are exercised through tests.
- `docker compose config --quiet` passes. Docker image builds/container runtime
  have not yet been verified; do not treat the manifest as a deployment receipt.
- `infra/terraform` defines private Cloud SQL/VPC, Cloud Run API/web and worker,
  migration/skill jobs, paused Scheduler, KMS, secret references, private evidence
  storage, Pub/Sub, Cloud Tasks and alerts. Final CLI validation is recorded below
  when available. Cloud Tasks wake delivery is not wired; PostgreSQL remains the
  queue authority and scheduled worker jobs provide the reconciliation path.
- No Terraform backend/state, provider credentials, domain, secrets or image
  artifacts have been configured for a real environment.

Before any approved staging deployment: validate infrastructure; use independent
project/state per environment; populate Secret Manager outside Terraform; build
scanned images; bootstrap restricted database login roles; run migration and skill
jobs; verify readiness/RLS; then unpause worker scheduling. Migration secrets must
not be injected into the API or worker. Budget alerts are notifications, not a
hard cloud-spend cap.

Cloud SQL does not expose superuser accounts. Migration 0007 grants explicit
worker RLS policies and removes legacy BYPASSRLS. Application tenant isolation
remains forced. Sources: [Cloud SQL role restrictions](https://docs.cloud.google.com/sql/docs/postgres/users),
[scheduled Cloud Run jobs](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule).
