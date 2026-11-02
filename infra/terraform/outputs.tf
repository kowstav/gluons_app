output "database_private_ip" { value = google_sql_database_instance.main.private_ip_address }
output "database_instance" { value = google_sql_database_instance.main.connection_name }
output "image_repository" { value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}" }
output "api_service" { value = var.deploy_services ? google_cloud_run_v2_service.api[0].uri : null }
output "web_service" { value = var.deploy_services ? google_cloud_run_v2_service.web[0].uri : null }
output "kms_key" { value = google_kms_crypto_key.vault.id }
