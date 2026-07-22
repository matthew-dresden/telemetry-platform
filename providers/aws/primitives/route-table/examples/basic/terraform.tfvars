vpc_cidr_block        = "10.0.0.0/16"
private_subnet_a_cidr = "10.0.1.0/24"
private_subnet_b_cidr = "10.0.2.0/24"
public_subnet_a_cidr  = "10.0.100.0/24"
availability_zone_a   = "us-east-1a"
availability_zone_b   = "us-east-1b"

tags = {
  Environment = "test"
  Purpose     = "route-table-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
