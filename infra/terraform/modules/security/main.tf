# Secret Manager containers and the KMS key for the credential vault.
#
# This module creates secrets with NO VERSIONS. The owner adds values with
# `gcloud secrets versions add`, so no secret value ever enters Terraform state or git.
# docs/EXTERNAL_DEPENDENCIES.md says what each one is and where it comes from.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

variable "prefix" { type = string }
variable "region" { type = string }
variable "service_accounts" {
  type        = map(string)
  description = "Service account email by workload name."
}
variable "populated_secrets" {
  type        = set(string)
  default     = []
  description = "Optional secrets the owner has added a value to. Only these are mounted, because Cloud Run refuses a revision that references a secret with no version."
}

locals {
  # name => env var, the workloads allowed to read it, and whether the app cannot start
  # without it. `system-database-url` is absent from the evaluator and billing on purpose:
  # neither has a reason to use the queue and webhook-routing role.
  catalog = {
    database-url           = { env = "GLUONS_DATABASE_URL", readers = ["api", "mcp", "webhooks", "worker", "evaluator", "billing"], required = true }
    system-database-url    = { env = "GLUONS_SYSTEM_DATABASE_URL", readers = ["api", "mcp", "webhooks", "worker"], required = true }
    signing-key            = { env = "GLUONS_SIGNING_KEY_B64", readers = ["api", "mcp", "webhooks", "worker", "evaluator", "billing"], required = true }
    migration-database-url = { env = "GLUONS_MIGRATION_DATABASE_URL", readers = ["migrator"], required = true }
    app-role-password      = { env = "GLUONS_APP_ROLE_PASSWORD", readers = ["migrator"], required = true }
    system-role-password   = { env = "GLUONS_SYSTEM_ROLE_PASSWORD", readers = ["migrator"], required = true }

    smtp-url                  = { env = "GLUONS_SMTP_URL", readers = ["api", "worker"], required = false }
    google-client-secret      = { env = "GLUONS_GOOGLE_CLIENT_SECRET", readers = ["api", "mcp", "worker"], required = false }
    meta-app-secret           = { env = "GLUONS_META_APP_SECRET", readers = ["api", "webhooks", "worker"], required = false }
    meta-webhook-verify-token = { env = "GLUONS_META_WEBHOOK_VERIFY_TOKEN", readers = ["webhooks"], required = false }
    elevenlabs-api-key        = { env = "GLUONS_ELEVENLABS_API_KEY", readers = ["api", "worker"], required = false }
    elevenlabs-webhook-secret = { env = "GLUONS_ELEVENLABS_WEBHOOK_SECRET", readers = ["webhooks"], required = false }
    elevenlabs-tool-secret    = { env = "GLUONS_ELEVENLABS_TOOL_SECRET", readers = ["api", "webhooks", "worker"], required = false }
    twilio-auth-token         = { env = "GLUONS_TWILIO_AUTH_TOKEN", readers = ["api", "webhooks", "worker"], required = false }
    shopify-client-secret     = { env = "GLUONS_SHOPIFY_CLIENT_SECRET", readers = ["api", "webhooks", "worker"], required = false }
    stripe-secret-key         = { env = "GLUONS_STRIPE_SECRET_KEY", readers = ["api", "worker", "billing"], required = false }
    stripe-webhook-secret     = { env = "GLUONS_STRIPE_WEBHOOK_SECRET", readers = ["webhooks", "billing"], required = false }
    anthropic-api-key         = { env = "GLUONS_ANTHROPIC_API_KEY", readers = ["api", "worker", "evaluator"], required = false }
    openai-api-key            = { env = "GLUONS_OPENAI_API_KEY", readers = ["api", "worker", "evaluator"], required = false }
    private-llm-api-key       = { env = "GLUONS_PRIVATE_LLM_API_KEY", readers = ["api", "worker", "evaluator"], required = false }
  }

  grants = merge([
    for name, secret in local.catalog : {
      for reader in secret.readers : "${name}:${reader}" => { secret = name, reader = reader }
    }
  ]...)

  workloads = toset(flatten([for secret in values(local.catalog) : secret.readers]))

  # Which secrets each workload receives as environment variables.
  mounted_env = {
    for workload in local.workloads : workload => {
      for name, secret in local.catalog : secret.env => "gluons-${name}"
      if contains(secret.readers, workload) && (secret.required || contains(var.populated_secrets, name))
    }
  }
}

resource "google_secret_manager_secret" "secret" {
  for_each  = local.catalog
  secret_id = "gluons-${each.key}"

  labels = {
    required = tostring(each.value.required)
  }

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

resource "google_secret_manager_secret_iam_member" "reader" {
  for_each  = local.grants
  secret_id = google_secret_manager_secret.secret[each.value.secret].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.service_accounts[each.value.reader]}"
}

resource "google_kms_key_ring" "vault" {
  name     = "${var.prefix}-vault"
  location = var.region
}

resource "google_kms_crypto_key" "vault" {
  name            = "provider-credentials"
  key_ring        = google_kms_key_ring.vault.id
  rotation_period = "7776000s"

  lifecycle {
    prevent_destroy = true
  }
}

# The webhooks service only routes events and the worker does the credentialed work, so it
# has no key access. The MCP service needs it because connect_service seals a PKCE verifier.
resource "google_kms_crypto_key_iam_member" "vault_user" {
  for_each      = toset(["api", "mcp", "worker"])
  crypto_key_id = google_kms_crypto_key.vault.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${var.service_accounts[each.value]}"
}

output "kms_key_id" { value = google_kms_crypto_key.vault.id }
output "mounted_env" {
  description = "Env var to secret id, by workload."
  value       = local.mounted_env
}
output "secret_ids" {
  description = "Every secret container, all of which start empty."
  value       = [for secret in google_secret_manager_secret.secret : secret.secret_id]
}
