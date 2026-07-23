# Per decision D24, aws_acm_certificate_validation is NOT created in this primitive.
# The consuming unit is responsible for feeding domain_validation_options into its own
# aws_acm_certificate_validation resource. The wait_for_validation and
# validation_record_fqdns variables are reserved inputs for that consuming unit pattern.
resource "aws_acm_certificate" "this" {
  domain_name               = var.domain_name
  subject_alternative_names = var.subject_alternative_names
  validation_method         = var.validation_method
  key_algorithm             = var.key_algorithm

  tags = local.common_tags

  lifecycle {
    create_before_destroy = true
  }
}
