# live/telemetry/us-east-1/sandbox/000/dns-collector/000/service_instance.hcl
#
# Service instance layer for the sandbox dns-collector unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# Instance "000" is the canonical singleton for dns-collector in the sandbox account.
# A second instance (e.g. "001") would imply a second independent collector alias
# record, which is not a supported pattern for this platform. The "000" label is
# intentional and permanent (spec 02 Section 1.3).
#
# RECORD OWNERSHIP (AC-8):
# This dns-collector unit is the SOLE OWNER of:
#   - DNS alias record R4 -- the public alias A record pointing the collector hostname
#     at the collector CloudFront distribution. R4 is written ONLY by this unit;
#     no other sandbox or prod unit declares R4 (AC-8, D39).
#     The collector-ingestion unit does NOT write R4 (D39 double-ownership guard).
#
# Single ownership of R4 is enforced by the dependency graph. The collector-ingestion
# unit exposes cloudfront_domain_name and cloudfront_hosted_zone_id as outputs; this
# unit reads them as dependency inputs and writes the alias record. Duplicate
# declaration would fail plan with a state conflict (AC-8).
#
# ALIAS CONTRACT (AC-3):
# The primitives/route53-record module enforces an exactly-one-of alias/records
# contract. This unit sets the alias block (cloudfront_domain_name and
# cloudfront_hosted_zone_id from the collector-ingestion dependency) and leaves
# the records input unset. Setting both alias and records would cause the
# primitive's validation to fail with a clear error message.
#
# The service_instance local is consumed by the root terragrunt.hcl remote_state
# key derivation. No additional per-instance flags are required at this layer.
#
# spec 02 Section 1.3, ledger D31, D39, AC-3, AC-8.

locals {
  # service_instance resolves to the directory basename, i.e. "000".
  # Combined with environment, environment_instance, service in the root
  # remote_state key: <env>/<env_instance>/<service>/<service_instance>/terraform.tfstate
  service_instance = basename(get_terragrunt_dir())
}
