# Gluons staging environment. NOTHING HERE HAS BEEN APPLIED OR PLANNED AGAINST A REAL PROJECT.
# `terraform apply` creates chargeable resources and needs the owner's explicit approval.
#
# ESTIMATED COST, UNVERIFIED. Written from memory of list prices, not checked against the
# Google Cloud pricing pages or a billing export, and excludes egress and provider usage.
# Use the Pricing Calculator before approving. Rough USD per month, us-central1:
#   Cloud SQL db-custom-2-7680, zonal, 20 GB, backups   110 to 140
#   Cloud Run, scale to zero, light use                   5 to 25
#   Load balancer forwarding rules                       18 to 25
#   Cloud Armor policy and rules                          8 to 12
#   KMS, Secret Manager, Artifact Registry, Pub/Sub       3 to 10
#   Cloud Scheduler, Cloud Tasks, Logging, Monitoring     1 to 10
#   Total                                                 about 145 to 220
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

  database_tier              = "db-custom-2-7680"
  database_high_availability = false
  database_disk_size_gb      = 20
  backup_retained_count      = 14
  deletion_protection        = true
  evidence_retention_days    = 90
  export_retention_days      = 14
  max_instances              = 3
  enable_load_balancer       = var.enable_load_balancer
  worker_invoker_iam_check   = var.worker_invoker_iam_check

  github_repository  = var.github_repository
  alert_email        = var.alert_email
  billing_account    = var.billing_account
  monthly_budget_usd = 250
}

output "gluons" { value = module.gluons }
