# Applying staging creates billable resources and requires explicit owner approval.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
  backend "gcs" {
    # Supply the bucket at init: terraform init -backend-config="bucket=YOUR_STATE_BUCKET"
    prefix = "gluons/staging"
  }
}

variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "deploy_services" {
  type    = bool
  default = false
}
variable "api_image" {
  type    = string
  default = ""
}
variable "web_image" {
  type    = string
  default = ""
}
variable "api_host" {
  type    = string
  default = ""
}
variable "web_host" {
  type    = string
  default = ""
}
variable "enable_load_balancer" {
  type    = bool
  default = false
}
variable "credit_limited_pilot" {
  type        = bool
  default     = false
  description = "Use a small database and Cloud Run HTTPS origins for a short staging pilot."

  validation {
    condition = !var.credit_limited_pilot || (
      var.region == "us-central1" && !var.enable_load_balancer &&
      var.api_host == "" && var.web_host == "" && var.billing_account == "" &&
      var.worker_invoker_iam_check
    )
    error_message = "The credit-limited pilot requires us-central1, Cloud Run origins, owner-managed budgets, and IAM on the private worker."
  }
}
variable "pilot_database_running" {
  type        = bool
  default     = false
  description = "Start the credit-limited staging database for an approved pilot window."
}
variable "plain_env" {
  type    = map(string)
  default = {}
}
variable "populated_secrets" {
  type    = set(string)
  default = []
}
variable "github_repository" {
  type    = string
  default = ""
}
variable "alert_email" {
  type    = string
  default = ""
}
variable "billing_account" {
  type    = string
  default = ""
}
variable "worker_invoker_iam_check" {
  type    = bool
  default = true
}

provider "google" {
  project = var.project_id
  region  = var.region
}

module "gluons" {
  source = "../../modules/gluons_stack"

  environment = "staging"
  project_id  = var.project_id
  region      = var.region

  deploy_services   = var.deploy_services
  api_image         = var.api_image
  web_image         = var.web_image
  api_host          = var.api_host
  web_host          = var.web_host
  plain_env         = var.plain_env
  populated_secrets = var.populated_secrets

  database_tier              = var.credit_limited_pilot ? "db-g1-small" : "db-custom-2-7680"
  database_high_availability = false
  database_disk_size_gb      = var.credit_limited_pilot ? 10 : 20
  database_disk_autoresize   = !var.credit_limited_pilot
  database_activation_policy = var.credit_limited_pilot && !var.pilot_database_running ? "NEVER" : "ALWAYS"
  backup_retained_count      = var.credit_limited_pilot ? 2 : 14
  log_retention_days         = var.credit_limited_pilot ? 2 : 7
  deletion_protection        = true
  evidence_retention_days    = 90
  export_retention_days      = 14
  max_instances              = var.credit_limited_pilot ? 1 : 3
  use_run_app_origins        = var.credit_limited_pilot
  enable_load_balancer       = var.enable_load_balancer
  worker_invoker_iam_check   = var.worker_invoker_iam_check

  github_repository  = var.github_repository
  alert_email        = var.alert_email
  billing_account    = var.billing_account
  monthly_budget_usd = 250
}

output "gluons" { value = module.gluons }
