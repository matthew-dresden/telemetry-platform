output "certificate_arn" {
  description = "The Amazon Resource Name (ARN) of the certificate. Downstream dependency reads (collector-ingestion / portal validation) resolve via dependency.certificate_arn (D37)."
  value       = module.certificate.certificate_arn
}

output "domain_validation_options" {
  description = "Set of domain validation objects which can be used to complete certificate validation. The dns-owner pretty/validate units and the downstream collector-ingestion/portal validation units consume this (per decision D24)."
  value       = module.certificate.domain_validation_options
}

output "certificate_domain_name" {
  description = "The domain name for which the certificate is issued."
  value       = module.certificate.certificate_domain_name
}

output "certificate_status" {
  description = "Status of the certificate."
  value       = module.certificate.certificate_status
}
