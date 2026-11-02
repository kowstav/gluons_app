# External HTTPS load balancer with Cloud Armor.
#
# One API origin. The app serves OAuth and /mcp on GLUONS_PUBLIC_API_URL, and the MCP SDK
# rejects any other Host header, so api, mcp and webhooks all sit behind one hostname and
# the path decides which Cloud Run service answers. Cloud Armor only protects traffic that
# passes through here, which is why public services use load-balancer-only ingress when
# this module is on.
terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

variable "prefix" { type = string }
variable "region" { type = string }
variable "api_host" { type = string }
variable "web_host" { type = string }
variable "service_names" {
  type        = map(string)
  description = "Cloud Run service name for api, mcp, webhooks and web."
}
variable "rate_limit_per_minute" { type = number }

resource "google_compute_security_policy" "edge" {
  name        = "${var.prefix}-edge"
  description = "Rate limit, internal path block and WAF signatures in preview."

  rule {
    action      = "deny(403)"
    priority    = 100
    description = "Worker endpoints are never public, even though the application also checks them."

    match {
      expr {
        expression = "request.path.startsWith('/internal')"
      }
    }
  }

  rule {
    action      = "throttle"
    priority    = 1000
    description = "Per-IP limit. Assistant hosts share egress addresses, so watch for false positives."

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }

    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"

      rate_limit_threshold {
        count        = var.rate_limit_per_minute
        interval_sec = 60
      }
    }
  }

  rule {
    action      = "deny(403)"
    priority    = 2000
    preview     = true
    description = "Preview only until signed webhooks and MCP payloads are shown not to trigger it."

    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1}) || evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1})"
      }
    }
  }

  rule {
    action      = "allow"
    priority    = 2147483647
    description = "Default rule."

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

resource "google_compute_region_network_endpoint_group" "service" {
  for_each              = var.service_names
  name                  = "${var.prefix}-${each.key}"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = each.value
  }
}

resource "google_compute_backend_service" "service" {
  for_each              = var.service_names
  name                  = "${var.prefix}-${each.key}"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.edge.id

  backend {
    group = google_compute_region_network_endpoint_group.service[each.key].id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_url_map" "https" {
  name            = "${var.prefix}-https"
  default_service = google_compute_backend_service.service["web"].id

  host_rule {
    hosts        = [var.api_host]
    path_matcher = "api"
  }

  host_rule {
    hosts        = [var.web_host]
    path_matcher = "web"
  }

  path_matcher {
    name            = "api"
    default_service = google_compute_backend_service.service["api"].id

    path_rule {
      paths   = ["/mcp", "/mcp/*"]
      service = google_compute_backend_service.service["mcp"].id
    }

    path_rule {
      paths   = ["/v1/webhooks/*"]
      service = google_compute_backend_service.service["webhooks"].id
    }
  }

  path_matcher {
    name            = "web"
    default_service = google_compute_backend_service.service["web"].id
  }
}

resource "google_compute_managed_ssl_certificate" "edge" {
  name = "${var.prefix}-edge"

  managed {
    domains = [var.api_host, var.web_host]
  }
}

resource "google_compute_target_https_proxy" "edge" {
  name             = "${var.prefix}-https"
  url_map          = google_compute_url_map.https.id
  ssl_certificates = [google_compute_managed_ssl_certificate.edge.id]
}

resource "google_compute_global_address" "edge" {
  name = "${var.prefix}-edge"
}

resource "google_compute_global_forwarding_rule" "https" {
  name                  = "${var.prefix}-https"
  target                = google_compute_target_https_proxy.edge.id
  ip_address            = google_compute_global_address.edge.id
  port_range            = "443"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_url_map" "redirect" {
  name = "${var.prefix}-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "redirect" {
  name    = "${var.prefix}-http-redirect"
  url_map = google_compute_url_map.redirect.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${var.prefix}-http-redirect"
  target                = google_compute_target_http_proxy.redirect.id
  ip_address            = google_compute_global_address.edge.id
  port_range            = "80"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

output "ip_address" {
  description = "Point the api and web DNS records here."
  value       = google_compute_global_address.edge.address
}
