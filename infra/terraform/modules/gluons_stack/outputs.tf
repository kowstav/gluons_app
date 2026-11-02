output "database_instance" { value = module.database.connection_name }
output "database_instance_name" { value = module.database.instance_name }
output "database_private_ip" { value = module.database.private_ip }
output "image_repository" { value = module.storage.image_repository }
output "kms_key" { value = module.security.kms_key_id }
output "secret_ids" { value = module.security.secret_ids }
output "service_accounts" { value = module.identities.emails }
output "backend_urls" { value = module.services.backend_urls }
output "web_service" { value = module.services.web_url }
output "edge_ip_address" {
  value = local.load_balancer_on ? module.edge[0].ip_address : null
}
output "workload_identity_provider" {
  value = var.github_repository != "" ? module.ci_identity[0].provider_name : null
}
