# terraform.tfvars -- dns-prod-zone deployment-unique values (spec section 4.5, AC-17)
#
# This file is auto-loaded by Terraform after Terragrunt copies the unit directory
# into the module working directory (spec section 1.1). It carries deployment-unique
# fixed values that are specific to this leaf and must not be baked into the shared
# terragrunt.hcl template.
#
# The dns-prod-zone unit creates the sandbox delegated public Route53 hosted zone,
# platform KMS key, and SSM parameter seed via references/dns-prod-zone.
# All zone_name and kms_key_principals inputs are merged from _envcommon/dns-prod-zone.hcl
# (scope-shared). The region and tags are resolved from root include locals. There are
# no deployment-unique fixed values for this leaf beyond what the shared template and
# root locals provide.
#
# Scope-shared values (emails, domain apexes, CIDR) are NOT present here; they
# remain in common/ and _envcommon/ (spec section 4.5, AC-FUNC-003, AC-FUNC-004).
#
# Secret material is NEVER stored in terraform.tfvars; only ARNs/names and SSM paths
# are referenced (spec section 3.6, AC-FUNC-004).
