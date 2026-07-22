# ---------------------------------------------------------------------------
# Module source variables (const = true -- enables source = var.<name>_source)
# Each defaults to the in-repo relative path; can be overridden to a pinned
# git URL at deploy time via use_pinned_module_sources (spec S4.1, G2).
# ---------------------------------------------------------------------------

variable "oidc_role_source" {
  type        = string
  const       = true
  description = "Source path for the iam-role primitive backing GitHub Actions OIDC roles. Defaults to the in-repo relative path from providers/aws/references/oidc-bootstrap/."
  default     = "../../primitives/iam-role"
}

variable "github_oidc_provider_arn" {
  type        = string
  description = "(Optional) ARN of the pre-existing GitHub Actions OIDC identity provider in the target account. Required when any role uses OIDC trust (sub field set). Omit for accounts that only create role-chaining roles (e.g. root dns-writer). The provider must be created as an operator prerequisite (D40); this reference never creates it."
  default     = ""

  validation {
    condition = (
      var.github_oidc_provider_arn == "" ||
      can(regex("^arn:aws:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$", var.github_oidc_provider_arn))
    )
    error_message = "github_oidc_provider_arn must be empty or a valid IAM OIDC provider ARN for token.actions.githubusercontent.com."
  }
}

variable "roles" {
  type = map(object({
    sub                  = optional(string, "")
    managed_policy_arns  = optional(list(string), [])
    inline_policies      = optional(map(string), {})
    description          = optional(string, "")
    max_session_duration = optional(number, 3600)
    trust_policy_json    = optional(string, "")
  }))
  description = "(Required) Map of role name to role configuration. For OIDC roles, set sub to the token.actions.githubusercontent.com:sub claim and provide github_oidc_provider_arn. For role-chaining roles (e.g. root dns-writer), omit sub and provide trust_policy_json with the custom assume-role trust policy. Must contain at least one entry."

  validation {
    condition     = length(var.roles) > 0
    error_message = "roles must contain at least one entry. A bootstrap unit must create at least one role."
  }
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
  default     = "oidc-bootstrap"
}
