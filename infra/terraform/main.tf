# No resources are created by checking out or validating this configuration.
# terraform apply is a separately approved, chargeable operation.
locals {
  prefix = "gluons-${var.environment}"
  services = toset([
    "run.googleapis.com", "sqladmin.googleapis.com", "servicenetworking.googleapis.com",
    "compute.googleapis.com", "secretmanager.googleapis.com", "cloudkms.googleapis.com",
    "cloudtasks.googleapis.com", "pubsub.googleapis.com", "cloudscheduler.googleapis.com",
    "artifactregistry.googleapis.com", "monitoring.googleapis.com", "logging.googleapis.com",
    "billingbudgets.googleapis.com", "aiplatform.googleapis.com"
  ])
  runtime_env = {
    GLUONS_ENV                 = var.environment == "dev" ? "staging" : var.environment
    GLUONS_PUBLIC_API_URL      = var.api_url
    GLUONS_PUBLIC_WEB_URL      = var.web_url
    GLUONS_ALLOWED_WEB_ORIGINS = var.web_url
    GLUONS_GCP_PROJECT_ID      = var.project_id
    GLUONS_GCP_REGION          = var.region
    GLUONS_KMS_KEY_NAME        = google_kms_crypto_key.vault.id
    GLUONS_ARTIFACTS_BUCKET    = google_storage_bucket.evidence.name
    GLUONS_PUBSUB_EVENTS_TOPIC = google_pubsub_topic.events["state-events"].id
    GLUONS_MAIL_TRANSPORT      = "smtp"
  }
}

resource "google_project_service" "required" {
  for_each           = local.services
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "private" {
  name                    = local.prefix
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required]
}
resource "google_compute_subnetwork" "runtime" {
  name                     = "${local.prefix}-runtime"
  ip_cidr_range            = "10.20.0.0/24"
  network                  = google_compute_network.private.id
  private_ip_google_access = true
}
resource "google_compute_global_address" "sql" {
  name          = "${local.prefix}-sql"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.private.id
}
resource "google_service_networking_connection" "sql" {
  network                 = google_compute_network.private.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql.name]
}
resource "google_sql_database_instance" "main" {
  name                = "${local.prefix}-postgres"
  database_version    = "POSTGRES_16"
  deletion_protection = true
  depends_on          = [google_service_networking_connection.sql]
  settings {
    tier              = var.database_tier
    availability_type = var.environment == "production" ? "REGIONAL" : "ZONAL"
    disk_autoresize   = true
    disk_size         = 20
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.private.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "02:00"
      backup_retention_settings { retained_backups = 14 }
    }
    maintenance_window {
      day          = 7
      hour         = 3
      update_track = "stable"
    }
  }
}
resource "google_sql_database" "gluons" {
  name     = "gluons"
  instance = google_sql_database_instance.main.name
}

