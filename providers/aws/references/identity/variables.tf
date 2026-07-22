# ---------------------------------------------------------------------------
# Per-block child module source variables (const = true, in-repo relative-path defaults).
# Each iam-role block gets its own distinct variable to enable per-instance pinning (AC-5).
# ---------------------------------------------------------------------------

variable "analyst_source" {
  type        = string
  const       = true
  description = "Source path for the iam-role primitive child module used as the TelemetryAnalyst role. Defaults to the in-repo relative path."
  default     = "../../primitives/iam-role"
}

variable "admin_source" {
  type        = string
  const       = true
  description = "Source path for the iam-role primitive child module used as the TelemetryAdmin role. Defaults to the in-repo relative path."
  default     = "../../primitives/iam-role"
}

variable "ecs_task_source" {
  type        = string
  const       = true
  description = "Source path for the iam-role primitive child module used as the ECS task role. Defaults to the in-repo relative path."
  default     = "../../primitives/iam-role"
}

variable "ecs_task_execution_source" {
  type        = string
  const       = true
  description = "Source path for the iam-role primitive child module used as the ECS task execution role. Defaults to the in-repo relative path."
  default     = "../../primitives/iam-role"
}

# ---------------------------------------------------------------------------
# Identity Center group name inputs (D8 -- viewer/author/admin groups)
# Every group name must be non-empty so an undefined group fails fast on the
# input contract before any role is created (AC-13).
# ---------------------------------------------------------------------------

variable "viewer_group_name" {
  type        = string
  description = "(Required) IAM Identity Center group name for the viewer (read-only) group. Must be non-empty."

  validation {
    condition     = length(var.viewer_group_name) > 0
    error_message = "viewer_group_name must be non-empty -- an undefined viewer group name is rejected before any role is created."
  }
}

variable "author_group_name" {
  type        = string
  description = "(Required) IAM Identity Center group name for the author (analyst) group. Must be non-empty."

  validation {
    condition     = length(var.author_group_name) > 0
    error_message = "author_group_name must be non-empty -- an undefined author group name is rejected before any role is created."
  }
}

variable "admin_group_name" {
  type        = string
  description = "(Required) IAM Identity Center group name for the admin group. Must be non-empty."

  validation {
    condition     = length(var.admin_group_name) > 0
    error_message = "admin_group_name must be non-empty -- an undefined admin group name is rejected before any role is created."
  }
}

# ---------------------------------------------------------------------------
# QuickSight permission-set role name inputs (docs/terragrunt-concepts.md)
# ---------------------------------------------------------------------------

variable "analyst_role_name" {
  type        = string
  description = "(Required) Name for the TelemetryAnalyst IAM role (Author permission set). Must be 64 characters or fewer."

  validation {
    condition     = length(var.analyst_role_name) > 0 && length(var.analyst_role_name) <= 64
    error_message = "analyst_role_name must be between 1 and 64 characters."
  }
}

variable "admin_role_name" {
  type        = string
  description = "(Required) Name for the TelemetryAdmin IAM role (Admin permission set). Must be 64 characters or fewer."

  validation {
    condition     = length(var.admin_role_name) > 0 && length(var.admin_role_name) <= 64
    error_message = "admin_role_name must be between 1 and 64 characters."
  }
}

# ---------------------------------------------------------------------------
# QuickSight permission-set inline policy inputs (docs/terragrunt-concepts.md,
# D2r/D3r)
# ---------------------------------------------------------------------------

variable "analyst_inline_policies" {
  type        = map(string)
  description = "(Required) Map of inline policy name to JSON policy document for the TelemetryAnalyst role. D2r: read-only Athena/Glue/S3-results. Must be a non-empty map with valid JSON values."

  validation {
    condition     = length(var.analyst_inline_policies) > 0
    error_message = "analyst_inline_policies must be non-empty -- at least one inline policy is required for the TelemetryAnalyst role."
  }

  validation {
    condition     = alltrue([for v in values(var.analyst_inline_policies) : can(jsondecode(v))])
    error_message = "Every value in analyst_inline_policies must be a valid JSON string."
  }
}

variable "admin_inline_policies" {
  type        = map(string)
  description = "(Required) Map of inline policy name to JSON policy document for the TelemetryAdmin role. D3r: manage QuickSight/datasets. Must be a non-empty map with valid JSON values."

  validation {
    condition     = length(var.admin_inline_policies) > 0
    error_message = "admin_inline_policies must be non-empty -- at least one inline policy is required for the TelemetryAdmin role."
  }

  validation {
    condition     = alltrue([for v in values(var.admin_inline_policies) : can(jsondecode(v))])
    error_message = "Every value in admin_inline_policies must be a valid JSON string."
  }
}

