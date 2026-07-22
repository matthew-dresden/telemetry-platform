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
  description = "(Required) Value for the Project default tag applied to all resources via the provider default_tags block. Supplied by terratest at run time; the offline tfvars value is telemetry-platform."
}

variable "terratest_run_id" {
  type        = string
  description = "(Required) Unique run identifier injected by terratest for scoped-destroy (D-2/D-4). No default -- must be supplied explicitly so an unset run id is caught at plan time."
}
