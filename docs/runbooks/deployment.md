# Deployment

The [deployment status](../DEPLOYMENT_STATUS.md) records which checks have been exercised. Provision and deploy only against an explicitly approved environment and resource plan.

## Local staging access

Record the real staging project ID, region, and HTTPS origins in the local `.gluons-data/gcp/staging-access.env` file. The file is excluded from Git and Docker contexts. If the operator uses an ADC-compatible credential JSON, set `GOOGLE_APPLICATION_CREDENTIALS` to its absolute path. With user ADC, leave it blank and authenticate through the Google Cloud CLI on the workstation. Use the authenticated principal to check the selected project before preparing a cloud plan. GitHub deployment authenticates separately with Workload Identity Federation. [Application Default Credentials](https://cloud.google.com/docs/authentication/application-default-credentials)

## First environment

1. Create one Google Cloud project per environment, and a state bucket per project.
2. Copy `infra/terraform/environments/<env>/terraform.tfvars.example` to `terraform.tfvars`. It is git-ignored and holds no secrets.
3. Add secret versions to Secret Manager outside Terraform. `.env.example` lists every setting and says which ones are secrets.
4. From `infra/terraform/environments/<env>`, run `terraform init` with the backend configured, then `terraform plan`. Read the plan, then apply it.
5. Bootstrap the `gluons_app` and `gluons_system` database login roles. Migrations run as a third role, and that secret never reaches the API or worker.
6. Build images from the pinned Dockerfiles and push them to Artifact Registry.
7. Run the migration job, then the skills job.
8. Check `/ready`, then confirm that every table in `TENANT_TABLES` shows forced row level security in `pg_class` on the new database.
9. Unpause the worker scheduler last.
10. Sign in once and check the logged client address against your own. If every request shows the load balancer's address, `GLUONS_TRUSTED_PROXY_HOPS` is wrong, and the per-address limits would treat all users as one.
11. Register the provider URLs in `docs/ACCOUNT_CONNECTIONS.md` with Google, Shopify, Meta and ElevenLabs.

## Credit-limited staging pilot

Set `credit_limited_pilot = true` in staging. For the first foundation apply, use `deploy_services = false` and `pilot_database_running = true` so Terraform can create the database while Cloud SQL is active. The profile uses a zonal `db-g1-small` in `us-central1`, 10 GiB of SSD with automatic growth disabled, two retained backups, and two days of transaction logs. Keep the bootstrap identity restricted to staging. Keep `worker_invoker_iam_check` enabled and leave the external load balancer, custom hosts, and Terraform-managed billing budget unset.

Before applying, record the approved run window, spending target, and teardown time. Require green build and scan checks on GitHub before starting the database window. After the foundation apply, use the `gluons.public_origins` output for the GitHub staging environment variables. Dispatch `Deploy` from `main` with `staging_images_only` for the first image publish. Use the scanned digests in the run summary to plan the initial services. The release builds the staging web image with the API origin and enables same-origin browser requests when the web origin ends in `.run.app`. This keeps session and CSRF cookies on the owner web origin. The assistant endpoint remains `/mcp` on the API origin. During the service phase, each Cloud Run revision scales to zero with a maximum of one instance. Keep the scheduler paused until testing worker wake and confirm the source address attribution before testing sign-in limits. Direct Cloud Run ingress starts with forwarded client address trust disabled.

At the end of the test window, pause the worker scheduler and stop the database. Confirm its activation policy is `NEVER` and keep `pilot_database_running = false` when reviewing later plans. Stopping Cloud SQL suspends compute charges while retained storage can still accrue charges. Review teardown of disposable services, images, database storage, and retained backups against the approval. Keep the Terraform state bucket through teardown so it can be verified. [Cloud SQL start and stop](https://docs.cloud.google.com/sql/docs/postgres/start-stop-restart-instance) · [Cloud Run HTTPS URLs](https://docs.cloud.google.com/run/docs/triggering/https-request)

## Later releases

Dispatch `Deploy` from `main` after its exact commit passes push CI. Both application images are scanned before migrations. Keep migrations compatible with the current release, especially while the canary sends traffic to old and new code. Set up the GitHub `production` environment's required reviewers before enabling production deployment.

Staging shifts all traffic to the named ready revisions and checks the API and web public routes. Verify assistant OAuth, owner sign-in mail, worker wake and recovery, and restore separately in staging. On explicit production confirmation and environment approval, the workflow shifts 10% to the named candidate revisions. Public smoke checks on a mixed traffic split can confirm that the public routes still answer. Candidate-specific telemetry and workflow evidence are required before authorizing a further traffic change.

After a successful canary, use the revision names in the workflow log for an explicitly authorized promotion. Confirm that they are still the intended revisions and check their error, latency, worker, and receipt metrics. For each service, promote by exact name with `gcloud run services update-traffic SERVICE --project PROJECT --region REGION --to-revisions REVISION=100`. Keep the previous splits from the workflow log for recovery.

## Rollback

The workflow attempts to restore and verify the previous traffic split for each service it touched if the traffic update or smoke test fails. If the runner is interrupted or restoration fails, use the exact previous `REVISION=percent` pairs logged before the shift and verify the resulting split for every affected service. A traffic restore leaves database migrations in place. Repair an incompatible migration with a forward change.

Budget alerts are notifications, not a spending cap.
