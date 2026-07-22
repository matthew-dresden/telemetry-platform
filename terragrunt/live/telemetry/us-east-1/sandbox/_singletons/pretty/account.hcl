# _singletons/pretty/account.hcl  (PRETTY-role account, config-derived by the apex asymmetry)
#
# The pretty CNAME lives in whichever zone owns the pretty apex:
#   dns_pretty_apex == dns_service_apex (e.g. sandbox) -> the env's OWN zone -> env SERVICE account.
#   dns_pretty_apex != dns_service_apex (e.g. prod)     -> the shared ROOT zone -> shared DNS-owner account.
# The account is resolved from common/domains.json + common/env_accounts.json (never the folder
# path), so this file copies unchanged to every env and selects the correct account automatically.
# Account abstracted out of the path; copy-safe (D2).
locals {
  _env_accounts = jsondecode(file("${get_repo_root()}/terragrunt/common/env_accounts.json"))
  _domains      = jsondecode(file("${get_repo_root()}/terragrunt/common/domains.json"))
  _env          = read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment
  _domain_cfg   = lookup(local._domains, local._env, null) != null ? local._domains[local._env] : tobool("ERROR: env-class '${local._env}' not found in common/domains.json.")
  _svc_entry    = lookup(local._env_accounts["envs"], local._env, null) != null ? local._env_accounts["envs"][local._env] : tobool("ERROR: env-class '${local._env}' not found in common/env_accounts.json (envs).")

  # true  -> pretty CNAME lands in the env's own zone (env service account)
  # false -> pretty CNAME lands in the shared root zone (dns-owner account)
  _pretty_in_env_zone = local._domain_cfg["dns_pretty_apex"] == local._domain_cfg["dns_service_apex"]

  aws_account_id            = local._pretty_in_env_zone ? local._svc_entry["account_id"] : local._env_accounts["dns_owner"]["account_id"]
  aws_profile               = local._pretty_in_env_zone ? local._svc_entry["aws_profile"] : local._env_accounts["dns_owner"]["aws_profile"]
  use_pinned_module_sources = local._svc_entry["use_pinned_module_sources"]
}
