variable "namespace" {
  type        = string
  description = "(Required) Fully-qualified namespace used to derive SET-SCOPED SSM parameter paths so coexisting instance sets never collide (design law: every resource name is namespace-derived)."

  validation {
    condition     = can(regex("^[a-z0-9_-]+$", var.namespace))
    error_message = "namespace must contain only lowercase letters, digits, hyphens, and underscores."
  }
}

variable "task_role_source" {
  type        = string
  const       = true
  description = "Source path for the iam-role primitive child module used as the ECS task role. Defaults to the in-repo relative path."
  default     = "../../primitives/iam-role"
}

variable "ssm_source" {
  type        = string
  const       = true
  description = "Source path for the ssm-parameter primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/ssm-parameter"
}

variable "alb_source" {
  type        = string
  const       = true
  description = "Source path for the alb primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/alb"
}

variable "listener_source" {
  type        = string
  const       = true
  description = "Source path for the alb-listener primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/alb-listener"
}

variable "service_source" {
  type        = string
  const       = true
  description = "Source path for the ecs-service primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/ecs-service"
}

variable "cloudwatch_source" {
  type        = string
  const       = true
  description = "Source path for the cloudwatch primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/cloudwatch"
}

variable "service_name" {
  type        = string
  description = "(Required) ECS service name. Must match ^[a-zA-Z0-9_-]+$ and be 255 characters or fewer."

  validation {
    condition     = length(var.service_name) >= 1 && length(var.service_name) <= 255 && can(regex("^[a-zA-Z0-9_-]+$", var.service_name))
    error_message = "service_name must be 1-255 characters and contain only alphanumeric characters, underscores, and hyphens."
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

variable "execution_role_arn" {
  type        = string
  description = "(Required) ARN of the IAM role used by ECS to pull images and write logs."

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:role/", var.execution_role_arn))
    error_message = "execution_role_arn must be a valid IAM role ARN."
  }
}

variable "task_role_name" {
  type        = string
  description = "(Required) Name of the per-service ECS task IAM role. Must be 64 characters or fewer."

  validation {
    condition     = length(var.task_role_name) >= 1 && length(var.task_role_name) <= 64
    error_message = "task_role_name must be 1-64 characters to satisfy the IAM role name limit."
  }
}

variable "task_role_inline_policies" {
  type        = map(string)
  description = "(Optional) Map of inline policy name to JSON policy document for the task role. Each value must be valid JSON."
  default     = {}

  validation {
    condition     = alltrue([for v in values(var.task_role_inline_policies) : can(jsondecode(v))])
    error_message = "Every value in task_role_inline_policies must be a valid JSON string."
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
  description = "(Optional) Desired number of tasks. Defaults to 1 for basic deployments."
  default     = 1

  validation {
    condition     = var.desired_count >= 1
    error_message = "desired_count must be at least 1."
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

variable "enable_autoscaling" {
  type        = bool
  description = "(Optional) Whether to enable Application Auto Scaling for the service. When true, autoscaling must be non-null."
  default     = false
}

variable "autoscaling" {
  type = object({
    min_capacity       = number
    max_capacity       = number
    cpu_target_percent = optional(number, 60)
    scale_in_cooldown  = optional(number, 300)
    scale_out_cooldown = optional(number, 60)
  })
  description = "(Optional) Auto Scaling configuration. Required when enable_autoscaling is true. max_capacity must be >= min_capacity."
  default     = null

  validation {
    condition     = var.autoscaling == null || var.autoscaling.max_capacity >= var.autoscaling.min_capacity
    error_message = "autoscaling.max_capacity must be >= autoscaling.min_capacity."
  }
}

variable "vpc_id" {
  type        = string
  description = "(Optional) VPC ID where the ALB is deployed. Required when alb is non-null."
  default     = null

  validation {
    condition     = var.vpc_id == null || can(regex("^vpc-", var.vpc_id))
    error_message = "vpc_id must start with 'vpc-' when provided."
  }
}

variable "alb" {
  type = object({
    name                   = string
    internal               = optional(bool, true)
    alb_subnet_ids         = list(string)
    alb_security_group_ids = optional(list(string), [])
    create_security_group  = optional(bool, true)
    ingress_cidr_blocks    = optional(list(string), [])
    idle_timeout           = optional(number, 60)
    target_groups = list(object({
      name        = string
      port        = number
      protocol    = string
      target_type = string
      health_check = object({
        path                = string
        port                = optional(string, "traffic-port")
        protocol            = optional(string, "HTTP")
        healthy_threshold   = optional(number, 3)
        unhealthy_threshold = optional(number, 3)
        interval            = optional(number, 30)
        timeout             = optional(number, 5)
        matcher             = optional(string, "200")
      })
    }))
  })
  description = "(Optional) ALB configuration. When non-null, an Application Load Balancer is created for the service."
  default     = null
}

variable "alb_listeners" {
  type = object({
    listeners = list(object({
      name            = string
      port            = number
      protocol        = string
      ssl_policy      = optional(string)
      certificate_arn = optional(string)
      default_action = object({
        type             = string
        target_group_arn = optional(string)
        redirect = optional(object({
          port        = string
          protocol    = string
          status_code = string
        }))
      })
    }))
    listener_rules = optional(list(object({
      listener_name = string
      priority      = number
      conditions = list(object({
        field  = string
        values = list(string)
      }))
      action = object({
        type             = string
        target_group_arn = optional(string)
      })
    })), [])
  })
  description = "(Optional) ALB listener configuration. Required when alb is non-null."
  default     = null
}

variable "ssm_parameters" {
  type = map(object({
    type       = string
    value      = string
    kms_key_id = optional(string)
  }))
  description = "(Optional) Map of SSM parameter logical key to parameter configuration. Each entry creates one SSM parameter. SecureString entries must set kms_key_id."
  default     = {}
}

variable "alarms" {
  type = map(object({
    comparison_operator = string
    evaluation_periods  = number
    metric_name         = string
    namespace           = string
    period              = number
    statistic           = string
    threshold           = number
    alarm_description   = optional(string, "")
    dimensions          = optional(map(string), {})
    alarm_actions       = list(string)
    ok_actions          = list(string)
    treat_missing_data  = optional(string, "missing")
  }))
  description = "(Optional) Map of alarm name to metric alarm configuration for the service. Each alarm must have non-empty alarm_actions and ok_actions."
  default     = {}
}

variable "log_group_retention_days" {
  type        = number
  description = "(Optional) Retention period in days for the CloudWatch log group created by the ecs-service primitive."
  default     = 30

  validation {
    condition     = contains([0, 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_group_retention_days)
    error_message = "log_group_retention_days must be a valid CloudWatch Logs retention value (0 = never expire)."
  }
}

variable "log_group_kms_key_arn" {
  type        = string
  description = "(Optional) ARN of the KMS CMK used to encrypt the ECS service CloudWatch log group at rest. When null, the default CloudWatch Logs service key is used. Must match ^arn:aws:kms: when provided."
  default     = null

  validation {
    condition     = var.log_group_kms_key_arn == null || can(regex("^arn:aws:kms:", var.log_group_kms_key_arn))
    error_message = "log_group_kms_key_arn must be a valid KMS key ARN matching ^arn:aws:kms: when provided."
  }
}

variable "env" {
  type        = string
  description = "(Required) Deployment environment label (e.g. sandbox, qa, prod). Used to construct SSM parameter path prefixes per /telemetry/<env>/<plane>/<key> convention (docs/terragrunt-concepts.md)."

  validation {
    condition     = length(var.env) >= 1
    error_message = "env must be non-empty."
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
  default     = "ecs-app-deploy"
}
