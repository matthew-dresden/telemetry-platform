terraform {
  required_version = ">= 1.15.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
