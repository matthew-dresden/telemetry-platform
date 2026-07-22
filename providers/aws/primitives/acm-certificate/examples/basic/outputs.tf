output "certificate_arn" {
  description = "The Amazon Resource Name (ARN) of the certificate."
  value       = module.example.certificate_arn
}

output "domain_validation_options" {
  description = "Set of domain validation objects for completing certificate validation in the consuming unit."
  value       = module.example.domain_validation_options
}

output "certificate_domain_name" {
  description = "The domain name for which the certificate is issued."
  value       = module.example.certificate_domain_name
}

output "certificate_status" {
  description = "Status of the certificate."
  value       = module.example.certificate_status
}
