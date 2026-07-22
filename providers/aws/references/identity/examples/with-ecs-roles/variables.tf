variable "viewer_group_name" {
  type        = string
  description = "(Required) IAM Identity Center group name for the viewer group."
  default     = "telemetry-viewers"

  validation {
    condition     = length(var.viewer_group_name) > 0
    error_message = "viewer_group_name must be non-empty."
  }
}

variable "author_group_name" {
  type        = string
  description = "(Required) IAM Identity Center group name for the author (analyst) group."
  default     = "telemetry-authors"

  validation {
    condition     = length(var.author_group_name) > 0
    error_message = "author_group_name must be non-empty."
  }
}

variable "admin_group_name" {
  type        = string
  description = "(Required) IAM Identity Center group name for the admin group."
  default     = "telemetry-admins"

  validation {
    condition     = length(var.admin_group_name) > 0
    error_message = "admin_group_name must be non-empty."
  }
}

variable "analyst_role_name" {
  type        = string
  description = "(Required) Name for the TelemetryAnalyst IAM role."
  default     = "telemetry-analyst-ecs-test"

  validation {
    condition     = length(var.analyst_role_name) > 0 && length(var.analyst_role_name) <= 64
    error_message = "analyst_role_name must be between 1 and 64 characters."
  }
}

variable "admin_role_name" {
  type        = string
  description = "(Required) Name for the TelemetryAdmin IAM role."
  default     = "telemetry-admin-ecs-test"

  validation {
    condition     = length(var.admin_role_name) > 0 && length(var.admin_role_name) <= 64
    error_message = "admin_role_name must be between 1 and 64 characters."
  }
}

variable "ecs_task_role_name" {
  type        = string
  description = "(Required) Name for the ECS task role (docs/terragrunt-concepts.md)."
  default     = "telemetry-adot-task-test"

  validation {
    condition     = length(var.ecs_task_role_name) > 0 && length(var.ecs_task_role_name) <= 64
    error_message = "ecs_task_role_name must be between 1 and 64 characters."
  }
}

variable "ecs_task_execution_role_name" {
  type        = string
  description = "(Required) Name for the ECS execution role (docs/terragrunt-concepts.md)."
  default     = "telemetry-ecs-execution-test"

  validation {
    condition     = length(var.ecs_task_execution_role_name) > 0 && length(var.ecs_task_execution_role_name) <= 64
    error_message = "ecs_task_execution_role_name must be between 1 and 64 characters."
  }
}

variable "firehose_stream_arn" {
  type        = string
  description = "(Required) ARN of the Firehose delivery stream for the ECS task role policy (docs/terragrunt-concepts.md)."
  default     = "arn:aws:firehose:us-east-1:333333333333:deliverystream/telemetry-ingest-test"

  validation {
    condition     = can(regex("^arn:aws:firehose:", var.firehose_stream_arn))
    error_message = "firehose_stream_arn must be a valid Firehose ARN."
  }
}

variable "telemetry_data_kms_key_arn" {
  type        = string
  description = "(Required) ARN of the telemetry-data KMS key for the ECS task role policy (docs/terragrunt-concepts.md)."
  default     = "arn:aws:kms:us-east-1:333333333333:key/00000000-0000-0000-0000-000000000001"

  validation {
    condition     = can(regex("^arn:aws:kms:", var.telemetry_data_kms_key_arn))
    error_message = "telemetry_data_kms_key_arn must be a valid KMS ARN."
  }
}

variable "adot_metrics_namespace" {
  type        = string
  description = "(Required) CloudWatch namespace for the ADOT metrics (docs/terragrunt-concepts.md)."
  default     = "TelemetryADOT"

  validation {
    condition     = length(var.adot_metrics_namespace) > 0
    error_message = "adot_metrics_namespace must be non-empty."
  }
}

variable "adot_log_group_arn" {
  type        = string
  description = "(Required) ARN of the ADOT log group for the ECS execution role policy (docs/terragrunt-concepts.md)."
  default     = "arn:aws:logs:us-east-1:333333333333:log-group:/telemetry/adot:*"

  validation {
    condition     = can(regex("^arn:aws:logs:", var.adot_log_group_arn))
    error_message = "adot_log_group_arn must be a valid CloudWatch Logs ARN."
  }
}

variable "aot_config_parameter_arn" {
  type        = string
  description = "(Required) ARN of the AOT_CONFIG_CONTENT SSM parameter for the ECS execution role policy (docs/terragrunt-concepts.md)."
  default     = "arn:aws:ssm:us-east-1:333333333333:parameter/telemetry/prod/ingest/AOT_CONFIG_CONTENT"

  validation {
    condition     = can(regex("^arn:aws:ssm:", var.aot_config_parameter_arn))
    error_message = "aot_config_parameter_arn must be a valid SSM ARN."
  }
}

variable "telemetry_config_kms_key_arn" {
  type        = string
  description = "(Required) ARN of the telemetry-config KMS key for the ECS execution role policy (docs/terragrunt-concepts.md)."
  default     = "arn:aws:kms:us-east-1:333333333333:key/00000000-0000-0000-0000-000000000002"

  validation {
    condition     = can(regex("^arn:aws:kms:", var.telemetry_config_kms_key_arn))
    error_message = "telemetry_config_kms_key_arn must be a valid KMS ARN."
  }
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "identity-ecs-roles-module-testing"
  }
}

variable "project_tag" {
  type        = string
  description = "(Required) Value for the Project default tag applied to all resources via the provider default_tags block. Supplied by terratest at run time; the offline tfvars value is telemetry-platform."
}

variable "terratest_run_id" {
  type        = string
  description = "(Required) Unique run identifier injected by terratest for scoped-destroy (D-2/D-4). No default -- must be supplied explicitly so an unset run id is caught at plan time."
}
