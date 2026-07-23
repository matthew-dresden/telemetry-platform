module "vpc_fixture" {
  source = "../../../tests/fixtures/vpc-flow-log"

  name_prefix    = "vpce-gw-${var.terratest_run_id}"
  vpc_cidr_block = var.vpc_cidr_block
  tags           = var.tags
}

resource "aws_route_table" "private" {
  vpc_id = module.vpc_fixture.vpc_id

  tags = merge(var.tags, {
    Name    = "vpce-gw-fixture-private-rt"
    Purpose = "terratest-fixture"
  })
}

module "example" {
  source = "../../"

  vpc_id = var.vpc_id != null ? var.vpc_id : module.vpc_fixture.vpc_id
  endpoints = var.endpoints != null ? var.endpoints : tolist([
    {
      name                = "s3"
      service_name        = "com.amazonaws.${var.aws_region}.s3"
      vpc_endpoint_type   = "Gateway"
      subnet_ids          = []
      route_table_ids     = [aws_route_table.private.id]
      security_group_ids  = []
      private_dns_enabled = true
    }
  ])
  tags = var.tags
}
