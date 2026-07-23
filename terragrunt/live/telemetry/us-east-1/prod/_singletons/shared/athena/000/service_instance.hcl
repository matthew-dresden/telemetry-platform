# live/telemetry/us-east-1/<env>/_singletons/shared/athena/000/service_instance.hcl
#
# Service instance layer for the shared athena unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# Instance "000" is the canonical singleton for the athena unit. A second instance
# (e.g. "001") would imply a second independent Athena workgroup, which is not a
# supported pattern for the platform athena service. The "000" label is intentional
# and permanent (spec 02 Section 1.3).
#
# The service_instance local is consumed by the root terragrunt.hcl remote_state
# key derivation and the namespace derivation.

locals {
  # service_instance resolves to the directory basename, i.e. "000".
  service_instance = basename(get_terragrunt_dir())
}
