# live/telemetry/us-east-1/<env>/account.hcl  (env-keyed account resolution -- SERVICE role)
#
# The AWS account is abstracted OUT of the folder path. This file resolves the account id,
# named AWS profile, and pinned-source toggle from common/env_accounts.json keyed by the
# env-folder basename. It sits at the env level (the same directory as environment.hcl, which
# uses the same basename idiom). It serves the SERVICE role -- units that run in the env's own
# account. Units that run in the shared DNS-owner account find a nearer _dns_owner/account.hcl.
#
# Copying an env folder to a new env-class works with zero edits: the account follows from
# env_accounts.json keyed by the new env-folder basename. Fail-fast: an env-class absent from
# env_accounts.json aborts parse with an actionable message (no fallback, spec S3.5/S7).
#
# get_terragrunt_dir() in a hierarchy file read via find_in_parent_folders resolves to THIS
# file's own directory (same as environment.hcl's basename idiom), so basename = the env-class.
locals {
  _env          = basename(get_terragrunt_dir())
  _env_accounts = jsondecode(file("${get_repo_root()}/terragrunt/common/env_accounts.json"))
  _entry        = lookup(local._env_accounts["envs"], local._env, null) != null ? local._env_accounts["envs"][local._env] : tobool("ERROR: env-class '${local._env}' not found in common/env_accounts.json (envs) -- add a row before deploying this env.")

  # Account identity + named profile + pinned-source toggle, all resolved from config (not path).
  aws_account_id            = local._entry["account_id"]
  aws_profile               = local._entry["aws_profile"]
  use_pinned_module_sources = local._entry["use_pinned_module_sources"]
}
