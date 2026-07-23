# <env>/000/_dns_owner/account.hcl  (env-keyed account resolution -- DNS_OWNER role)
#
# Units under this _dns_owner/ subtree run in the SHARED DNS-owner account, not the env's own
# account. The account id + named profile come from common/env_accounts.json dns_owner (the
# same shared account for every env); use_pinned_module_sources is keyed by the ENV this subtree
# supports (read from environment.hcl) so a prod dns_owner unit uses prod's pinned setting.
#
# Copying an env folder copies this file unchanged: it resolves the env-invariant dns_owner
# account plus the env's pinned toggle. The account is abstracted out of the folder path.
locals {
  _env_accounts = jsondecode(file("${get_repo_root()}/terragrunt/common/env_accounts.json"))
  _env          = read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment

  aws_account_id            = local._env_accounts["dns_owner"]["account_id"]
  aws_profile               = local._env_accounts["dns_owner"]["aws_profile"]
  use_pinned_module_sources = lookup(local._env_accounts["envs"], local._env, null) != null ? local._env_accounts["envs"][local._env]["use_pinned_module_sources"] : tobool("ERROR: env-class '${local._env}' not found in common/env_accounts.json (envs).")
}
