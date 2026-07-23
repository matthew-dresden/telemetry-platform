output "zone_id" {
  description = "The hosted zone ID."
  value       = module.example.zone_id
}

output "name_servers" {
  description = "A list of name servers for the hosted zone."
  value       = module.example.name_servers
}

output "arn" {
  description = "The Amazon Resource Name (ARN) of the hosted zone."
  value       = module.example.arn
}

output "zone_name" {
  description = "The DNS name of the hosted zone."
  value       = module.example.zone_name
}
