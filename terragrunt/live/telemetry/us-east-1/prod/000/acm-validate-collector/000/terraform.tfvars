# terraform.tfvars -- acm-validate-collector deployment-unique values (spec section 4.5, AC-17)
#
# This file is auto-loaded by Terraform after Terragrunt copies the unit directory
# into the module working directory (spec section 1.1). It carries deployment-unique
# fixed values that are specific to this leaf and must not be baked into the shared
# terragrunt.hcl template.
#
# The acm-validate-collector unit writes R3 (the collector pretty-SAN ACM validation
# CNAME) into the delegated sandbox subdomain zone via primitives/route53-record.
# The record type, TTL, name, and value are derived from the acm-collector dependency
# output (scope-resolved). There are no deployment-unique fixed values for this leaf
# beyond what dependency outputs and account.hcl resolve.
#
# Scope-shared values (emails, domain apexes, CIDR) are NOT present here; they
# remain in common/ and _envcommon/ (spec section 4.5, AC-FUNC-003, AC-FUNC-004).
#
# Secret material is NEVER stored in terraform.tfvars; only ARNs/names and SSM paths
# are referenced (spec section 3.6, AC-FUNC-004).
