# Deployment status

## Hosted staging release and email delivery on November 26, 2026

GitHub built and scanned the staging images, ran the database migration and skill sync, and verified public readiness. Cloud Run confirmed the intended release on all five staging services. The rollout checks the exact candidate revision before shifting traffic. [Staging release](https://github.com/kowstav/gluons_app/actions/runs/45626818067) · [Rollout correction](https://github.com/kowstav/gluons_app/pull/22)

Staging accepted one sign-in code request through the owner site's same-origin route. Gmail accepted the message and the pilot owner confirmed it reached their inbox. A request for an address outside the approved list returned HTTP 403, and unauthenticated MCP still returned HTTP 401. Browser code redemption, real-device passkey approval, and OAuth through an actual assistant host still need verification. [Account mail runbook](runbooks/secrets_and_webhooks.md#staging-account-mail)

Production readiness also requires alert delivery, an isolated backup restore, and a consenting business trial with independently verified provider results.

## Account and spending release candidate on November 24, 2026

Email-code sign-in and passkey confirmation for eligible unattended spending allowances are merged. GitHub tested email code delivery through the file outbox, browser-bound one-time verification, and passkey enrollment and policy signing with Chromium's virtual authenticator on desktop and mobile. These changes still need a hosted SMTP and real-device test. [Email sign-in CI](https://github.com/kowstav/gluons_app/actions/runs/45411523731) · [Spending passkey CI](https://github.com/kowstav/gluons_app/actions/runs/45414930573)

At that checkpoint, staging still ran the earlier candidate when its database was active. Staging database activation was verified as `NEVER` after the pilot. Hosted owner sign-in and passkey testing were pending. [Account mail runbook](runbooks/secrets_and_webhooks.md#staging-account-mail)

## Credit-limited staging pilot on November 24, 2026

The approved pilot provisioned a private `db-g1-small` with a fixed 10 GiB SSD, a scoped GitHub federation identity, scale-to-zero Cloud Run services, and a private worker. Cloud SQL rejected the first retention settings. The corrected retry used two backups and two days of transaction logs and completed the eight remaining foundation resources. [Retention correction](https://github.com/kowstav/gluons_app/pull/16) · [Deployment runbook](runbooks/deployment.md#credit-limited-staging-pilot)

GitHub passed CI on the app release commit and built and scanned both published staging images. Terraform deployed their immutable digests. On Cloud Run, the PostgreSQL migration and skill synchronization jobs each succeeded. Hosted checks passed for PostgreSQL readiness, OAuth discovery, the MCP authorization boundary, and the same-origin web session route. One authenticated worker wake succeeded, and the recurring schedule was paused afterward. An automated backup was successful. The pilot database was stopped and its `NEVER` activation policy verified. [Release CI](https://github.com/kowstav/gluons_app/actions/runs/45351947684) · [Staging image scans](https://github.com/kowstav/gluons_app/actions/runs/45354270489)

Live owner sign-in delivery, an assistant authorization through a real host, a consenting business workflow with provider readback, alert delivery, and an isolated restore remain unverified. Production was outside the approved staging pilot.

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
