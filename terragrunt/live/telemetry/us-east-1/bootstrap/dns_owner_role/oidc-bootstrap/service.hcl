# live/telemetry/us-east-1/bootstrap/dns_owner_role/oidc-bootstrap/service.hcl
#
# Service layer for the root oidc-bootstrap unit.
# Basename resolves to "oidc-bootstrap" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit creates exactly the telemetry-platform-dns-writer IAM role in the root
# account. The dns-writer role is assumed via sts:AssumeRole (role-chaining) from
# the prod telemetry-platform-gha-tg-apply role so the prod apply can write
# cross-account DNS records into the production DNS zone.
# Root needs no GitHub OIDC provider (D40).

locals {
  service = basename(get_terragrunt_dir())
}
