# live/telemetry/us-east-1/sandbox/000/acm-validate-collector/service.hcl
#
# Service layer for the sandbox acm-validate-collector unit.
# Basename resolves to "acm-validate-collector" per the canonical layer idiom
# (spec 02 Section 1.3).
#
# This unit writes the R3 validation CNAME for the collector pretty-SAN
# (the delegated sandbox subdomain pretty hostname) into the delegated sandbox
# subdomain zone. The CNAME name and value are sourced from the domain_validation_options
# output of the upstream acm-collector unit (D26, D19). The record is written through the
# aliased telemetry-platform-dns-writer provider, which assumes the DNS-writer role in the
# root dns-owner account via sts:AssumeRole role-chaining.
#
# The unit sources exactly one module: primitives/route53-record (AC-3, spec 02 Section 3.5).
# The route53-record exactly-one-of alias/records guard is satisfied by setting alias = null
# and records = [dvo.resource_record_value] (AC-7).
#
# Record ownership: R3 is written ONLY by this unit. No other unit in this account
# or any other account writes R3 (AC-8). The dns-delegation unit is NS-only and does
# not own R3 (AC-8).
#
# Module source: primitives/route53-record (referenced from this repo via git, AC-3).
# Pre-tag local source is used during initial rollout; replace with an immutable
# ?ref=<semver> tag once the module is published and promoted to stable.
#
# Dependencies:
#   - primitives/route53-record module must be present in the repo at
#     providers/aws/primitives/route53-record (AC-3).
#   - acm-collector unit (E6-F1-S1-T2) must expose domain_validation_options
#     containing the pretty-SAN entry (D26, D19).
#   - dns-prod-zone unit must expose zone_id (the sandbox delegated subdomain zone)
#     so R3 can be written into the correct hosted zone (AC-7, D38).
#   - state-bootstrap unit (E4-F1-S1-T1) must have been applied so the remote state
#     backend and lock table exist (D40).
#
# The enable_custom_domain flag is sourced from account.hcl (sandbox default: false).
# When false, the route53-record module call is zero-count and no R3 CNAME is written.
# When true, R3 is written into the delegated sandbox subdomain zone (D31, AC-8).
#
# spec 02 Section 4.2, ledger D2, D4, D19, D26, D31, D37, D38, D40, D45, D47, AC-3, AC-7, AC-8.

locals {
  # service resolves to the directory basename, i.e. "acm-validate-collector".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
