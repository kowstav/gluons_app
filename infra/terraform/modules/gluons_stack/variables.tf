variable "project_id" { type = string }

variable "region" {
  type        = string
  description = "Choose to match the first customer's data residency, not a marketing claim."
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "Use a separate project and state for dev, staging or production."
  }
}

variable "deploy_services" {
  type        = bool
  default     = false
  description = "Enable only after approved provisioning, image builds, secret population and migration review."
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
  type        = string
  default     = ""
  description = "Public API origin when there is no api_host. Must be https."
}

variable "web_url" {
  type        = string
  default     = ""
  description = "Public web origin when there is no web_host. Must be https."
}

variable "api_host" {
  type        = string
  default     = ""
  description = "API hostname, such as api.example.com. Needed for the load balancer and uptime checks."
}

variable "web_host" {
  type        = string
  default     = ""
  description = "Web hostname, such as app.example.com."
}

variable "enable_load_balancer" {
  type        = bool
  default     = false
  description = "Create the HTTPS load balancer and Cloud Armor. Needs api_host, web_host and DNS."
}

variable "rate_limit_per_minute" {
  type    = number
  default = 600
}

variable "plain_env" {
  type        = map(string)
  default     = {}
  description = "Non-secret settings such as GLUONS_GOOGLE_CLIENT_ID and GLUONS_MAIL_FROM. Secrets never go here."
}

variable "populated_secrets" {
  type        = set(string)
  default     = []
  description = "Optional secrets with a value added, named without the gluons- prefix."
}

variable "database_tier" {
  type    = string
  default = "db-custom-2-7680"
}

variable "database_high_availability" {
  type    = bool
  default = false
}

variable "database_disk_size_gb" {
  type    = number
  default = 20
}

variable "backup_retained_count" {
  type    = number
  default = 14
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "evidence_retention_days" {
  type    = number
  default = 365
}

variable "export_retention_days" {
  type    = number
  default = 30
}

variable "max_instances" {
  type    = number
  default = 3
}

variable "min_api_instances" {
  type    = number
  default = 0
}

variable "worker_invoker_iam_check" {
  type        = bool
  default     = true
  description = "Keep Cloud Run's IAM check on the private worker. See the OPEN TEST note in modules/services/main.tf."
}

variable "worker_wake_schedule" {
  type    = string
  default = "* * * * *"
}

variable "gmail_push_path" {
  type    = string
  default = "/v1/webhooks/gmail"
}

variable "enable_vertex_ai" {
  type        = bool
  default     = false
  description = "Grant Vertex AI use to the worker and evaluator. This is a project-level role."
}

variable "github_repository" {
  type        = string
  default     = ""
  description = "OWNER/REPO for Workload Identity Federation. Empty until a GitHub remote exists."
}

variable "alert_email" {
  type    = string
  default = ""
}

variable "notification_channels" {
  type    = list(string)
  default = []
}

variable "billing_account" {
  type        = string
  default     = ""
  description = "Billing account id for the budget alert."
}

variable "monthly_budget_usd" {
  type    = number
  default = 100
}
