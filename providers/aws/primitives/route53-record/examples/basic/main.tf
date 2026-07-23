module "example" {
  source = "../../"

  zone_id = var.zone_id
  name    = var.name
  type    = var.type
  records = var.records
  ttl     = var.ttl
  tags    = var.tags
}

output "fqdn" {
  description = "The FQDN of the DNS record."
  value       = module.example.fqdn
}

output "name" {
  description = "The DNS name of the record."
  value       = module.example.name
}

output "record_type" {
  description = "The DNS record type."
  value       = module.example.record_type
}
