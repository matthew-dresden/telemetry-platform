# live/telemetry/us-east-1/<env>/_singletons/shared/vpc-flow-logs/000/service_instance.hcl
#
# Service instance layer for the vpc-flow-logs unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# Instance "000" is the canonical singleton for the VPC Flow Logs destination + delivery
# role. A second instance would imply a second independent flow-logs target, which is not a
# supported pattern. The "000" label is intentional and permanent (spec 02 Section 1.3).
#
# The service_instance local is consumed by the root terragrunt.hcl remote_state key
# derivation. No additional locals are required at this layer.

locals {
  # service_instance resolves to the directory basename, i.e. "000".
  # Combined with environment, environment_instance, service in the root
  # remote_state key: <env>/<env_instance>/<service>/<service_instance>/terraform.tfstate
  service_instance = basename(get_terragrunt_dir())
}
