module "vpc_fixture" {
  source = "../../../tests/fixtures/vpc-flow-log"

  # Include the run ID in the prefix so IAM role and log group names are unique
  # per test run. Static names cause ResourceAlreadyExistsException errors when
  # a prior run fails and leaves resources behind. IAM role name max is 64
  # chars; the longest derivative is "<prefix>-vpc-flow-log-role" (18 suffix
  # chars), so the prefix must not exceed 46 chars. "rt-fx-tt-YYYYMMDDHHMMSS-
  # xxxxxx" is 32 chars, well within the limit.
  name_prefix    = "rt-fx-${var.terratest_run_id}"
  vpc_cidr_block = var.vpc_cidr_block
  tags           = var.tags
}

resource "aws_subnet" "private_a" {
  vpc_id            = module.vpc_fixture.vpc_id
  cidr_block        = var.private_subnet_a_cidr
  availability_zone = var.availability_zone_a

  tags = merge(var.tags, {
    Name    = "route-table-fixture-private-a"
    Purpose = "terratest-fixture"
  })
}

resource "aws_subnet" "private_b" {
  vpc_id            = module.vpc_fixture.vpc_id
  cidr_block        = var.private_subnet_b_cidr
  availability_zone = var.availability_zone_b

  tags = merge(var.tags, {
    Name    = "route-table-fixture-private-b"
    Purpose = "terratest-fixture"
  })
}

resource "aws_subnet" "public_a" {
  vpc_id            = module.vpc_fixture.vpc_id
  cidr_block        = var.public_subnet_a_cidr
  availability_zone = var.availability_zone_a

  tags = merge(var.tags, {
    Name    = "route-table-fixture-public-a"
    Purpose = "terratest-fixture"
  })
}

resource "aws_internet_gateway" "fixture" {
  vpc_id = module.vpc_fixture.vpc_id

  tags = merge(var.tags, {
    Name    = "route-table-fixture-igw"
    Purpose = "terratest-fixture"
  })
}

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = merge(var.tags, {
    Name    = "route-table-fixture-nat-eip"
    Purpose = "terratest-fixture"
  })

  depends_on = [aws_internet_gateway.fixture]
}

resource "aws_nat_gateway" "fixture" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public_a.id

  tags = merge(var.tags, {
    Name    = "route-table-fixture-nat"
    Purpose = "terratest-fixture"
  })

  depends_on = [aws_internet_gateway.fixture]
}

module "example" {
  source = "../../"

  vpc_id = var.vpc_id != null ? var.vpc_id : module.vpc_fixture.vpc_id
  route_tables = var.route_tables != null ? var.route_tables : [
    {
      name       = "private"
      subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_b.id]
      routes = [
        {
          destination_cidr_block = "0.0.0.0/0"
          nat_gateway_id         = aws_nat_gateway.fixture.id
        }
      ]
    }
  ]
  tags = var.tags
}
