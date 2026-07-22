# live/telemetry/us-east-1/sandbox/000/collector-ingestion/000/service_instance.hcl
#
# Service instance layer for the sandbox collector-ingestion unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# Instance "000" is the canonical singleton for collector-ingestion in the sandbox
# account. A second instance (e.g. "001") would imply a second independent collector
# stack, which is not a supported pattern for the platform collector-ingestion service.
# The "000" label is intentional and permanent (spec 02 Section 1.3).
#
# The service_instance local is consumed by the root terragrunt.hcl remote_state
# key derivation. No additional locals are required at this layer because the
# collector-ingestion unit carries no per-instance flag (unlike dns-prod-zone,
# which exposes enable_custom_domain at this layer -- D31).

locals {
  # service_instance resolves to the directory basename, i.e. "000".
  # Combined with environment, environment_instance, service in the root
  # remote_state key: <env>/<env_instance>/<service>/<service_instance>/terraform.tfstate
  service_instance = basename(get_terragrunt_dir())
}
