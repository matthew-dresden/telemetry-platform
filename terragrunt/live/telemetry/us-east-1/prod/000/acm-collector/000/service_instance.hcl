# live/telemetry/us-east-1/sandbox/000/acm-collector/000/service_instance.hcl
#
# Service instance layer for the sandbox acm-collector unit.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
#
# Instance "000" is the canonical singleton for the acm-collector unit in the sandbox
# account. The "000" label is intentional and permanent
# (spec 02 Section 1.3).
#
# The service_instance local is consumed by the root terragrunt.hcl remote_state
# key derivation. No additional per-instance flags are required at this layer
# because the SAN list is fully determined by the _envcommon/acm-collector.hcl
# template via local.root.locals.dns_service_apex / local.root.locals.dns_pretty_apex
# (root loaded with read_terragrunt_config(find_in_parent_folders("root.hcl")),
# resolving from terragrunt/common/domains.json via root local.domain_cfg; account.hcl
# supplies only aws_account_id) (D45/D47).

locals {
  # service_instance resolves to the directory basename, i.e. "000".
  # Combined with environment, environment_instance, service in the root
  # remote_state key: <env>/<env_instance>/<service>/<service_instance>/terraform.tfstate
  service_instance = basename(get_terragrunt_dir())
}
