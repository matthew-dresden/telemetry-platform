data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # Derive a unique zone name per test run by embedding the account ID,
  # making the fixture safe to run in parallel CI environments.
  zone_name = "test-${local.account_id}.internal"

  # A KMS alias is unique per account+region, so the fixture MUST NOT create the
  # production alias name: when the foundation-tier dns-prod-zone unit has been
  # applied, alias/telemetry-config already exists in the account and a fixture
  # creating the same alias fails with AlreadyExistsException. Suffix the alias
  # with the per-run id (same run-scoping pattern as zone_name) so the fixture
  # exercises the module's real alias-creation path without colliding with the
  # live foundation CMK. var.terratest_run_id is injected per run; for offline
  # validate it is the committed tfvars value.
  kms_alias = "telemetry-config-${var.terratest_run_id}"

  tags = {
    Purpose   = "terratest-fixture"
    ManagedBy = "terraform"
  }
}

module "example" {
  source = "../../"

  zone_name     = local.zone_name
  kms_alias     = local.kms_alias
  region        = local.region
  force_destroy = true

  kms_key_principals = [
    "arn:aws:iam::${local.account_id}:root",
  ]

  ssm_parameters = {
    "/telemetry/dns/zone-id" = {
      type  = "String"
      value = "placeholder-resolved-at-apply"
    }
  }

  tags = local.tags
}

output "zone_id" {
  description = "The Route 53 hosted zone ID."
  value       = module.example.zone_id
}

output "name_servers" {
  description = "List of name servers for the hosted zone."
  value       = module.example.name_servers
}

output "zone_arn" {
  description = "The ARN of the Route 53 hosted zone."
  value       = module.example.zone_arn
}

output "kms_key_arn" {
  description = "The ARN of the prod DNS/cert KMS key."
  value       = module.example.kms_key_arn
}

output "kms_key_id" {
  description = "The ID of the prod DNS/cert KMS key."
  value       = module.example.kms_key_id
}
