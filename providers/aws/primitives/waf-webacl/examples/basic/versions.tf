terraform {
  required_version = ">= 1.15.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
  }
}

# CLOUDFRONT-scope WAF must be created in us-east-1.
# The provider is pinned to us-east-1 in this example.
provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project         = var.project_tag
      "terratest-run" = var.terratest_run_id
    }
  }
}
