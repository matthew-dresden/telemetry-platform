variable "name" {
  type        = string
  description = "Name prefix for fixture resources (ALB name). Max 32 characters."
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

variable "target_group_port" {
  type        = number
  description = "Port for the fixture target group."
  default     = 4318
}

variable "certificate_domain" {
  type        = string
  description = "Common name (CN) for the self-signed TLS certificate imported into ACM. No real DNS delegation is required; this is used only as the CN field in the X.509 subject."
  default     = "alb-listener-redirect.example.internal"
}

variable "ssl_policy" {
  type        = string
  description = "SSL policy for the HTTPS listener."
  default     = "ELBSecurityPolicy-TLS13-1-2-2021-06"
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "alb-listener-module-testing"
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
