data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  region = data.aws_region.current.name
  name   = var.name
  tags = merge(var.tags, {
    Purpose = "terratest-fixture"
  })
}

# -- Internet Gateway --
# The IGW is now created INSIDE the vpc-network module (docs/terragrunt-concepts.md) and exposed
# via the internet_gateway_id output. The fixture no longer creates one and no
# longer passes internet_gateway_id in: that broke the create-time cycle where
# the fixture IGW depended on the module VPC while the module public route
# depended on the fixture IGW id.

module "example" {
  source = "../../"

  vpc_name       = local.name
  vpc_cidr_block = var.vpc_cidr_block

  enable_flow_logs          = true
  flow_logs_iam_role_arn    = aws_iam_role.vpc_flow_log.arn
  flow_logs_destination_arn = aws_cloudwatch_log_group.vpc_flow_log.arn

  public_subnets = [
    {
      name              = "${local.name}-public-a"
      cidr_block        = var.public_subnet_cidrs[0]
      availability_zone = "${local.region}a"
    },
    {
      name              = "${local.name}-public-b"
      cidr_block        = var.public_subnet_cidrs[1]
      availability_zone = "${local.region}b"
    },
    {
      name              = "${local.name}-public-c"
      cidr_block        = var.public_subnet_cidrs[2]
      availability_zone = "${local.region}c"
    },
  ]

  private_subnets = [
    {
      name              = "${local.name}-private-a"
      cidr_block        = var.private_subnet_cidrs[0]
      availability_zone = "${local.region}a"
    },
    {
      name              = "${local.name}-private-b"
      cidr_block        = var.private_subnet_cidrs[1]
      availability_zone = "${local.region}b"
    },
    {
      name              = "${local.name}-private-c"
      cidr_block        = var.private_subnet_cidrs[2]
      availability_zone = "${local.region}c"
    },
  ]

  nat_gateways = [
    {
      name               = "${local.name}-nat-a"
      public_subnet_name = "${local.name}-public-a"
      connectivity_type  = "public"
    }
  ]

  interface_endpoint_service_names = [
    "com.amazonaws.${local.region}.ssm",
  ]

  interface_endpoint_subnet_names = [
    "${local.name}-private-a",
  ]

  s3_gateway_endpoint_service_name = "com.amazonaws.${local.region}.s3"

  tags = local.tags
}

output "vpc_id" {
  description = "The ID of the VPC."
  value       = module.example.vpc_id
}

output "internet_gateway_id" {
  description = "The ID of the Internet Gateway created by the vpc-network module."
  value       = module.example.internet_gateway_id
}

output "public_subnet_ids" {
  description = "Ordered list of public subnet IDs."
  value       = module.example.public_subnet_ids
}

output "private_subnet_ids" {
  description = "Ordered list of private subnet IDs."
  value       = module.example.private_subnet_ids
}

output "nat_gateway_ids" {
  description = "Ordered list of NAT gateway IDs."
  value       = module.example.nat_gateway_ids
}

output "route_table_ids" {
  description = "Ordered list of route table IDs."
  value       = module.example.route_table_ids
}

output "interface_endpoint_ids" {
  description = "Ordered list of interface endpoint IDs."
  value       = module.example.interface_endpoint_ids
}

output "s3_gateway_endpoint_id" {
  description = "The ID of the S3 gateway endpoint."
  value       = module.example.s3_gateway_endpoint_id
}
