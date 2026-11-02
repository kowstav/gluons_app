# One identity per workload, with no project-level roles at all. Every grant lives beside
# the named resource it applies to (secret, key, topic, queue, bucket), so reviewing a
# resource shows exactly who can touch it.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

variable "environment" { type = string }

locals {
  accounts = {
    api         = "Serves the owner REST API and OAuth"
    mcp         = "Serves the remote MCP endpoint"
    worker      = "Runs durable background jobs"
    webhooks    = "Receives signed provider webhooks"
    evaluator   = "Runs model and skill evaluations"
    billing     = "Exports the usage ledger to a billing provider"
    web         = "Serves the Next.js owner app and holds no permissions"
    migrator    = "Applies database migrations"
    scheduler   = "Identity in the OIDC tokens that wake the private worker"
    pubsub-push = "Identity in the OIDC tokens on Pub/Sub push deliveries"
    deployer    = "Used by CI through Workload Identity Federation"
  }
}

resource "google_service_account" "account" {
  for_each     = local.accounts
  account_id   = "gluons-${each.key}"
  display_name = "Gluons ${var.environment} ${each.key}"
  description  = each.value
}

output "emails" {
  description = "Service account email by workload name."
  value       = { for name, account in google_service_account.account : name => account.email }
}

output "ids" {
  description = "Fully qualified service account id by workload name."
  value       = { for name, account in google_service_account.account : name => account.id }
}
