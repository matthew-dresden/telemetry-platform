# live/telemetry/us-east-1/bootstrap/dns_owner_role/oidc-bootstrap/000/service_instance.hcl
#
# Service instance layer for the root oidc-bootstrap unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# The unit-level instance is "000" (the single instance of the oidc-bootstrap
# service in the root bootstrap layer). No per-instance overrides are needed
# beyond what the leaf terragrunt.hcl declares.

locals {
  service_instance = basename(get_terragrunt_dir())
}
