# terraform.tfvars -- data-lake deployment-unique values (spec section 4.5, AC-17)
#
# This file is auto-loaded by Terraform after Terragrunt copies the unit directory
# into the module working directory (spec section 1.1). It carries deployment-unique
# fixed values that are specific to this leaf and must not be baked into the shared
# terragrunt.hcl template.
#
# DEPLOYMENT-UNIQUE FIXED VALUES (spec section 0.5, AC-FUNC-002):
#   transition_days -- S3 lifecycle: days in hot storage before transitioning to
#   GLACIER. Value 365 = 1 year hot storage; overrides the _envcommon default of 90.
#   This satisfies the sandbox records-retention policy (AC-11): 1 year hot.
#
#   expiration_days -- S3 lifecycle: total days before permanent deletion.
#   Value 730 = 365 hot + 365 cold (GLACIER), then expired. The references/data-lake
#   module propagates this to the s3-bucket primitive lifecycle_rules so objects
#   expire after 730 days total (AC-11).
#
# Both values were previously inlined in the leaf terragrunt.hcl inputs block and are
# now sourced from this file (AC-FUNC-002). They map to declared module variables in
# references/data-lake.
#
# Scope-shared values (emails, domain apexes, CIDR) are NOT present here; they
# remain in common/ and _envcommon/ (spec section 4.5, AC-FUNC-003, AC-FUNC-004).
#
# Secret material is NEVER stored in terraform.tfvars; only ARNs/names and SSM paths
# are referenced (spec section 3.6, AC-FUNC-004).

transition_days = 365
expiration_days = 730
