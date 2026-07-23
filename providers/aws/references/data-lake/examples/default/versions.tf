terraform {
  required_version = ">= 1.15.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4.0"
    }
  }
}
