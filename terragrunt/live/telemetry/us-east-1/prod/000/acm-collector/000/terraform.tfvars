# terraform.tfvars -- acm-collector deployment-unique values (spec section 4.5, AC-17)
#
# This file is auto-loaded by Terraform after Terragrunt copies the unit directory
# into the module working directory (spec section 1.1). It carries deployment-unique
# fixed values that are specific to this leaf and must not be baked into the shared
# terragrunt.hcl template.
#
# The acm-collector unit requests the collector TLS certificate via primitives/acm-certificate.
# All domain_name, subject_alternative_names, and wait_for_validation inputs are
# supplied by _envcommon/acm-collector.hcl (scope-shared). There are no deployment-unique
# fixed values for this leaf beyond what the shared template provides.
#
# Scope-shared values (emails, domain apexes, CIDR) are NOT present here; they
# remain in common/ and _envcommon/ (spec section 4.5, AC-FUNC-003, AC-FUNC-004).
#
# Secret material is NEVER stored in terraform.tfvars; only ARNs/names and SSM paths
# are referenced (spec section 3.6, AC-FUNC-004).
