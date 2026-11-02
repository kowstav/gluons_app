# Cloud Run services and jobs.
#
# Exposure, from docs/research/mcp_hosts_google.md sections 8 and 9:
#
#   api, mcp, webhooks, web   Public. The invoker IAM check is OFF because ChatGPT, Claude
#                             and provider webhooks send their own bearer tokens or
#                             signatures, which are not Google ID tokens. The application
#                             authenticates every request. invoker_iam_disabled replaces an
#                             allUsers grant, which domain-restricted sharing can block.
#   worker                    Private. Internal ingress, and only gluons-scheduler holds
#                             roles/run.invoker. Cloud Tasks, Cloud Scheduler and Pub/Sub
#                             reach it because they sit in this project and use the default
#                             run.app URL.
#
# OPEN TEST: the wake endpoint also verifies the OIDC token in the application. Google
# documents signature removal only for X-Serverless-Authorization. Whether the signature
# survives on `Authorization` once the IAM check has run is UNVERIFIED, and the research note
# warns not to rely on it untested. If a staging wake returns 401 with reason
# `bad_token`, set worker_invoker_iam_check = false. The worker then stays unreachable from
# the internet through internal ingress, and the application still verifies signature,
# audience, issuer and service account. See docs/DEPLOYMENT_STATUS.md.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

variable "project_id" { type = string }
variable "region" { type = string }
variable "prefix" { type = string }
variable "environment" { type = string }
variable "deploy_services" { type = bool }
variable "api_image" { type = string }
variable "web_image" { type = string }
variable "public_ingress" {
  type        = string
  description = "INGRESS_TRAFFIC_ALL, or INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER once the load balancer exists, so Cloud Armor cannot be bypassed through run.app."
}
variable "network_name" { type = string }
variable "subnetwork_name" { type = string }
variable "service_accounts" { type = map(string) }
variable "service_account_ids" { type = map(string) }
variable "runtime_env" {
  type        = map(string)
  description = "Plain settings shared by every backend workload."
}
variable "secret_env" {
  type        = map(map(string))
  description = "Env var to Secret Manager secret id, by workload."
}
variable "max_instances" { type = number }
variable "min_api_instances" { type = number }
variable "worker_invoker_iam_check" { type = bool }
variable "worker_wake_audience" { type = string }
variable "worker_wake_schedule" { type = string }

locals {
  backends = {
    api      = { public = true, timeout = "60s", concurrency = 20, memory = "1Gi", min = var.min_api_instances }
    mcp      = { public = true, timeout = "300s", concurrency = 20, memory = "1Gi", min = 0 }
    webhooks = { public = true, timeout = "30s", concurrency = 20, memory = "512Mi", min = 0 }
    # One request per instance: a wake holds a whole drain, and concurrent wakes add nothing
    # that another instance would not do. Claims use SKIP LOCKED, so overlap stays safe.
    worker = { public = false, timeout = "900s", concurrency = 1, memory = "1Gi", min = 0 }
  }

  # Deterministic Cloud Run URL. Using service.uri here would make the worker's environment
  # depend on the worker itself.
  worker_url = "https://${var.prefix}-worker-${data.google_project.current.number}.${var.region}.run.app"
  wake_url   = "${local.worker_url}/internal/worker/wake"

  worker_only_env = {
    GLUONS_WORKER_WAKE_URL        = local.wake_url
    GLUONS_WORKER_WAKE_AUDIENCE   = var.worker_wake_audience
    GLUONS_WORKER_SERVICE_ACCOUNT = var.service_accounts["scheduler"]
  }

  jobs = {
    migrate = { account = "migrator", args = ["migrate"] }
    skills  = { account = "worker", args = ["skills-sync"] }
  }

  deployed_backends = var.deploy_services ? local.backends : {}
  deployed_jobs     = var.deploy_services ? local.jobs : {}
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_cloud_run_v2_service" "backend" {
  for_each             = local.deployed_backends
  name                 = "${var.prefix}-${each.key}"
  location             = var.region
  deletion_protection  = var.environment == "production"
  ingress              = each.value.public ? var.public_ingress : "INGRESS_TRAFFIC_INTERNAL_ONLY"
  invoker_iam_disabled = each.value.public ? true : !var.worker_invoker_iam_check
  custom_audiences     = each.key == "worker" ? [var.worker_wake_audience] : []

  template {
    service_account                  = var.service_accounts[each.key]
    timeout                          = each.value.timeout
    max_instance_request_concurrency = each.value.concurrency

    scaling {
      min_instance_count = each.value.min
      max_instance_count = var.max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"

      network_interfaces {
        network    = var.network_name
        subnetwork = var.subnetwork_name
      }
    }

    containers {
      image = var.api_image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = each.value.memory
        }
      }

      dynamic "env" {
        for_each = merge(var.runtime_env, each.key == "worker" ? local.worker_only_env : {})
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.secret_env[each.key]
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 20

        http_get {
          path = "/health"
          port = 8000
        }
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8000
        }
      }
    }
  }

  lifecycle {
    # CI rolls images through gcloud so a deploy never needs write access to Terraform state.
    ignore_changes = [template[0].containers[0].image, client, client_version]

    precondition {
      condition     = var.api_image != ""
      error_message = "deploy_services needs api_image."
    }
    precondition {
      condition     = startswith(var.runtime_env["GLUONS_PUBLIC_API_URL"], "https://") && startswith(var.runtime_env["GLUONS_PUBLIC_WEB_URL"], "https://")
      error_message = "Hosted environments refuse to boot without https public URLs (config.py)."
    }
  }
}

