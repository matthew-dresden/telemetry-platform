# live/telemetry/us-east-1/<env>/_singletons/dns_owner/dns-delegation/service.hcl
#
# Service layer for the root sandbox dns-delegation unit (<dns-owner-account>).
# Basename resolves to "dns-delegation" per the canonical layer idiom
# (spec 02 Section 1.3).
#
# This unit writes the NS delegation record that delegates
# sandbox.telemetry.example.com from the DNS-owner zone
# (telemetry.example.com). The NS records come from the
# name_servers output of the sandbox dns-prod-zone unit via a five-dotdot
# cross-account dependency (spec FR-2, D-3).
#
# Because this unit runs in the root DNS-owner account (<dns-owner-account>) and the
# DNS-owner zone also lives in that account, the base AWS provider generated
# by the root terragrunt.hcl (which already authenticates to <dns-owner-account>) is
# sufficient for Route 53 writes. No secondary aliased provider or additional
# assume_role block is required (D4: the OIDC-assumed deploy role IS the
# deploy identity -- no second role assumption).
#
# The unit sources exactly one module: primitives/route53-record (AC-2).
# The delegation record is NS-only. The real route53-record schema
# with zone_id, name, type, ttl, and records is used exclusively.
# No hardcoded NS literals are permitted (AC-7).
#
# spec section 4.2, 4.4, ledger D-3, D-5, AC-2, AC-7.

locals {
  # service resolves to the directory basename, i.e. "dns-delegation".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
