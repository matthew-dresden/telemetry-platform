module "vpc" {
  source = var.vpc_source

  name                      = var.vpc_name
  cidr_block                = var.vpc_cidr_block
  enable_dns_support        = true
  enable_dns_hostnames      = var.enable_dns_hostnames
  enable_flow_logs          = var.enable_flow_logs
  flow_logs_iam_role_arn    = var.flow_logs_iam_role_arn
  flow_logs_destination_arn = var.flow_logs_destination_arn

  tags = local.common_tags
}

module "public_subnets" {
  source = var.public_subnets_source

  vpc_id = module.vpc.vpc_id
  subnets = [
    for s in var.public_subnets : {
      name                    = s.name
      cidr_block              = s.cidr_block
      availability_zone       = s.availability_zone
      map_public_ip_on_launch = s.map_public_ip_on_launch
    }
  ]

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "subnet-public"
}

module "private_subnets" {
  source = var.private_subnets_source

  vpc_id = module.vpc.vpc_id
  subnets = [
    for s in var.private_subnets : {
      name                    = s.name
      cidr_block              = s.cidr_block
      availability_zone       = s.availability_zone
      map_public_ip_on_launch = false
    }
  ]

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "subnet-private"
}

# ---------------------------------------------------------------------------
# Internet Gateway (docs/terragrunt-concepts.md: "public route to internet gateway").
# The REUSED upstream vpc primitive creates ONLY aws_vpc + default SG + DHCP
# options + flow log (docs/terragrunt-concepts.md); it creates NO internet gateway. NAT gateways
# (S1b) require an IGW for egress, and the public route table (S1c) routes
# 0.0.0.0/0 to it. This NEW-LOCAL resource is owned by vpc-network so the IGW
# id is no longer an external input (which had no upstream creator and formed a
# create-time cycle with the VPC). The id is exposed as an output for callers
# that need to reference it directly.
# ---------------------------------------------------------------------------
resource "aws_internet_gateway" "this" {
  vpc_id = module.vpc.vpc_id

  tags = merge(local.common_tags, { Name = "${var.vpc_name}-igw" })
}

module "nat_gateways" {
  source = var.nat_gateways_source

  nat_gateways = local.nat_gateway_definitions

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "nat-gateway"

  depends_on = [module.public_subnets]
}

module "public_route_tables" {
  source = var.public_route_tables_source

  vpc_id = module.vpc.vpc_id
  route_tables = [
    {
      name       = "${var.vpc_name}-public"
      subnet_ids = values(module.public_subnets.subnet_ids)
      routes = [
        {
          destination_cidr_block = "0.0.0.0/0"
          gateway_id             = aws_internet_gateway.this.id
          nat_gateway_id         = null
          vpc_endpoint_id        = null
        }
      ]
    }
  ]

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "route-table-public"

  depends_on = [module.public_subnets]
}

module "private_route_tables" {
  source = var.private_route_tables_source

  vpc_id = module.vpc.vpc_id
  route_tables = [
    {
      name       = "${var.vpc_name}-private"
      subnet_ids = values(module.private_subnets.subnet_ids)
      routes = [
        {
          destination_cidr_block = "0.0.0.0/0"
          gateway_id             = null
          nat_gateway_id         = module.nat_gateways.nat_gateway_ids[local.primary_nat_gateway_name]
          vpc_endpoint_id        = null
        }
      ]
    }
  ]

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "route-table-private"

  depends_on = [module.private_subnets, module.nat_gateways]
}

# Security group for the interface VPC endpoints. Interface endpoints are ENIs that
# require an SG allowing the clients' traffic; without it AWS attaches the VPC default
# SG (which does not allow the ADOT task SG), so ECS tasks time out reaching SSM/ECR/Logs
# at startup (ResourceInitializationError). Allow HTTPS (443) from the VPC CIDR so any
# in-VPC client (ECS tasks, Lambdas) can reach the endpoints. Endpoint ENIs only return
# responses to in-VPC clients, so egress is scoped to the VPC CIDR rather than 0.0.0.0/0.
resource "aws_security_group" "vpc_endpoints" {
  name        = "${var.vpc_name}-vpce-sg"
  description = "Allow HTTPS from within the VPC to the interface VPC endpoints."
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "HTTPS from within the VPC to the interface endpoints."
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr_block]
  }

  egress {
    description = "Endpoint ENI responses to in-VPC clients only."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr_block]
  }

  tags = merge(local.common_tags, { Name = "${var.vpc_name}-vpce-sg" })
}

module "vpc_endpoints" {
  source = var.vpc_endpoints_source

  vpc_id    = module.vpc.vpc_id
  endpoints = local.all_endpoint_definitions

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "vpc-endpoint"

  depends_on = [module.private_subnets, module.private_route_tables]
}