resource "google_service_account" "runtime" {
  for_each     = toset(["api", "worker", "migrator", "scheduler"])
  account_id   = "${local.prefix}-${each.key}"
  display_name = "Gluons ${var.environment} ${each.key}"
  depends_on   = [google_project_service.required]
}
resource "google_kms_key_ring" "vault" {
  name       = "${local.prefix}-vault"
  location   = var.region
  depends_on = [google_project_service.required]
}
resource "google_kms_crypto_key" "vault" {
  name            = "provider-credentials"
  key_ring        = google_kms_key_ring.vault.id
  rotation_period = "7776000s"
  lifecycle { prevent_destroy = true }
}
resource "google_kms_crypto_key_iam_member" "runtime" {
  for_each      = toset(["api", "worker"])
  crypto_key_id = google_kms_crypto_key.vault.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.runtime[each.key].email}"
}
resource "google_secret_manager_secret_iam_member" "runtime" {
  for_each  = { for pair in setproduct(["api", "worker"], toset([for s in values(var.secret_env) : s.secret])) : "${pair[0]}:${pair[1]}" => pair }
  secret_id = each.value[1]
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime[each.value[0]].email}"
}
resource "google_secret_manager_secret_iam_member" "migrator" {
  for_each  = toset([for s in values(var.migration_secret_env) : s.secret])
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime["migrator"].email}"
}
resource "google_artifact_registry_repository" "images" {
  repository_id = local.prefix
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.required]
}
resource "google_storage_bucket" "evidence" {
  name                        = "${var.project_id}-${local.prefix}-evidence"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  lifecycle_rule {
    condition { age = 365 }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.required]
}
resource "google_pubsub_topic" "events" {
  for_each                   = toset(["incoming-events", "state-events", "automation-alerts", "dead-letters"])
  name                       = "${local.prefix}-${each.key}"
  message_retention_duration = "604800s"
  depends_on                 = [google_project_service.required]
}
resource "google_pubsub_topic_iam_member" "worker" {
  topic  = google_pubsub_topic.events["state-events"].name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.runtime["worker"].email}"
}
resource "google_cloud_tasks_queue" "actions" {
  name     = "${local.prefix}-actions"
  location = var.region
  rate_limits {
    max_concurrent_dispatches = 5
    max_dispatches_per_second = 5
  }
  retry_config {
    max_attempts  = 6
    min_backoff   = "5s"
    max_backoff   = "900s"
    max_doublings = 5
  }
  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_service" "api" {
  count               = var.deploy_services ? 1 : 0
  name                = "${local.prefix}-api"
  location            = var.region
  deletion_protection = var.environment == "production"
  template {
    service_account                  = google_service_account.runtime["api"].email
    timeout                          = "60s"
    max_instance_request_concurrency = 20
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.private.name
        subnetwork = google_compute_subnetwork.runtime.name
      }
    }
    containers {
      image = var.api_image
      ports { container_port = 8000 }
      resources { limits = { cpu = "1", memory = "1Gi" } }
      dynamic "env" {
        for_each = local.runtime_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {

        for_each = var.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value.secret
              version = env.value.version
            }
          }
        }
      }
      startup_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 20
      }
      liveness_probe {
        http_get {
          path = "/health"
          port = 8000
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.runtime]
}
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  count    = var.deploy_services ? 1 : 0
  name     = google_cloud_run_v2_service.api[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
resource "google_cloud_run_v2_service" "web" {
  count               = var.deploy_services ? 1 : 0
  name                = "${local.prefix}-web"
  location            = var.region
  deletion_protection = var.environment == "production"
  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
    containers {
      image = var.web_image
      ports { container_port = 3000 }
      resources { limits = { cpu = "1", memory = "512Mi" } }
    }
  }
}
resource "google_cloud_run_v2_service_iam_member" "web_public" {
  count    = var.deploy_services ? 1 : 0
  name     = google_cloud_run_v2_service.web[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_job" "jobs" {
  for_each            = var.deploy_services ? { worker = "worker", migrate = "migrate", skills = "skills-sync" } : {}
  name                = "${local.prefix}-${each.key}"
  location            = var.region
  deletion_protection = var.environment == "production"
  template {
    template {
      service_account = google_service_account.runtime[each.key == "migrate" ? "migrator" : "worker"].email
      max_retries     = 1
      timeout         = "600s"
      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.private.name
          subnetwork = google_compute_subnetwork.runtime.name
        }
      }
      containers {
        image   = var.api_image
        command = ["gluons"]
        args    = each.key == "worker" ? ["worker", "--once"] : [each.value]
        resources { limits = { cpu = "1", memory = "1Gi" } }
        dynamic "env" {
          for_each = local.runtime_env
          content {
            name  = env.key
            value = env.value
          }
        }
        dynamic "env" {

          for_each = each.key == "migrate" ? var.migration_secret_env : var.secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value.secret
                version = env.value.version
              }
            }
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.runtime, google_secret_manager_secret_iam_member.migrator]
}
resource "google_cloud_run_v2_job_iam_member" "scheduler" {
  count    = var.deploy_services ? 1 : 0
  name     = google_cloud_run_v2_job.jobs["worker"].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime["scheduler"].email}"
}
resource "google_cloud_scheduler_job" "worker" {
  count    = var.deploy_services ? 1 : 0
  name     = "${local.prefix}-worker-wakeup"
  schedule = "* * * * *"
  region   = var.region
  paused   = true # Unpause only after migrations, skill sync, and approved staging smoke checks.
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.jobs["worker"].name}:run"
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }
    oauth_token { service_account_email = google_service_account.runtime["scheduler"].email }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.scheduler]
}
resource "google_monitoring_alert_policy" "api_errors" {
  count                 = var.deploy_services ? 1 : 0
  display_name          = "${local.prefix} API 5xx"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "HTTP server errors"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code_class = \"5xx\" AND resource.labels.service_name = \"${local.prefix}-api\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "60s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
}
resource "google_billing_budget" "monthly" {
  count           = var.billing_account != "" ? 1 : 0
  billing_account = var.billing_account
  display_name    = "${local.prefix} monthly spending alert"
  budget_filter {
    projects = ["projects/${
      data.google_project.current.number
    }"]
    calendar_period = "MONTH"
  }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(floor(var.monthly_budget_usd))
    }
  }
  threshold_rules { threshold_percent = 0.5 }
  threshold_rules { threshold_percent = 0.9 }
  threshold_rules { threshold_percent = 1.0 }
}
data "google_project" "current" { project_id = var.project_id }
