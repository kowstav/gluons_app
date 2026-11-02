# Gluons dev environment. NOTHING HERE HAS BEEN APPLIED OR PLANNED AGAINST A REAL PROJECT.
# `terraform apply` creates chargeable resources and needs the owner's explicit approval.
#
# ESTIMATED COST, UNVERIFIED. Written from memory of list prices, not checked against the
# Google Cloud pricing pages or a billing export, and excludes egress and provider usage.
# Use the Pricing Calculator before approving. Rough USD per month, us-central1:
#   Cloud SQL db-custom-1-3840, zonal, 10 GB, backups    55 to 75
#   Cloud Run, scale to zero, light use                   0 to 10
#   Load balancer                                         0 (off in dev)
#   KMS, Secret Manager, Artifact Registry, Pub/Sub       2 to 6
#   Cloud Scheduler, Cloud Tasks, Logging, Monitoring     0 to 5
#   Total                                                 about 60 to 95
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
  backend "gcs" {
    # Supply the bucket at init: terraform init -backend-config="bucket=YOUR_STATE_BUCKET"
    prefix = "gluons/dev"
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
variable "api_url" {
  type    = string
  default = ""
}
variable "web_url" {
  type    = string
  default = ""
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

  environment = "dev"
  project_id  = var.project_id
  region      = var.region

  deploy_services   = var.deploy_services
  api_image         = var.api_image
  web_image         = var.web_image
  api_url           = var.api_url
  web_url           = var.web_url
  plain_env         = var.plain_env
  populated_secrets = var.populated_secrets

  database_tier              = "db-custom-1-3840"
  database_high_availability = false
  database_disk_size_gb      = 10
  backup_retained_count      = 7
  deletion_protection        = false
  evidence_retention_days    = 30
  export_retention_days      = 7
  max_instances              = 2
  enable_load_balancer       = false
  worker_invoker_iam_check   = var.worker_invoker_iam_check

  github_repository  = var.github_repository
  alert_email        = var.alert_email
  billing_account    = var.billing_account
  monthly_budget_usd = 100
}

output "gluons" { value = module.gluons }
