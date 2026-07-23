terraform {
  required_version = ">= 1.15.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project         = var.project_tag
      "terratest-run" = var.terratest_run_id
    }
  }
}
