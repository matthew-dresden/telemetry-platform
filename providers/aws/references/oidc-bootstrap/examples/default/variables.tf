variable "github_oidc_provider_arn" {
  type        = string
  description = "(Optional) ARN of the pre-existing GitHub Actions OIDC identity provider. When empty, the example computes it from the current account ID. Inject a real ARN in CI to point to the actual provider."
  default     = ""
}

variable "roles" {
  type = map(object({
    sub                 = string
    managed_policy_arns = optional(list(string), [])
    inline_policies     = optional(map(string), {})
    description         = optional(string, "")
  }))
  description = "(Required) Map of role name to role configuration. Each entry creates one OIDC assume-role."
  default = {
    "telemetry-platform-gha-tg-sandbox" = {
      sub         = "repo:matthew-dresden/telemetry-platform:environment:sandbox"
      description = "GitHub Actions Terragrunt role for the sandbox account"
    }
  }
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "oidc-bootstrap-module-testing"
  }
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Value for the ManagedBy tag."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Value for the Module tag."
  default     = "oidc-bootstrap"
}

variable "project_tag" {
  type        = string
  description = "(Required) Value for the Project default tag applied to all resources via the provider default_tags block. Supplied by terratest at run time; the offline tfvars value is telemetry-platform."
}

variable "terratest_run_id" {
  type        = string
  description = "(Required) Unique run identifier injected by terratest for scoped-destroy (D-2/D-4). No default -- must be supplied explicitly so an unset run id is caught at plan time."
}
