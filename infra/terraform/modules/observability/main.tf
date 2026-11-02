# Budget alerts, log-based metrics, alert policies and uptime checks.
#
# Log filters match the structured records written by gluons.observability. If a log
# message changes there, change it here too. Runbooks in docs/runbooks/ name these alerts.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

variable "project_id" { type = string }
variable "project_number" { type = string }
variable "prefix" { type = string }
variable "billing_account" { type = string }
variable "monthly_budget_usd" { type = number }
variable "alert_email" {
  type        = string
  description = "Empty sends alerts only to notification_channels."
}
variable "notification_channels" { type = list(string) }
variable "api_host" { type = string }
variable "web_host" { type = string }
variable "deploy_services" { type = bool }

locals {
  channels = concat(var.notification_channels, google_monitoring_notification_channel.email[*].id)

  log_metrics = {
    gluons-job-dead-lettered = {
      description = "A background job exhausted its attempts."
      filter      = "resource.type=\"cloud_run_revision\" AND jsonPayload.message=\"job dead-lettered\""
    }
    gluons-job-failed = {
      description = "A background job raised and will retry."
      filter      = "resource.type=\"cloud_run_revision\" AND jsonPayload.message=\"job failed\""
    }
    gluons-wake-rejected = {
      description = "A caller failed OIDC verification on the worker wake endpoint."
      filter      = "resource.type=\"cloud_run_revision\" AND jsonPayload.message=\"internal wake rejected\""
    }
    gluons-unhandled-error = {
      description = "Any application error log."
      filter      = "resource.type=\"cloud_run_revision\" AND severity>=ERROR AND jsonPayload.logger=~\"^gluons\""
    }
  }

  log_alerts = {
    gluons-job-dead-lettered = { display = "Background job dead-lettered", threshold = 0, duration = "0s" }
    gluons-wake-rejected     = { display = "Worker wake rejections", threshold = 5, duration = "300s" }
    gluons-unhandled-error   = { display = "Application errors", threshold = 20, duration = "300s" }
  }
}

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email != "" ? 1 : 0
  display_name = "${var.prefix} on-call email"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }
}

resource "google_logging_metric" "log" {
  for_each    = local.log_metrics
  name        = each.key
  description = each.value.description
  filter      = each.value.filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "log" {
  for_each              = local.log_alerts
  display_name          = "${var.prefix} ${each.value.display}"
  combiner              = "OR"
  notification_channels = local.channels

  conditions {
    display_name = each.value.display

    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"logging.googleapis.com/user/${each.key}\""
      comparison      = "COMPARISON_GT"
      threshold_value = each.value.threshold
      duration        = each.value.duration

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  depends_on = [google_logging_metric.log]
}

resource "google_monitoring_alert_policy" "api_errors" {
  count                 = var.deploy_services ? 1 : 0
  display_name          = "${var.prefix} API 5xx"
  combiner              = "OR"
  notification_channels = local.channels

  conditions {
    display_name = "HTTP server errors"

    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\" AND resource.labels.service_name=\"${var.prefix}-api\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "60s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
}

# Silence is the failure mode of a private worker, so absence of requests also alerts.
resource "google_monitoring_alert_policy" "worker_silent" {
  count                 = var.deploy_services ? 1 : 0
  display_name          = "${var.prefix} Worker not woken"
  combiner              = "OR"
  notification_channels = local.channels
  enabled               = false # Turn on when the Cloud Scheduler wake job is unpaused.

  conditions {
    display_name = "No requests reached the worker"

    condition_absent {
      filter   = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\" AND resource.labels.service_name=\"${var.prefix}-worker\""
      duration = "600s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "database_disk" {
  display_name          = "${var.prefix} Database disk above 85 percent"
  combiner              = "OR"
  notification_channels = local.channels

  conditions {
    display_name = "Cloud SQL disk utilisation"

    condition_threshold {
      filter          = "resource.type=\"cloudsql_database\" AND metric.type=\"cloudsql.googleapis.com/database/disk/utilization\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.85
      duration        = "600s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "dead_letter_backlog" {
  display_name          = "${var.prefix} Pub/Sub dead letters waiting"
  combiner              = "OR"
  notification_channels = local.channels

  conditions {
    display_name = "Undelivered dead letters"

    condition_threshold {
      filter          = "resource.type=\"pubsub_subscription\" AND metric.type=\"pubsub.googleapis.com/subscription/num_undelivered_messages\" AND resource.labels.subscription_id=\"${var.prefix}-dead-letters-review\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }
}

# Uptime checks need a stable hostname, so they arrive with DNS and the load balancer.
resource "google_monitoring_uptime_check_config" "public" {
  for_each = var.api_host != "" ? { api = { host = var.api_host, path = "/ready" }, web = { host = var.web_host, path = "/" } } : {}

  display_name = "${var.prefix} ${each.key}"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = each.value.path
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = each.value.host
    }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  for_each              = google_monitoring_uptime_check_config.public
  display_name          = "${var.prefix} ${each.key} uptime"
  combiner              = "OR"
  notification_channels = local.channels

  conditions {
    display_name = "Uptime check failing"

    condition_threshold {
      filter          = "resource.type=\"uptime_url\" AND metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id=\"${each.value.uptime_check_id}\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "600s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_TRUE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }
}

# Budget alerts notify. They do not stop spending, so the over-budget runbook still matters.
resource "google_billing_budget" "monthly" {
  count           = var.billing_account != "" ? 1 : 0
  billing_account = var.billing_account
  display_name    = "${var.prefix} monthly spending alert"

  budget_filter {
    projects        = ["projects/${var.project_number}"]
    calendar_period = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(floor(var.monthly_budget_usd))
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  all_updates_rule {
    monitoring_notification_channels = local.channels
    disable_default_iam_recipients   = false
  }
}