resource "google_cloud_run_v2_service" "web" {
  count                = var.deploy_services ? 1 : 0
  name                 = "${var.prefix}-web"
  location             = var.region
  deletion_protection  = var.environment == "production"
  ingress              = var.public_ingress
  invoker_iam_disabled = true

  template {
    # A dedicated empty identity. Omitting this would run on the Compute default account,
    # which is commonly a project Editor.
    service_account = var.service_accounts["web"]

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    containers {
      image = var.web_image

      ports {
        container_port = 3000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      # No environment. NEXT_PUBLIC_API_URL is compiled into the bundle, so CI builds one
      # web image per environment instead of promoting a single digest.
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]

    precondition {
      condition     = var.web_image != ""
      error_message = "deploy_services needs web_image."
    }
  }
}

resource "google_cloud_run_v2_job" "job" {
  for_each            = local.deployed_jobs
  name                = "${var.prefix}-${each.key}"
  location            = var.region
  deletion_protection = var.environment == "production"

  template {
    template {
      service_account = var.service_accounts[each.value.account]
      max_retries     = 1
      timeout         = "600s"

      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"

        network_interfaces {
          network    = var.network_name
          subnetwork = var.subnetwork_name
        }
      }

      containers {
        image   = var.api_image
        command = ["gluons"]
        args    = each.value.args

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        dynamic "env" {
          for_each = var.runtime_env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = var.secret_env[each.value.account]
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }
}

# Kept even when the IAM check is off, so turning it on needs no second change.
resource "google_cloud_run_v2_service_iam_member" "wake_invoker" {
  count    = var.deploy_services ? 1 : 0
  name     = google_cloud_run_v2_service.backend["worker"].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.service_accounts["scheduler"]}"
}

# The queue keeps working if every Cloud Tasks wake is lost: this tick claims due jobs.
resource "google_cloud_scheduler_job" "worker_wake" {
  count     = var.deploy_services ? 1 : 0
  name      = "${var.prefix}-worker-wake"
  schedule  = var.worker_wake_schedule
  region    = var.region
  paused    = true # Unpause only after migrations, skill sync and approved staging smoke checks.
  time_zone = "Etc/UTC"

  attempt_deadline = "900s"

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.backend["worker"].uri}/internal/worker/wake"

    oidc_token {
      service_account_email = var.service_accounts["scheduler"]
      audience              = var.worker_wake_audience
    }
  }

  depends_on = [google_cloud_run_v2_service_iam_member.wake_invoker]
}

# CI rolls images only on these named services and jobs, never project-wide.
resource "google_cloud_run_v2_service_iam_member" "deployer_service" {
  for_each = local.deployed_backends
  name     = google_cloud_run_v2_service.backend[each.key].name
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${var.service_accounts["deployer"]}"
}

resource "google_cloud_run_v2_service_iam_member" "deployer_web" {
  count    = var.deploy_services ? 1 : 0
  name     = google_cloud_run_v2_service.web[0].name
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${var.service_accounts["deployer"]}"
}

resource "google_cloud_run_v2_job_iam_member" "deployer_job" {
  for_each = local.deployed_jobs
  name     = google_cloud_run_v2_job.job[each.key].name
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${var.service_accounts["deployer"]}"
}

resource "google_service_account_iam_member" "deployer_acts_as" {
  for_each           = toset(["api", "mcp", "webhooks", "worker", "web", "migrator"])
  service_account_id = var.service_account_ids[each.value]
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.service_accounts["deployer"]}"
}

output "backend_names" {
  value = { for name, service in google_cloud_run_v2_service.backend : name => service.name }
}
output "backend_urls" {
  value = { for name, service in google_cloud_run_v2_service.backend : name => service.uri }
}
output "web_service_name" {
  value = var.deploy_services ? google_cloud_run_v2_service.web[0].name : null
}
output "web_url" {
  value = var.deploy_services ? google_cloud_run_v2_service.web[0].uri : null
}
