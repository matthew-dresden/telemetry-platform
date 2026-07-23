output "zone_id" {
  description = "The Route 53 hosted zone ID. Downstream dependency reads resolve via dependency.zone_id (D37)."
  value       = module.zone.zone_id
}

output "name_servers" {
  description = "List of exactly four authoritative name servers for the hosted zone."
  value       = module.zone.name_servers
}

output "zone_arn" {
  description = "The Amazon Resource Name (ARN) of the Route 53 hosted zone."
  value       = module.zone.arn
}

output "kms_key_arn" {
  description = "The ARN of the single prod DNS/cert customer-managed KMS key (docs/terragrunt-concepts.md). Downstream dependency reads resolve via dependency.kms_key_arn (D37)."
  value       = module.kms.key_arn
}

output "kms_key_id" {
  description = "The globally unique ID of the single prod DNS/cert customer-managed KMS key (docs/terragrunt-concepts.md). Downstream dependency reads resolve via dependency.kms_key_id (D37)."
  value       = module.kms.key_id
}
