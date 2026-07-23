# live/telemetry/us-east-1/<env>/_singletons/dns_owner/dns-delegation/000/service_instance.hcl
#
# Service instance layer for the root sandbox dns-delegation unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# Instance "000" is the canonical singleton for dns-delegation in the root
# sandbox environment. The "000" label is intentional and permanent.
#
# NOTE ON DEPENDENCY PLACEMENT (Terragrunt v1.x constraint):
# The cross-account dependency block is declared in terragrunt.hcl rather than
# this file. root.hcl reads service_instance.hcl at line 40 via read_terragrunt_config
# as part of its own locals resolution; if a dependency block exists in this file at
# that point, Terragrunt v1.x triggers dependency output resolution before mock_outputs
# are served, causing a cascade backend-init error. Moving the dependency block to
# terragrunt.hcl (where it is evaluated in leaf context, not in root.hcl's context)
# prevents the cascade.
#
# The service_instance local is consumed by the root terragrunt.hcl remote_state
# key derivation:
#   <env>/<env_instance>/<service>/<service_instance>/terraform.tfstate
#
# spec section 1.3, ledger D-3, D-5.

locals {
  # service_instance resolves to the directory basename, i.e. "000".
  # Combined with environment, environment_instance, service in the root
  # remote_state key: sandbox/000/dns-delegation/000/terraform.tfstate
  service_instance = basename(get_terragrunt_dir())
}
