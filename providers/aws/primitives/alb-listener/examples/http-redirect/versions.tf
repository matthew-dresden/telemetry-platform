terraform {
  required_version = ">= 1.15.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.0.0"
    }
  }
}

# Used by the fixture VPC Flow Logs KMS key policy.
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
