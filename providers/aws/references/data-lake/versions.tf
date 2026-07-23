terraform {
  required_version = ">= 1.15.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
    # archive packages the CloudWatch-Logs-split transform Lambda source into the
    # deployment zip (data.archive_file) referenced by aws_lambda_function.cwl_transform.
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4.0"
    }
  }
}
