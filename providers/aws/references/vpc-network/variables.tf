# ---------------------------------------------------------------------------
# Child module source variables -- const=true defaults resolve to in-repo local
# relative paths so terraform init -backend=false succeeds without network access.
# Each variable is declared with const=true to prevent callers from overriding
# the canonical in-repo source at plan time. (E9-F1-S1-T4 const-source convention.)
# ---------------------------------------------------------------------------

variable "vpc_source" {
  type        = string
  const       = true
  description = "Source path for the vpc primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/vpc"
}

variable "public_subnets_source" {
  type        = string
  const       = true
  description = "Source path for the public subnet primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/subnet"
}

variable "private_subnets_source" {
  type        = string
  const       = true
  description = "Source path for the private subnet primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/subnet"
}

variable "nat_gateways_source" {
  type        = string
  const       = true
  description = "Source path for the nat-gateway primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/nat-gateway"
}

variable "public_route_tables_source" {
  type        = string
  const       = true
  description = "Source path for the public route-table primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/route-table"
}

variable "private_route_tables_source" {
  type        = string
  const       = true
  description = "Source path for the private route-table primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/route-table"
}

variable "vpc_endpoints_source" {
  type        = string
  const       = true
  description = "Source path for the vpc-endpoint primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/vpc-endpoint"
}

variable "vpc_name" {
  type        = string
  description = "(Required) Name for the VPC and associated resources."
}

variable "vpc_cidr_block" {
  type        = string
  description = "(Required) The IPv4 CIDR block for the VPC."

  validation {
    condition     = can(cidrhost(var.vpc_cidr_block, 0))
    error_message = "vpc_cidr_block must be a valid CIDR block."
  }
}

variable "enable_dns_hostnames" {
  type        = bool
  description = "(Optional) Enable DNS hostnames in the VPC. Defaults to true."
  default     = true
}

variable "enable_flow_logs" {
  type        = bool
  description = "(Optional) Whether to enable VPC Flow Logs. Defaults to false."
  default     = false
}

variable "flow_logs_iam_role_arn" {
  type        = string
  description = "(Optional) The ARN for the IAM role used for VPC Flow Logs. Required if enable_flow_logs is true."
  default     = null
}

variable "flow_logs_destination_arn" {
  type        = string
  description = "(Optional) The ARN of the CloudWatch log group or S3 bucket for VPC Flow Logs. Required if enable_flow_logs is true."
  default     = null
}

variable "public_subnets" {
  type = list(object({
    name                    = string
    cidr_block              = string
    availability_zone       = string
    map_public_ip_on_launch = optional(bool, false)
  }))
  description = "(Required) List of public subnet definitions. Each entry must specify name, cidr_block, and availability_zone. map_public_ip_on_launch defaults to false (secure-by-default): these subnets host NAT gateways (which use Elastic IPs, not subnet auto-assignment), so auto-assigning public IPs is unnecessary. Set it to true only for a subnet that must auto-assign public IPs to launched instances."

  validation {
    condition     = length(var.public_subnets) > 0
    error_message = "public_subnets must contain at least one entry."
  }

  validation {
    condition     = alltrue([for s in var.public_subnets : can(cidrhost(s.cidr_block, 0))])
    error_message = "Every cidr_block in public_subnets must be a valid CIDR block."
  }
}

variable "private_subnets" {
  type = list(object({
    name              = string
    cidr_block        = string
    availability_zone = string
  }))
  description = "(Required) List of private subnet definitions. Each entry must specify name, cidr_block, and availability_zone."

  validation {
    condition     = length(var.private_subnets) > 0
    error_message = "private_subnets must contain at least one entry."
  }

  validation {
    condition     = alltrue([for s in var.private_subnets : can(cidrhost(s.cidr_block, 0))])
    error_message = "Every cidr_block in private_subnets must be a valid CIDR block."
  }
}

variable "nat_gateways" {
  type = list(object({
    name               = string
    public_subnet_name = string
    connectivity_type  = optional(string, "public")
  }))
  description = "(Required) List of NAT gateway definitions. Each entry specifies name and public_subnet_name. One NAT gateway per AZ for HA."

  validation {
    condition     = length(var.nat_gateways) > 0
    error_message = "nat_gateways must contain at least one entry."
  }

  validation {
    condition     = alltrue([for ng in var.nat_gateways : contains(["public", "private"], ng.connectivity_type)])
    error_message = "Every connectivity_type in nat_gateways must be either 'public' or 'private'."
  }
}

variable "interface_endpoint_service_names" {
  type        = list(string)
  description = "(Required) List of AWS service names for Interface-type VPC endpoints (e.g. com.amazonaws.us-east-1.ssm). Must be non-empty."

  validation {
    condition     = length(var.interface_endpoint_service_names) > 0
    error_message = "interface_endpoint_service_names must contain at least one entry."
  }
}

variable "interface_endpoint_subnet_names" {
  type        = list(string)
  description = "(Required) List of private subnet names to attach to Interface endpoints."

  validation {
    condition     = length(var.interface_endpoint_subnet_names) > 0
    error_message = "interface_endpoint_subnet_names must contain at least one entry."
  }
}

variable "s3_gateway_endpoint_service_name" {
  type        = string
  description = "(Required) The AWS service name for the S3 Gateway endpoint (e.g. com.amazonaws.us-east-1.s3)."
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
  default     = "vpc-network"
}