# ---------------------------------------------------------------------------
# ECS task/execution role name inputs (docs/terragrunt-concepts.md, consumed by
# collector-ingestion)
# ---------------------------------------------------------------------------

variable "ecs_task_role_name" {
  type        = string
  description = "(Optional) Name for the ECS task role consumed by collector-ingestion (docs/terragrunt-concepts.md). When non-empty, the ecs_task_role module is created and ecs_task_role_arn is non-null. Must be 64 characters or fewer when provided."
  default     = ""

  validation {
    condition     = length(var.ecs_task_role_name) <= 64
    error_message = "ecs_task_role_name must be 64 characters or fewer when provided."
  }
}

variable "ecs_task_execution_role_name" {
  type        = string
  description = "(Optional) Name for the ECS execution role consumed by collector-ingestion (docs/terragrunt-concepts.md). When non-empty, the ecs_task_execution_role module is created and ecs_task_execution_role_arn is non-null. Must be 64 characters or fewer when provided."
  default     = ""

  validation {
    condition     = length(var.ecs_task_execution_role_name) <= 64
    error_message = "ecs_task_execution_role_name must be 64 characters or fewer when provided."
  }
}

variable "ecs_task_inline_policies" {
  type        = map(string)
  description = "(Optional) Map of inline policy name to JSON policy document for the ECS task role (docs/terragrunt-concepts.md -- ARN-scoped firehose/KMS/SSM/CloudWatch, no S3/Athena/Glue). Required when ecs_task_role_name is non-empty."
  default     = {}

  validation {
    condition     = alltrue([for v in values(var.ecs_task_inline_policies) : can(jsondecode(v))])
    error_message = "Every value in ecs_task_inline_policies must be a valid JSON string."
  }
}

variable "ecs_task_execution_inline_policies" {
  type        = map(string)
  description = "(Optional) Map of inline policy name to JSON policy document for the ECS execution role (docs/terragrunt-concepts.md -- ECR pull, ADOT log group logs, AOT_CONFIG_CONTENT SSM, telemetry-config KMS). Required when ecs_task_execution_role_name is non-empty."
  default     = {}

  validation {
    condition     = alltrue([for v in values(var.ecs_task_execution_inline_policies) : can(jsondecode(v))])
    error_message = "Every value in ecs_task_execution_inline_policies must be a valid JSON string."
  }
}

variable "ecs_task_assume_role_policy_json" {
  type        = string
  description = "(Optional) Trust policy JSON for the ECS task role. Required when ecs_task_role_name is non-empty. Must be valid JSON."
  default     = null

  validation {
    condition     = var.ecs_task_assume_role_policy_json == null || can(jsondecode(var.ecs_task_assume_role_policy_json))
    error_message = "ecs_task_assume_role_policy_json must be valid JSON when provided."
  }
}

variable "ecs_task_execution_assume_role_policy_json" {
  type        = string
  description = "(Optional) Trust policy JSON for the ECS execution role. Required when ecs_task_execution_role_name is non-empty. Must be valid JSON."
  default     = null

  validation {
    condition     = var.ecs_task_execution_assume_role_policy_json == null || can(jsondecode(var.ecs_task_execution_assume_role_policy_json))
    error_message = "ecs_task_execution_assume_role_policy_json must be valid JSON when provided."
  }
}

# ---------------------------------------------------------------------------
# Permission-set account input (docs/terragrunt-concepts.md)
# ---------------------------------------------------------------------------

variable "permission_set_account_id" {
  type        = string
  description = "(Required) AWS account ID hosting the IAM Identity Center permission sets. Used in the trust policy principal for the analyst/admin roles."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.permission_set_account_id))
    error_message = "permission_set_account_id must be a 12-digit AWS account ID."
  }
}

variable "analyst_assume_role_policy_json" {
  type        = string
  description = "(Required) Trust policy JSON for the TelemetryAnalyst role. Must be valid JSON."

  validation {
    condition     = can(jsondecode(var.analyst_assume_role_policy_json))
    error_message = "analyst_assume_role_policy_json must be a valid JSON string."
  }
}

variable "admin_assume_role_policy_json" {
  type        = string
  description = "(Required) Trust policy JSON for the TelemetryAdmin role. Must be valid JSON."

  validation {
    condition     = can(jsondecode(var.admin_assume_role_policy_json))
    error_message = "admin_assume_role_policy_json must be a valid JSON string."
  }
}

# ---------------------------------------------------------------------------
# Shared tagging inputs
# ---------------------------------------------------------------------------

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources created by this reference module."
  default     = {}
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Value for the ManagedBy tag."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Value for the Module tag identifying the source reference module."
  default     = "identity"
}
