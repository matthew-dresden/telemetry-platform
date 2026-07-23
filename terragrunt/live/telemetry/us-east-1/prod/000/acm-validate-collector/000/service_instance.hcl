# live/telemetry/us-east-1/sandbox/000/acm-validate-collector/000/service_instance.hcl
#
# Service instance layer for the sandbox acm-validate-collector unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# Instance "000" is the canonical singleton for acm-validate-collector in the sandbox
# account. The "000" label is intentional and permanent
# (spec 02 Section 1.3).
#
# The service_instance local is consumed by the root terragrunt.hcl remote_state
# key derivation. No additional per-instance flags are required at this layer
# because the enable_custom_domain toggle flows from account.hcl through the
# environment_instance.hcl layer (D31).

locals {
  # service_instance resolves to the directory basename, i.e. "000".
  # Combined with environment, environment_instance, service in the root
  # remote_state key: <env>/<env_instance>/<service>/<service_instance>/terraform.tfstate
  service_instance = basename(get_terragrunt_dir())
}
