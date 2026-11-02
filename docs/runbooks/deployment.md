# Deployment

Nothing here has been run against Google Cloud yet. Each step needs the owner's approval because it creates chargeable resources. `docs/DEPLOYMENT_STATUS.md` records what has been verified.

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

## Later releases

Run the `Deploy` workflow by hand. It never runs on push, and production sits behind the GitHub `production` environment's required reviewers. Apply migrations before releasing code that needs them, and keep each migration compatible with the previous release so a rollback is a redeploy.

## Rollback

Redeploy the previous image digest. Migrations are not reversed in production. If a migration is at fault, write a new one.

Budget alerts are notifications, not a spending cap.
