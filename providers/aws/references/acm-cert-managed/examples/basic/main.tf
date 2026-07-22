module "example" {
  source = "../../"

  domain_name               = var.domain_name
  subject_alternative_names = var.subject_alternative_names
  validation_method         = var.validation_method
  key_algorithm             = var.key_algorithm
  wait_for_validation       = var.wait_for_validation
  tags                      = var.tags

  # The cross-account remote-state read gate is left at its empty default, so the
  # standalone example creates zero aws_kms_grant and zero aws_s3_bucket_policy and
  # performs no plan-time AWS read. The prod acm-collector / acm-portal leaf enables it.
}

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
