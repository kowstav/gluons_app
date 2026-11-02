# Private network for Cloud SQL. Cloud Run reaches the database through Direct VPC egress,
# so no service account needs a Cloud SQL role and the instance has no public address.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

variable "prefix" { type = string }
variable "region" { type = string }
variable "runtime_cidr" {
  type    = string
  default = "10.20.0.0/24"
}

resource "google_compute_network" "private" {
  name                    = var.prefix
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "runtime" {
  name                     = "${var.prefix}-runtime"
  region                   = var.region
  ip_cidr_range            = var.runtime_cidr
  network                  = google_compute_network.private.id
  private_ip_google_access = true
}

resource "google_compute_global_address" "sql" {
  name          = "${var.prefix}-sql"
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

output "network_id" { value = google_compute_network.private.id }
output "network_name" { value = google_compute_network.private.name }
output "subnetwork_name" { value = google_compute_subnetwork.runtime.name }
