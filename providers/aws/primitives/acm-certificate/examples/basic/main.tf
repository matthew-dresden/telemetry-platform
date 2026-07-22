module "example" {
  source = "../../"

  domain_name               = var.domain_name
  subject_alternative_names = var.subject_alternative_names
  validation_method         = var.validation_method
  key_algorithm             = var.key_algorithm
  wait_for_validation       = var.wait_for_validation
  tags                      = var.tags
}
