variable "domain_name" {
  type        = string
  description = "(Required) Fully qualified domain name for the certificate. Must match pattern ^[a-z0-9.-]+$."

  validation {
    condition     = can(regex("^[a-z0-9.-]+$", var.domain_name))
    error_message = "domain_name must contain only lowercase alphanumeric characters, hyphens, and dots."
  }
}

variable "subject_alternative_names" {
  type        = list(string)
  description = "(Optional) List of domains that should be Subject Alternative Names in the issued certificate. Each element must match pattern ^[a-z0-9.*-]+$."
  default     = []

  validation {
    condition     = alltrue([for san in var.subject_alternative_names : can(regex("^[a-z0-9.*-]+$", san))])
    error_message = "Each subject_alternative_names entry must contain only lowercase alphanumeric characters, dots, asterisks, and hyphens."
  }
}

variable "validation_method" {
  type        = string
  description = "(Optional) Method to use for domain validation. Valid values: DNS, EMAIL."
  default     = "DNS"

  validation {
    condition     = contains(["DNS", "EMAIL"], var.validation_method)
    error_message = "validation_method must be one of DNS or EMAIL."
  }
}

variable "key_algorithm" {
  type        = string
  description = "(Optional) Specifies the algorithm of the public and private key pair that your Amazon-issued certificate uses to encrypt data. Valid values: RSA_2048, EC_prime256v1, EC_secp384r1."
  default     = "RSA_2048"

  validation {
    condition     = contains(["RSA_2048", "EC_prime256v1", "EC_secp384r1"], var.key_algorithm)
    error_message = "key_algorithm must be one of RSA_2048, EC_prime256v1, or EC_secp384r1."
  }
}

variable "wait_for_validation" {
  type        = bool
  description = "(Optional) Reserved input for consuming units. This module does NOT create aws_acm_certificate_validation regardless of value (per decision D24). The validation resource must be created in the consuming unit using domain_validation_options from this module's outputs."
  default     = true
}

variable "validation_record_fqdns" {
  type        = list(string)
  description = "(Optional) Reserved for consuming units. List of FQDNs that implement the validation. This input is not used by this module; the consuming unit is responsible for creating aws_acm_certificate_validation using domain_validation_options (per decision D24)."
  default     = []
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources created by this module."
  default     = {}
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Value for the ManagedBy tag."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Value for the Module tag identifying the source module."
  default     = "acm-certificate"
}
