terraform {
  required_version = ">= 1.15.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
  }
}

# The AWS provider must be pinned to us-east-1 for ACM certificates used with
# CloudFront, which requires certificates in us-east-1 (per decision D24 and CloudFront requirements).
provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project         = var.project_tag
      "terratest-run" = var.terratest_run_id
    }
  }
}
