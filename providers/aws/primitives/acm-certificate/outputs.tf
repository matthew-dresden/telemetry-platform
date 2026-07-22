output "certificate_arn" {
  description = "The Amazon Resource Name (ARN) of the certificate."
  value       = aws_acm_certificate.this.arn
}

output "domain_validation_options" {
  description = "Set of domain validation objects which can be used to complete certificate validation. Feed this into the consuming unit's aws_acm_certificate_validation resource (per decision D24)."
  value       = aws_acm_certificate.this.domain_validation_options
}

output "certificate_domain_name" {
  description = "The domain name for which the certificate is issued."
  value       = aws_acm_certificate.this.domain_name
}

output "certificate_status" {
  description = "Status of the certificate."
  value       = aws_acm_certificate.this.status
}

output "accepted_inputs" {
  description = "Inputs accepted at the module interface for call-site symmetry but NOT consumed internally (per decision D24, certificate validation is owned by the consuming unit); surfaced for caller introspection and to assert interface stability."
  value = {
    wait_for_validation     = var.wait_for_validation
    validation_record_fqdns = var.validation_record_fqdns
  }
}
