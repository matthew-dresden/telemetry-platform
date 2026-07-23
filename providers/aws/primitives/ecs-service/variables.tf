variable "name" {
  type        = string
  description = "(Required) ECS service name. Must match ^[a-zA-Z0-9_-]+$ and be 255 characters or fewer."

  validation {
    condition     = length(var.name) >= 1 && length(var.name) <= 255 && can(regex("^[a-zA-Z0-9_-]+$", var.name))
    error_message = "name must be 1-255 characters and contain only alphanumeric characters, underscores, and hyphens."
  }
}

variable "cluster_arn" {
  type        = string
  description = "(Required) ARN of the ECS cluster where this service runs."

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:ecs:[a-z0-9-]+:[0-9]{12}:cluster/", var.cluster_arn))
    error_message = "cluster_arn must be a valid ECS cluster ARN."
  }
}

variable "task_cpu" {
  type        = number
  description = "(Optional) CPU units for the task. Must be a valid Fargate CPU value (256, 512, 1024, 2048, 4096). Defaults to 512 per D44."
  default     = 512

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096], var.task_cpu)
    error_message = "task_cpu must be one of 256, 512, 1024, 2048, 4096 (valid Fargate CPU values)."
  }
}

variable "task_memory" {
  type        = number
  description = "(Optional) Memory (MiB) for the task. Must be >= 512 and a valid Fargate combination for task_cpu. Defaults to 1024 per D44."
  default     = 1024

  validation {
    condition     = var.task_memory >= 512
    error_message = "task_memory must be at least 512 MiB."
  }
}

variable "execution_role_arn" {
  type        = string
  description = "(Required) ARN of the IAM role used by ECS to pull images and write logs."

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:role/", var.execution_role_arn))
    error_message = "execution_role_arn must be a valid IAM role ARN."
  }
}

variable "task_role_arn" {
  type        = string
  description = "(Required) ARN of the IAM role assumed by the task for AWS API calls."

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:role/", var.task_role_arn))
    error_message = "task_role_arn must be a valid IAM role ARN."
  }
}

variable "container_definitions" {
  type        = string
  description = "(Required) JSON-encoded list of container definitions. Must be valid JSON. Container config must reference SSM -- not baked into the image."

  validation {
    condition     = can(jsondecode(var.container_definitions))
    error_message = "container_definitions must be a valid JSON string."
  }
}

variable "desired_count" {
  type        = number
  description = "(Optional) Desired number of tasks. Defaults to 1 for basic (no ALB) deployments."
  default     = 1

  validation {
    condition     = var.desired_count >= 1
    error_message = "desired_count must be at least 1."
  }
}

variable "subnet_ids" {
  type        = list(string)
  description = "(Required) List of subnet IDs for the ECS service. At least 2 subnets across availability zones are required."

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "subnet_ids must contain at least 2 subnets across availability zones."
  }
}

variable "security_group_ids" {
  type        = list(string)
  description = "(Required) List of security group IDs for the ECS service tasks. Must be non-empty."

  validation {
    condition     = length(var.security_group_ids) >= 1
    error_message = "security_group_ids must contain at least one security group ID."
  }
}

variable "assign_public_ip" {
  type        = bool
  description = "(Optional) Whether to assign a public IP to Fargate tasks. Defaults to false for private subnets."
  default     = false
}

variable "load_balancer" {
  type = object({
    target_group_arn = string
    container_name   = string
    container_port   = number
  })
  description = "(Optional) ALB load balancer configuration. When non-null, the service is attached to the specified target group."
  default     = null
}

variable "health_check_grace_period_seconds" {
  type        = number
  description = "(Optional) Seconds to ignore failing load balancer health checks on newly instantiated tasks. Only applied when load_balancer is non-null. Defaults to 60."
  default     = 60

  validation {
    condition     = var.health_check_grace_period_seconds >= 0
    error_message = "health_check_grace_period_seconds must be >= 0."
  }
}

variable "force_new_deployment" {
  type        = bool
  description = "(Optional) Whether to force a new deployment of the service on every apply. Defaults to true."
  default     = true
}

variable "enable_autoscaling" {
  type        = bool
  description = "(Optional) Whether to enable Application Auto Scaling for the service. When true, autoscaling must be non-null."
  default     = false
}

variable "autoscaling" {
  type = object({
    min_capacity         = number
    max_capacity         = number
    cpu_target_percent   = optional(number, 60)
    request_count_target = optional(number)
    alb_resource_label   = optional(string)
    scale_in_cooldown    = optional(number, 300)
    scale_out_cooldown   = optional(number, 60)
  })
  description = "(Optional) Auto Scaling configuration. Required when enable_autoscaling is true. A CPU target-tracking policy (cpu_target_percent) is always applied. Additionally, when request_count_target AND alb_resource_label are both set, an ALBRequestCountPerTarget target-tracking policy is also applied -- load-proportional and faster-reacting than CPU (recommended primary signal for request-driven services; CPU stays as a safety ceiling). alb_resource_label is the predefined-metric ResourceLabel in the form 'app/<alb-name>/<alb-id>/targetgroup/<tg-name>/<tg-id>'. max_capacity must be >= min_capacity."
  default     = null

  validation {
    condition     = var.autoscaling == null || var.autoscaling.max_capacity >= var.autoscaling.min_capacity
    error_message = "autoscaling.max_capacity must be >= autoscaling.min_capacity."
  }

  validation {
    condition     = var.autoscaling == null || (var.autoscaling.request_count_target == null) == (var.autoscaling.alb_resource_label == null)
    error_message = "autoscaling.request_count_target and autoscaling.alb_resource_label must be set together (both or neither) to enable ALBRequestCountPerTarget scaling."
  }

  validation {
    condition     = var.autoscaling == null || var.autoscaling.request_count_target == null || var.autoscaling.request_count_target > 0
    error_message = "autoscaling.request_count_target must be > 0 when set."
  }
}

variable "create_log_group" {
  type        = bool
  description = "(Optional) Whether to create a CloudWatch log group for the service. Defaults to true."
  default     = true
}

variable "log_group_kms_key_arn" {
  type        = string
  description = "(Optional) ARN of the KMS CMK used to encrypt the CloudWatch log group at rest. When null, the log group uses the default CloudWatch Logs service key. Supply a CMK ARN to satisfy customer-managed-key encryption requirements. Must match ^arn:aws:kms: when provided."
  default     = null

  validation {
    condition     = var.log_group_kms_key_arn == null || can(regex("^arn:aws:kms:", var.log_group_kms_key_arn))
    error_message = "log_group_kms_key_arn must be a valid KMS key ARN matching ^arn:aws:kms: when provided."
  }
}

variable "log_group_retention_days" {
  type        = number
  description = "(Optional) Retention period in days for the CloudWatch log group."
  default     = 30

  validation {
    condition     = contains([0, 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_group_retention_days)
    error_message = "log_group_retention_days must be a valid CloudWatch Logs retention value (0 = never expire)."
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
  default     = "ecs-service"
}
