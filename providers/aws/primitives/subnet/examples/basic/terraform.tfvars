vpc_cidr_block = "10.0.0.0/16"

subnets = [
  {
    name              = "private-a"
    cidr_block        = "10.0.1.0/24"
    availability_zone = "us-east-1a"
  },
  {
    name              = "private-b"
    cidr_block        = "10.0.2.0/24"
    availability_zone = "us-east-1b"
  },
  {
    name              = "private-c"
    cidr_block        = "10.0.3.0/24"
    availability_zone = "us-east-1c"
  },
]

tags = {
  Environment = "test"
  Purpose     = "subnet-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
