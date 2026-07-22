module "vpc_fixture" {
  source = "../../../tests/fixtures/vpc-flow-log"

  # Include the run ID in the prefix so IAM role and log group names are unique
  # per test run. Static names cause ResourceAlreadyExistsException errors when
  # a prior run fails and leaves resources behind. IAM role name max is 64
  # chars; the longest derivative is "<prefix>-vpc-flow-log-role" (18 suffix
  # chars), so the prefix must not exceed 46 chars. "sn-fx-<runid>" is
  # well within the limit.
  name_prefix    = "sn-fx-${var.terratest_run_id}"
  vpc_cidr_block = var.vpc_cidr_block
  tags           = var.tags
}

module "example" {
  source = "../../"

  vpc_id  = var.vpc_id != null ? var.vpc_id : module.vpc_fixture.vpc_id
  subnets = var.subnets
  tags    = var.tags
}
