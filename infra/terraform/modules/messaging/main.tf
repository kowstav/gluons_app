# Cloud Tasks queues, Pub/Sub topics and the Gmail push subscription.
#
# ADR-002: Postgres is the execution authority. Queues and topics only wake workers and fan
# out events, so a lost delivery delays work but never loses it.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

variable "prefix" { type = string }
variable "region" { type = string }
variable "project_number" { type = string }
variable "service_accounts" {
  type        = map(string)
  description = "Service account email by workload name."
}
variable "service_account_ids" {
  type        = map(string)
  description = "Fully qualified service account id by workload name."
}
variable "enable_gmail_push" {
  type        = bool
  description = "Create the push subscription. It needs the webhooks service URL, so it waits for deploy_services."
}
variable "gmail_push_endpoint" {
  type    = string
  default = ""
}
variable "gmail_push_audience" { type = string }

locals {
  queues = {
    model-work          = { max_concurrent = 10, per_second = 5 }
    connector-actions   = { max_concurrent = 5, per_second = 5 }
    scheduled-followups = { max_concurrent = 5, per_second = 2 }
  }
  topics = ["incoming-events", "state-events", "automation-alerts", "dead-letters"]

  # Least privilege by queue and topic, not across the project.
  enqueuers = {
    "model-work:api"             = { queue = "model-work", account = "api" }
    "model-work:worker"          = { queue = "model-work", account = "worker" }
    "model-work:evaluator"       = { queue = "model-work", account = "evaluator" }
    "connector-actions:api"      = { queue = "connector-actions", account = "api" }
    "connector-actions:mcp"      = { queue = "connector-actions", account = "mcp" }
    "connector-actions:webhooks" = { queue = "connector-actions", account = "webhooks" }
    "connector-actions:worker"   = { queue = "connector-actions", account = "worker" }
    "scheduled-followups:worker" = { queue = "scheduled-followups", account = "worker" }
  }
  publishers = {
    "incoming-events:webhooks"  = { topic = "incoming-events", account = "webhooks" }
    "state-events:worker"       = { topic = "state-events", account = "worker" }
    "automation-alerts:worker"  = { topic = "automation-alerts", account = "worker" }
    "automation-alerts:billing" = { topic = "automation-alerts", account = "billing" }
    "dead-letters:worker"       = { topic = "dead-letters", account = "worker" }
  }
  pubsub_agent = "serviceAccount:service-${var.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
  tasks_agent  = "serviceAccount:service-${var.project_number}@gcp-sa-cloudtasks.iam.gserviceaccount.com"
}

resource "google_cloud_tasks_queue" "queue" {
  for_each = local.queues
  name     = "${var.prefix}-${each.key}"
  location = var.region

  rate_limits {
    max_concurrent_dispatches = each.value.max_concurrent
    max_dispatches_per_second = each.value.per_second
  }

  retry_config {
    max_attempts  = 6
    min_backoff   = "5s"
    max_backoff   = "900s"
    max_doublings = 5
  }
}

resource "google_cloud_tasks_queue_iam_member" "enqueuer" {
  for_each = local.enqueuers
  name     = google_cloud_tasks_queue.queue[each.value.queue].name
  location = var.region
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${var.service_accounts[each.value.account]}"
}

# Enqueuing a task that carries an OIDC token needs actAs on the identity in the token.
resource "google_service_account_iam_member" "enqueuer_acts_as_scheduler" {
  for_each           = toset(distinct([for grant in values(local.enqueuers) : grant.account]))
  service_account_id = var.service_account_ids["scheduler"]
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.service_accounts[each.value]}"
}

resource "google_service_account_iam_member" "tasks_agent_acts_as_scheduler" {
  service_account_id = var.service_account_ids["scheduler"]
  role               = "roles/iam.serviceAccountUser"
  member             = local.tasks_agent
}

resource "google_pubsub_topic" "event" {
  for_each                   = toset(local.topics)
  name                       = "${var.prefix}-${each.key}"
  message_retention_duration = "604800s"
}

resource "google_pubsub_topic_iam_member" "publisher" {
  for_each = local.publishers
  topic    = google_pubsub_topic.event[each.value.topic].name
  role     = "roles/pubsub.publisher"
  member   = "serviceAccount:${var.service_accounts[each.value.account]}"
}

# Without a subscription, dead letters would simply be discarded.
resource "google_pubsub_subscription" "dead_letter_review" {
  name                       = "${var.prefix}-dead-letters-review"
  topic                      = google_pubsub_topic.event["dead-letters"].id
  message_retention_duration = "604800s"
  ack_deadline_seconds       = 60

  expiration_policy {
    ttl = ""
  }
}

resource "google_pubsub_topic" "gmail" {
  name                       = "${var.prefix}-gmail-notifications"
  message_retention_duration = "604800s"
}

# Gmail publishes as this Google-owned account. An organisation policy for domain-restricted
# sharing can reject the grant. docs/EXTERNAL_DEPENDENCIES.md covers the exemption.
resource "google_pubsub_topic_iam_member" "gmail_publisher" {
  topic  = google_pubsub_topic.gmail.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:gmail-api-push@system.gserviceaccount.com"
}

resource "google_service_account_iam_member" "pubsub_agent_signs_push_tokens" {
  service_account_id = var.service_account_ids["pubsub-push"]
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.pubsub_agent
}

resource "google_pubsub_subscription" "gmail_push" {
  count = var.enable_gmail_push ? 1 : 0
  name  = "${var.prefix}-gmail-push"
  topic = google_pubsub_topic.gmail.id

  # The handler acks by enqueuing. Delivery is at-least-once, so it dedupes on messageId.
  ack_deadline_seconds       = 60
  message_retention_duration = "604800s"

  expiration_policy {
    ttl = ""
  }

  push_config {
    push_endpoint = var.gmail_push_endpoint

    oidc_token {
      service_account_email = var.service_accounts["pubsub-push"]
      audience              = var.gmail_push_audience
    }
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.event["dead-letters"].id
    max_delivery_attempts = 10
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

resource "google_pubsub_topic_iam_member" "agent_dead_letters" {
  topic  = google_pubsub_topic.event["dead-letters"].name
  role   = "roles/pubsub.publisher"
  member = local.pubsub_agent
}

resource "google_pubsub_subscription_iam_member" "agent_forwards_dead_letters" {
  count        = var.enable_gmail_push ? 1 : 0
  subscription = google_pubsub_subscription.gmail_push[0].name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}

output "queue_ids" {
  value = { for name, queue in google_cloud_tasks_queue.queue : name => queue.id }
}
output "topic_ids" {
  value = { for name, topic in google_pubsub_topic.event : name => topic.id }
}
output "gmail_topic_id" { value = google_pubsub_topic.gmail.id }
