output "fqdn" {
  description = "The FQDN of the DNS record. Consumed by aws_acm_certificate_validation.validation_record_fqdns."
  value       = aws_route53_record.this.fqdn
}

output "name" {
  description = "The DNS name of the record."
  value       = aws_route53_record.this.name
}

output "record_type" {
  description = "The DNS record type."
  value       = aws_route53_record.this.type
}

# ---------------------------------------------------------------------------
# Accepted interface inputs -- surfaced for caller introspection and to assert
# interface stability. These inputs are accepted at the module interface for
# call-site symmetry / toggles that this module does not consume internally
# (aws_route53_record does not support tags, per D3).
# ---------------------------------------------------------------------------

output "accepted_inputs" {
  description = "Inputs accepted at the module interface for call-site symmetry / toggles that this module does not consume internally; surfaced for caller introspection and to assert interface stability."
  value = {
    tags           = var.tags
    managed_by_tag = var.managed_by_tag
    module_tag     = var.module_tag
  }
}
