# Deployment status

## Credit-limited staging plan on November 24, 2026

Read-only Google Cloud checks confirmed the staging account's bootstrap and federation permissions and access to the versioned Terraform state bucket. Terraform validated dev, staging, and production. The live-state plan proposed 183 foundation additions and a service preview proposed 203 total additions. Both proposed zero modifications or deletions. The service preview used placeholder image tags. It verified scale-to-zero settings, private worker IAM, and a paused scheduler. The foundation uses a private `db-g1-small` with a fixed 10 GiB SSD. Approval of the exact low-cost plan and image evidence are pending. No cloud resources were applied. [Deployment runbook](runbooks/deployment.md#credit-limited-staging-pilot)

## Release candidate check on November 21, 2026

The chat-first and inventory trial changes are on the local `codex/ex05-chat-first-setup` branch. The latest successful push CI found on GitHub is for `main` at `16dd6ac`. The repository Actions history shows zero manual workflow runs in the current check. This is evidence about this repository's workflow history. [Main CI](https://github.com/kowstav/gluons_app/actions/runs/42960424970)

Local release preparation updated the Python dependency pins flagged by the separate redesign branch and the web framework patch. The release workflow now requires green push CI for the exact `main` commit, scans both API and web images, restores previous traffic splits if rollout checks fail, and holds production at 10% pending candidate-specific review. The rollout behavior is exercised locally against simulated Cloud Run responses. [Redesign branch CI](https://github.com/kowstav/gluons_app/actions/runs/44881583990) · [Deployment runbook](runbooks/deployment.md)

Hosted sign-off still needs an approved staging project and exact resource plan, deployed image evidence, real TLS and OAuth callbacks, SMTP delivery, worker recovery, alert delivery, and an isolated restore. The fictional pilot proves local behavior. The live workflow gate needs a real consenting test account, explicit authority, and independent provider readback.

## Historical snapshot on November 7, 2026

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
