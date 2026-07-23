# bootstrap/<role>/account.hcl
#
# Bootstrap account resolution by ROLE. The dir basename is the 4th namespace field's
# "*_role" sub-class: sandbox_role / prod_role / qa_role / dns_owner_role (the three
# 4th-field sub-classes are numeric set NNN / bare tier word shared|dns_owner|pretty /
# bootstrap role *_role). The trailing "_role" suffix is the namespace token; it is
# STRIPPED here to recover the env_accounts.json lookup key:
#   sandbox_role   -> "sandbox"   (envs.sandbox)
#   prod_role      -> "prod"      (envs.prod)
#   qa_role        -> "qa"        (envs.qa)
#   dns_owner_role -> "dns_owner" (the shared dns_owner block, NOT an envs entry)
# The AWS account id is abstracted OUT of the folder path (D2). Copying-safe: the role
# basename is the only key.
locals {
  _env_accounts = jsondecode(file("${get_repo_root()}/terragrunt/common/env_accounts.json"))
  # Strip the "_role" namespace suffix to get the env_accounts lookup key.
  _role                     = trimsuffix(basename(get_terragrunt_dir()), "_role")
  _is_dns_owner             = local._role == "dns_owner"
  _entry                    = local._is_dns_owner ? local._env_accounts["dns_owner"] : (lookup(local._env_accounts["envs"], local._role, null) != null ? local._env_accounts["envs"][local._role] : tobool("ERROR: bootstrap role '${local._role}' (from dir <role>_role) not found in common/env_accounts.json (expected sandbox/prod/qa under envs, or 'dns_owner')."))
  aws_account_id            = local._entry["account_id"]
  aws_profile               = local._entry["aws_profile"]
  use_pinned_module_sources = local._is_dns_owner ? false : local._entry["use_pinned_module_sources"]
}
