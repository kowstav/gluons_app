# Everything in one Gluons environment. Each root in environments/ calls this once, with its
# own project and state, so staging and production can never share a resource or a secret.
#
# Nothing here has been applied. `terraform apply` creates chargeable resources and needs the
# owner's explicit approval. See docs/DEPLOYMENT_STATUS.md.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

locals {
  prefix = "gluons-${var.environment}"

  # Blueprint doc 07 also lists Cloud Build, Workflows and Identity Platform. They stay off:
  # CI builds in GitHub Actions, ADR-002 replaces Workflows, and Gluons runs its own identity.
  services = toset([
    "run.googleapis.com", "sqladmin.googleapis.com", "servicenetworking.googleapis.com",
    "compute.googleapis.com", "secretmanager.googleapis.com", "cloudkms.googleapis.com",
    "cloudtasks.googleapis.com", "pubsub.googleapis.com", "cloudscheduler.googleapis.com",
    "artifactregistry.googleapis.com", "monitoring.googleapis.com", "logging.googleapis.com",
    "billingbudgets.googleapis.com", "aiplatform.googleapis.com", "iam.googleapis.com",
    "iamcredentials.googleapis.com", "sts.googleapis.com", "cloudtrace.googleapis.com",
    "gmail.googleapis.com",
  ])

  load_balancer_on = var.deploy_services && var.enable_load_balancer

  api_url = var.api_host != "" ? "https://${var.api_host}" : var.api_url
  web_url = var.web_host != "" ? "https://${var.web_host}" : var.web_url

  worker_wake_audience = "https://${local.prefix}-worker.gluons.internal/wake"
  gmail_push_audience  = "https://${local.prefix}-webhooks.gluons.internal${var.gmail_push_path}"

  runtime_env = merge(var.plain_env, {
    # config.py knows only staging and production as hosted, so dev runs with staging rules.
    GLUONS_ENV            = var.environment == "dev" ? "staging" : var.environment
    GLUONS_PUBLIC_API_URL = local.api_url
    GLUONS_PUBLIC_WEB_URL = local.web_url
    # The external Application Load Balancer appends "<client>, <load balancer>" to
    # X-Forwarded-For. Confirm the logged client address on first deploy (deployment runbook).
    GLUONS_TRUSTED_PROXY_HOPS         = "2"
    GLUONS_ALLOWED_WEB_ORIGINS        = local.web_url
    GLUONS_GCP_PROJECT_ID             = var.project_id
    GLUONS_GCP_REGION                 = var.region
    GLUONS_KMS_KEY_NAME               = module.security.kms_key_id
    GLUONS_ARTIFACTS_BUCKET           = module.storage.evidence_bucket
    GLUONS_PUBSUB_EVENTS_TOPIC        = module.messaging.topic_ids["state-events"]
    GLUONS_MAIL_TRANSPORT             = "smtp"
    GLUONS_GMAIL_PUBSUB_TOPIC         = module.messaging.gmail_topic_id
    GLUONS_GMAIL_PUSH_AUDIENCE        = local.gmail_push_audience
    GLUONS_GMAIL_PUSH_SERVICE_ACCOUNT = module.identities.emails["pubsub-push"]
    GLUONS_LOG_FORMAT                 = "json"
  })
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_service" "required" {
  for_each           = local.services
  service            = each.value
  disable_on_destroy = false
}

module "identities" {
  source      = "../identities"
  environment = var.environment
  depends_on  = [google_project_service.required]
}

module "network" {
  source     = "../network"
  prefix     = local.prefix
  region     = var.region
  depends_on = [google_project_service.required]
}

module "database" {
  source                = "../database"
  prefix                = local.prefix
  region                = var.region
  network_id            = module.network.network_id
  tier                  = var.database_tier
  high_availability     = var.database_high_availability
  disk_size_gb          = var.database_disk_size_gb
  backup_retained_count = var.backup_retained_count
  log_retention_days    = var.log_retention_days
  deletion_protection   = var.deletion_protection
  depends_on            = [module.network]
}

module "security" {
  source            = "../security"
  prefix            = local.prefix
  region            = var.region
  service_accounts  = module.identities.emails
  populated_secrets = var.populated_secrets
  depends_on        = [google_project_service.required]
}

module "storage" {
  source                  = "../storage"
  project_id              = var.project_id
  prefix                  = local.prefix
  region                  = var.region
  service_accounts        = module.identities.emails
  evidence_retention_days = var.evidence_retention_days
  export_retention_days   = var.export_retention_days
  depends_on              = [google_project_service.required]
}

module "messaging" {
  source              = "../messaging"
  prefix              = local.prefix
  region              = var.region
  project_number      = data.google_project.current.number
  service_accounts    = module.identities.emails
  service_account_ids = module.identities.ids
  enable_gmail_push   = var.deploy_services
  gmail_push_endpoint = var.deploy_services ? "${module.services.backend_urls["webhooks"]}${var.gmail_push_path}" : ""
  gmail_push_audience = local.gmail_push_audience
  depends_on          = [google_project_service.required]
}

module "services" {
  source                   = "../services"
  project_id               = var.project_id
  region                   = var.region
  prefix                   = local.prefix
  environment              = var.environment
  deploy_services          = var.deploy_services
  api_image                = var.api_image
  web_image                = var.web_image
  public_ingress           = local.load_balancer_on ? "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" : "INGRESS_TRAFFIC_ALL"
  network_name             = module.network.network_name
  subnetwork_name          = module.network.subnetwork_name
  service_accounts         = module.identities.emails
  service_account_ids      = module.identities.ids
  runtime_env              = local.runtime_env
  secret_env               = module.security.mounted_env
  max_instances            = var.max_instances
  min_api_instances        = var.min_api_instances
  worker_invoker_iam_check = var.worker_invoker_iam_check
  worker_wake_audience     = local.worker_wake_audience
  worker_wake_schedule     = var.worker_wake_schedule
  depends_on               = [module.database, module.security, module.storage]
}

module "edge" {
  count                 = local.load_balancer_on ? 1 : 0
  source                = "../edge"
  prefix                = local.prefix
  region                = var.region
  api_host              = var.api_host
  web_host              = var.web_host
  rate_limit_per_minute = var.rate_limit_per_minute
  service_names = {
    api      = module.services.backend_names["api"]
    mcp      = module.services.backend_names["mcp"]
    webhooks = module.services.backend_names["webhooks"]
    web      = module.services.web_service_name
  }
}

module "ci_identity" {
  count                       = var.github_repository != "" ? 1 : 0
  source                      = "../ci_identity"
  github_repository           = var.github_repository
  github_environment          = var.environment
  deployer_service_account_id = module.identities.ids["deployer"]
  depends_on                  = [google_project_service.required]
}

module "observability" {
  source                = "../observability"
  project_id            = var.project_id
  project_number        = data.google_project.current.number
  prefix                = local.prefix
  billing_account       = var.billing_account
  monthly_budget_usd    = var.monthly_budget_usd
  alert_email           = var.alert_email
  notification_channels = var.notification_channels
  api_host              = var.api_host
  web_host              = var.web_host
  deploy_services       = var.deploy_services
  depends_on            = [google_project_service.required]
}

# Vertex AI has no per-model IAM, so this is the one project-level grant in the stack. It is
# off until Gemini is chosen, and goes only to the workloads that run models.
resource "google_project_iam_member" "vertex_user" {
  for_each = var.enable_vertex_ai ? toset(["worker", "evaluator"]) : toset([])
  project  = var.project_id
  role     = "roles/aiplatform.user"
  member   = "serviceAccount:${module.identities.emails[each.value]}"
}
