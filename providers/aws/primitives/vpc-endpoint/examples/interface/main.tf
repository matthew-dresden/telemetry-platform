module "vpc_fixture" {
  source = "../../../tests/fixtures/vpc-flow-log"

  name_prefix    = "vpce-iface-${var.terratest_run_id}"
  vpc_cidr_block = var.vpc_cidr_block
  tags           = var.tags
}

resource "aws_subnet" "private_a" {
  vpc_id            = module.vpc_fixture.vpc_id
  cidr_block        = var.private_subnet_cidr
  availability_zone = var.availability_zone

  tags = merge(var.tags, {
    Name    = "vpce-iface-fixture-private-a"
    Purpose = "terratest-fixture"
  })
}

resource "aws_security_group" "endpoint" {
  name        = "vpce-iface-fixture-sg"
  description = "Security group for VPC interface endpoint testing"
  vpc_id      = module.vpc_fixture.vpc_id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr_block]
    description = "Allow HTTPS from within VPC for endpoint traffic"
  }

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr_block]
    description = "Allow HTTPS outbound within VPC"
  }

  tags = merge(var.tags, {
    Name    = "vpce-iface-fixture-sg"
    Purpose = "terratest-fixture"
  })
}

module "example" {
  source = "../../"

  vpc_id = var.vpc_id != null ? var.vpc_id : module.vpc_fixture.vpc_id
  endpoints = var.endpoints != null ? var.endpoints : tolist([
    {
      name                = "ssm"
      service_name        = "com.amazonaws.${var.aws_region}.ssm"
      vpc_endpoint_type   = "Interface"
      subnet_ids          = [aws_subnet.private_a.id]
      route_table_ids     = []
      security_group_ids  = [aws_security_group.endpoint.id]
      private_dns_enabled = true
    }
  ])
  tags = var.tags
}
