# live/telemetry/us-east-1/bootstrap/dns_owner_role/state-bootstrap/000/service_instance.hcl
#
# Service instance layer for the root state-bootstrap unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# The unit-level instance is "000" (the single instance of the state-bootstrap
# service in the prod bootstrap layer). No per-instance overrides are needed
# beyond what the leaf terragrunt.hcl declares.
#
# Instance "000" is the canonical singleton for state-bootstrap. A second instance
# (e.g. "001") would imply a second independent state-backend stack in the same
# account, which is not a supported pattern. The "000" label is intentional and
# permanent for this account (spec 02 Section 1.3, D48).
#
# The service_instance local is consumed by the root terragrunt.hcl remote_state
# key derivation. No additional locals or includes are required at this layer.
# All leaf-specific configuration (source, inputs, backend override) lives in the
# sibling terragrunt.hcl file (D40, D46).

locals {
  # service_instance resolves to the directory basename, i.e. "000".
  # Combined with environment, environment_instance, service in the root
  # remote_state key: <env>/<env_instance>/<service>/<service_instance>/terraform.tfstate
  service_instance = basename(get_terragrunt_dir())
}
