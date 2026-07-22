# _singletons/shared/account.hcl  (SERVICE-role account, env resolved via environment.hcl)
# The env-class basename of this dir is "_shared" (not an env class), so account
# resolution reads environment.hcl (the env folder) and looks up the SERVICE account
# in env_accounts.json. Account abstracted out of the path; copy-safe.
locals {
  _env_accounts             = jsondecode(file("${get_repo_root()}/terragrunt/common/env_accounts.json"))
  _env                      = read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment
  _entry                    = lookup(local._env_accounts["envs"], local._env, null) != null ? local._env_accounts["envs"][local._env] : tobool("ERROR: env-class '${local._env}' not found in common/env_accounts.json (envs).")
  aws_account_id            = local._entry["account_id"]
  aws_profile               = local._entry["aws_profile"]
  use_pinned_module_sources = local._entry["use_pinned_module_sources"]
}
