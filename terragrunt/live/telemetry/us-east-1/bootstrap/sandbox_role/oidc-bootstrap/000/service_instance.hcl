# live/telemetry/us-east-1/bootstrap/sandbox_role/oidc-bootstrap/000/service_instance.hcl
#
# Service instance layer for the sandbox oidc-bootstrap unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# Instance "000" is the canonical singleton for oidc-bootstrap. A second instance
# (e.g. "001") would imply a second independent OIDC role set in the same account,
# which is not a supported pattern (spec 02 Section 1.3, D48).
#
# The service_instance local is consumed by the root terragrunt.hcl remote_state
# key derivation. No additional locals or includes are required at this layer.
# All leaf-specific configuration lives in the sibling terragrunt.hcl file (D40).

locals {
  # service_instance resolves to the directory basename, i.e. "000".
  service_instance = basename(get_terragrunt_dir())
}
