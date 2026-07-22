variable "name" {
  type        = string
  description = "ALB name. Must be alphanumeric and hyphens only, max 32 characters."
}

variable "internal" {
  type        = bool
  description = "Whether the ALB is internal."
  default     = true
}

variable "vpc_cidr_block" {
  type        = string
  description = "CIDR block for the fixture VPC."
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr_block, 0))
    error_message = "vpc_cidr_block must be a valid CIDR block."
  }
}

variable "subnet_cidr_a" {
  type        = string
  description = "CIDR block for the first fixture subnet."
  default     = "10.0.1.0/24"
}

variable "subnet_cidr_b" {
  type        = string
  description = "CIDR block for the second fixture subnet."
  default     = "10.0.2.0/24"
}

variable "availability_zone_a" {
  type        = string
  description = "Availability zone for the first fixture subnet."
  default     = "us-east-1a"
}

variable "availability_zone_b" {
  type        = string
  description = "Availability zone for the second fixture subnet."
  default     = "us-east-1b"
}

variable "subnet_ids" {
  type        = list(string)
  description = "Override subnet IDs. When provided, the fixture subnets are not used. Used in validation error tests."
  default     = null
}

variable "create_security_group" {
  type        = bool
  description = "Whether to create a managed security group for the ALB."
  default     = true
}

variable "ingress_cidr_blocks" {
  type        = list(string)
  description = "Allowed ingress CIDR blocks for the ALB security group."
  default     = []
}

variable "idle_timeout" {
  type        = number
  description = "ALB idle connection timeout in seconds."
  default     = 60
}

variable "enable_deletion_protection" {
  type        = bool
  description = "Whether to enable deletion protection on the ALB."
  default     = false
}

variable "target_groups" {
  type = list(object({
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
  description = "Target groups to create. Defaults to a single ADOT OTLP/HTTP target group on port 4318."
  default = [
    {
      name        = "adot-otlp"
      port        = 4318
      protocol    = "HTTP"
      target_type = "ip"
      health_check = {
        path = "/healthz"
      }
    }
  ]
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "alb-module-testing"
  }
}

variable "project_tag" {
  type        = string
  description = "Project tag value applied to all resources via the provider default_tags block."
}

variable "terratest_run_id" {
  type        = string
  description = "Terratest run identifier applied to all resources via the provider default_tags block."
}
