# terraform.tfvars -- identity deployment-unique values (spec section 4.5, AC-17)
#
# This file is auto-loaded by Terraform after Terragrunt copies the unit directory
# into the module working directory (spec section 1.1). It carries deployment-unique
# fixed values that are specific to this leaf and must not be baked into the shared
# terragrunt.hcl template.
#
# The identity unit maps IAM Identity Center groups (Viewer, Author, Admin) to QuickSight,
# Athena, and portal access roles via references/identity. Group names and role names
# are merged from _envcommon/identity.hcl (scope-shared). Trust policies and inline
# policies are derived from account.hcl locals in the leaf (input-driven, not literals).
# There are no deployment-unique fixed values for this leaf beyond what the shared
# template and account.hcl provide.
#
# Scope-shared values (emails, domain apexes, CIDR) are NOT present here; they
# remain in common/ and _envcommon/ (spec section 4.5, AC-FUNC-003, AC-FUNC-004).
#
# Secret material is NEVER stored in terraform.tfvars; only ARNs/names and SSM paths
# are referenced (spec section 3.6, AC-FUNC-004).
