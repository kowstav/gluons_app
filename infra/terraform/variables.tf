variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "environment" {

  type = string
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "Use a separate project and state for dev, staging or production."
  }
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
variable "deploy_services" {

  type        = bool
  default     = false
  description = "Enable only after approved provisioning, image builds, secret population and migration review."
}
variable "database_tier" {
  type    = string
  default = "db-custom-2-7680"
}
variable "secret_env" {

  type        = map(object({ secret = string, version = string }))
  default     = {}
  description = "Environment variable to existing Secret Manager secret ID/version. Never pass secret values to Terraform."
}
variable "migration_secret_env" {

  type        = map(object({ secret = string, version = string }))
  default     = {}
  description = "Migration-only database URL and role password secret references; never injected into the runtime services."
}
variable "notification_channels" {
  type    = list(string)
  default = []
}
variable "billing_account" {
  type    = string
  default = ""
}
variable "monthly_budget_usd" {
  type    = number
  default = 100
}
