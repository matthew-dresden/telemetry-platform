# terraform.tfvars -- dns-collector deployment-unique values (spec section 4.5, AC-17)
#
# This file is auto-loaded by Terraform after Terragrunt copies the unit directory
# into the module working directory (spec section 1.1). It carries deployment-unique
# fixed values that are specific to this leaf and must not be baked into the shared
# terragrunt.hcl template.
#
# The dns-collector unit writes alias record R4 (the public alias A record pointing
# the collector hostname at the CloudFront distribution) via primitives/route53-record.
# All inputs are resolved from dependency outputs and account.hcl (zone_id from
# dns-prod-zone, cloudfront_domain_name and cloudfront_hosted_zone_id from
# collector-ingestion, record name from dns_service_apex). There are no deployment-unique
# fixed values for this leaf beyond what dependency outputs and account.hcl resolve.
#
# Structural resource constants (type = "A", evaluate_target_health = false) are NOT
# moved to tfvars; they are structural constants that belong in the leaf HCL (AC-FUNC-003).
#
# Scope-shared values (emails, domain apexes, CIDR) are NOT present here; they
# remain in common/ and _envcommon/ (spec section 4.5, AC-FUNC-003, AC-FUNC-004).
#
# Secret material is NEVER stored in terraform.tfvars; only ARNs/names and SSM paths
# are referenced (spec section 3.6, AC-FUNC-004).
