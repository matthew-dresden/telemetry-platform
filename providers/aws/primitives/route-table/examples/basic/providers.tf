provider "aws" {
  default_tags {
    tags = {
      Project         = var.project_tag
      "terratest-run" = var.terratest_run_id
    }
  }
}
