# Workload Identity Federation for GitHub Actions, so CI holds no service account key.
# Only the named repository, inside the named GitHub environment, can act as gluons-deployer.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

variable "github_repository" {
  type        = string
  description = "OWNER/REPO. The repository has no GitHub remote yet."
}
variable "github_environment" {
  type        = string
  description = "GitHub environment whose protection rules gate this project."
}
variable "deployer_service_account_id" { type = string }

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"        = "assertion.sub"
    "attribute.repository"  = "assertion.repository"
    "attribute.environment" = "assertion.environment"
  }

  # Checked on Google's side, so a pull request from a fork or a job outside the protected
  # GitHub environment cannot obtain a token at all.
  attribute_condition = "assertion.repository == \"${var.github_repository}\" && assertion.environment == \"${var.github_environment}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer" {
  service_account_id = var.deployer_service_account_id
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

output "provider_name" {
  description = "Value for the GCP_WORKLOAD_IDENTITY_PROVIDER GitHub environment variable."
  value       = google_iam_workload_identity_pool_provider.github.name
}
