module "vpc_fixture" {
  source = "../../../tests/fixtures/vpc-flow-log"

  # Include the run ID in the prefix so IAM role and log group names are unique
  # per test run. Static names cause EntityAlreadyExists errors when a prior run
  # fails and leaves resources behind. IAM role name max is 64 chars; the
  # longest derivative is "<prefix>-vpc-flow-log-role" (18 suffix chars), so
  # the prefix must not exceed 46 chars. "nat-gw-tt-YYYYMMDDHHMMSS-xxxxxx" is
  # 32 chars, well within the limit.
  name_prefix    = "nat-gw-${var.terratest_run_id}"
  vpc_cidr_block = var.vpc_cidr_block
  tags           = var.tags
}

resource "aws_subnet" "public_a" {
  vpc_id                  = module.vpc_fixture.vpc_id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false

  tags = merge(var.tags, {
    Name    = "nat-gw-fixture-public-a"
    Purpose = "terratest-fixture"
  })
}

resource "aws_internet_gateway" "fixture" {
  vpc_id = module.vpc_fixture.vpc_id

  tags = merge(var.tags, {
    Name    = "nat-gw-fixture-igw"
    Purpose = "terratest-fixture"
  })
}

module "example" {
  source = "../../"

  nat_gateways = var.nat_gateways != null ? var.nat_gateways : [
    {
      name             = "nat-a"
      public_subnet_id = aws_subnet.public_a.id
    }
  ]
  tags = var.tags

  depends_on = [aws_internet_gateway.fixture]
}
