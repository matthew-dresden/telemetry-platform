# live/telemetry/us-east-1/bootstrap/<role>/dns-prod-zone/000/service_instance.hcl
#
# Service instance layer for the foundation-tier dns-prod-zone unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# Instance "000" is the canonical singleton for dns-prod-zone in the role's account.
# A second instance (e.g. "001") would imply a second independent hosted zone stack,
# which is not a supported pattern for the platform DNS zone. The "000" label is
# intentional and permanent for this foundation unit (spec 02 Section 1.3).
#
# The service_instance local is consumed by the root terragrunt.hcl remote_state
# key derivation. The zone domain resolves from common/domains.json via
# _envcommon/dns-prod-zone.hcl (keyed by the env-class recovered from the bootstrap
# role basename); it is NOT declared in this file.

locals {
  # service_instance resolves to the directory basename, i.e. "000".
  # Combined with environment, environment_instance, service in the root
  # remote_state key: <env>/<env_instance>/<service>/<service_instance>/terraform.tfstate
  service_instance = basename(get_terragrunt_dir())
}
