# live/telemetry/us-east-1/sandbox/000/dns-collector/service.hcl
#
# Service layer for the sandbox dns-collector unit.
# Basename resolves to "dns-collector" per the canonical layer idiom
# (spec 02 Section 1.3).
#
# This unit writes the public alias A record R4:
#   collector.* -> the collector CloudFront distribution
#
# R4 is a Route 53 ALIAS record (type A) that points the collector hostname at
# the CloudFront distribution created by the collector-ingestion unit. The alias
# target values (cloudfront_domain_name, cloudfront_hosted_zone_id) are read
# from the collector-ingestion unit using the UNPREFIXED output names per D48 --
# a prefixed read yields a dangling reference and fails apply.
#
# Record ownership (AC-8):
#   R4 -- the collector alias A record -- is written ONLY by this unit.
#   Neither collector-ingestion nor any other unit in any account writes R4 (AC-8, D39).
#   Single ownership is enforced by the dependency graph.
#
# Alias vs records contract (AC-3):
#   The primitives/route53-record module enforces an exactly-one-of contract:
#   either the alias block OR the records list must be set, never both.
#   This unit sets the alias block and leaves records unset so the guard passes.
#
# Dependencies:
#   - primitives/route53-record module must be present in the repo at
#     providers/aws/primitives/route53-record before this unit can be applied.
#   - sandbox dns-prod-zone unit (E6-F1-S1-T1) must be applied and expose
#     zone_id so the R4 record lands in the correct hosted zone.
#   - sandbox collector-ingestion unit (E6-F2-S1-T2) must be applied and expose
#     the UNPREFIXED cloudfront_domain_name and cloudfront_hosted_zone_id outputs
#     that the R4 alias target requires (D48).
#   - state-bootstrap unit (E4-F1-S1-T1) must have been applied so the remote
#     state backend and lock table exist (D40).
#
# The _envcommon include is not used at this service layer. No shared template
# centralises route53-record inputs for dns-collector because the alias-target
# values are entirely per-service-instance (sourced from the upstream CloudFront unit).

locals {
  # service resolves to the directory basename, i.e. "dns-collector".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
