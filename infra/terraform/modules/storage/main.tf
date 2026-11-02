# Private buckets and the image registry.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

variable "project_id" { type = string }
variable "prefix" { type = string }
variable "region" { type = string }
variable "service_accounts" { type = map(string) }
variable "evidence_retention_days" { type = number }
variable "export_retention_days" { type = number }

locals {
  seconds_per_day = 86400

  # Prefix-scoped access. IAM conditions on objects need uniform bucket-level access.
  evidence_prefixes = {
    api       = "evidence/"
    worker    = "evidence/"
    evaluator = "evaluations/"
  }
}

resource "google_storage_bucket" "evidence" {
  name                        = "${var.project_id}-${var.prefix}-evidence"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  # Cloud Storage does not allow a retention policy together with Object Versioning, and
  # the retention policy is the stronger promise for evidence. is_locked stays false:
  # locking cannot be undone and is a legal decision for the owner, not a default.
  retention_policy {
    retention_period = var.evidence_retention_days * local.seconds_per_day
    is_locked        = false
  }

  lifecycle_rule {
    condition {
      age = var.evidence_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket" "exports" {
  name                        = "${var.project_id}-${var.prefix}-exports"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  # Audit exports and offboarding archives hold customer data, so they expire.
  lifecycle_rule {
    condition {
      age = var.export_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "evidence_read" {
  for_each = local.evidence_prefixes
  bucket   = google_storage_bucket.evidence.name
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${var.service_accounts[each.key]}"

  condition {
    title      = "${each.key}-prefix"
    expression = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.evidence.name}/objects/${each.value}\")"
  }
}

resource "google_storage_bucket_iam_member" "evidence_create" {
  for_each = local.evidence_prefixes
  bucket   = google_storage_bucket.evidence.name
  role     = "roles/storage.objectCreator"
  member   = "serviceAccount:${var.service_accounts[each.key]}"

  condition {
    title      = "${each.key}-prefix"
    expression = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.evidence.name}/objects/${each.value}\")"
  }
}

resource "google_storage_bucket_iam_member" "exports_create" {
  bucket = google_storage_bucket.exports.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${var.service_accounts["worker"]}"
}

resource "google_artifact_registry_repository" "images" {
  repository_id = var.prefix
  location      = var.region
  format        = "DOCKER"

  docker_config {
    immutable_tags = true
  }
}

resource "google_artifact_registry_repository_iam_member" "deployer" {
  repository = google_artifact_registry_repository.images.name
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${var.service_accounts["deployer"]}"
}

resource "google_artifact_registry_repository_iam_member" "runtime_pull" {
  for_each   = toset(["api", "mcp", "web", "webhooks", "worker", "evaluator", "billing", "migrator"])
  repository = google_artifact_registry_repository.images.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${var.service_accounts[each.value]}"
}

output "evidence_bucket" { value = google_storage_bucket.evidence.name }
output "exports_bucket" { value = google_storage_bucket.exports.name }
output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
