# Private Cloud SQL for PostgreSQL with backups and point-in-time recovery.
#
# Application roles are deliberately NOT created here. A user made through the Cloud SQL
# Admin API (google_sql_user, gcloud, the console) joins `cloudsqlsuperuser` and gets
# CREATEROLE and CREATEDB, which `gluons_app` and `gluons_system` must never hold.
# Migration 0002 creates them with plain SQL from GLUONS_APP_ROLE_PASSWORD and
# GLUONS_SYSTEM_ROLE_PASSWORD, which the migrate job reads from Secret Manager. The one
# Cloud SQL user, `gluons_migrator`, is created by hand so its password never enters
# Terraform state. See docs/DEPLOYMENT_STATUS.md.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

variable "prefix" { type = string }
variable "region" { type = string }
variable "network_id" { type = string }
variable "tier" { type = string }
variable "high_availability" { type = bool }
variable "disk_size_gb" { type = number }
variable "backup_retained_count" { type = number }
variable "log_retention_days" {
  type        = number
  description = "Write-ahead log kept for point-in-time recovery."
}
variable "deletion_protection" { type = bool }

resource "google_sql_database_instance" "main" {
  name                = "${var.prefix}-postgres"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.deletion_protection

  settings {
    # db-custom tiers exist only on Enterprise. Newer PostgreSQL versions can default to
    # Enterprise Plus, so the edition is stated rather than inherited.
    edition                     = "ENTERPRISE"
    tier                        = var.tier
    availability_type           = var.high_availability ? "REGIONAL" : "ZONAL"
    disk_autoresize             = true
    disk_size                   = var.disk_size_gb
    deletion_protection_enabled = var.deletion_protection

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "02:00"
      transaction_log_retention_days = var.log_retention_days

      backup_retention_settings {
        retained_backups = var.backup_retained_count
      }
    }

    maintenance_window {
      day          = 7
      hour         = 3
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled  = true
      record_application_tags = false
      record_client_address   = false
    }
  }
}

resource "google_sql_database" "gluons" {
  name     = "gluons"
  instance = google_sql_database_instance.main.name
}

output "instance_name" { value = google_sql_database_instance.main.name }
output "connection_name" { value = google_sql_database_instance.main.connection_name }
output "private_ip" { value = google_sql_database_instance.main.private_ip_address }
