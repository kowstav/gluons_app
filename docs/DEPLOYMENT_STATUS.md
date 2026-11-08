# Deployment status — 2026-11-07

> November 8: the table below is historical. Private `kowstav/gluons_app` is published; first CI built three images and passed functional/Terraform checks, but dependency/image security scans failed. Container runtime and deployment remain unverified. See the [current verification ledger](execution/06_VERIFICATION_AND_RELEASE.md).

Nothing is deployed. No cloud resources exist, no paid provider action was taken, and no real credentials were used.

| Check | State |
|---|---|
| `terraform fmt -check` and `validate` for dev, staging and prod | Passed with Terraform 1.14.0 and Google provider 7.46.2 |
| Provider locks | Committed per environment, with hashes for Linux, Windows and macOS |
| `terraform plan` or `apply` | Never run. Needs a project, credentials and owner approval. |
| Docker image builds | Never run. Docker Desktop did not start unattended on this machine. |
| `docker compose config` | Passed earlier, on the Compose file before the operations merge |
| GitHub Actions | Never run. There is no GitHub remote. Deploy is manual only. |
| Migrations | Applied on embedded PostgreSQL in every test run, never on Cloud SQL |
| Backups, restore, alerts | Configured in Terraform, never exercised |

Validation proves the configuration is well formed. It does not prove permissions, quotas, connectivity or a working application.

Terraform defines private Cloud SQL and VPC, Cloud Run API, web and worker, migration and skill jobs, a paused scheduler, KMS, Secret Manager references, private evidence storage, Pub/Sub, Cloud Tasks, Cloud Armor and alerts. Each environment has its own identities with least privilege. Follow `docs/runbooks/deployment.md`.

Budget alerts notify. They do not cap spending.

Cloud SQL has no superuser, so migration 0007 gives the worker explicit row level security policies and removes `BYPASSRLS`. Tenant isolation stays forced for the application role. Sources: [Cloud SQL role restrictions](https://docs.cloud.google.com/sql/docs/postgres/users), [scheduled Cloud Run jobs](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule).
