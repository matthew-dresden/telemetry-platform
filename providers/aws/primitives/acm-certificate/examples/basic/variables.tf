variable "domain_name" {
  type        = string
  description = "Fully qualified domain name for the certificate."
}

variable "subject_alternative_names" {
  type        = list(string)
  description = "List of domains that should be Subject Alternative Names in the issued certificate."
  default     = []
}

variable "validation_method" {
  type        = string
  description = "Method to use for domain validation. Valid values: DNS, EMAIL."
  default     = "DNS"
}

variable "key_algorithm" {
  type        = string
  description = "Algorithm for the public and private key pair. Valid values: RSA_2048, EC_prime256v1, EC_secp384r1."
  default     = "RSA_2048"
}

variable "wait_for_validation" {
  type        = bool
  description = "Reserved input -- this module does not create aws_acm_certificate_validation regardless of value (per D24)."
  default     = false
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default     = {}
}

variable "project_tag" {
  type        = string
  description = "Project tag value applied to all resources via the provider default_tags block."
}

variable "terratest_run_id" {
  type        = string
  description = "Terratest run identifier applied to all resources via the provider default_tags block."
}
