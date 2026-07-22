# _singletons/pretty/environment_instance.hcl
#
# The pretty tier's environment-instance layer. The 4th namespace field
# (environment_instance) is the dir basename VERBATIM -- here the bare tier word "pretty"
# (one of the three 4th-field sub-classes: numeric set NNN / bare tier word
# shared|dns_owner|pretty / bootstrap role *_role). It feeds the state-key prefix + the
# per-field tags. The pretty CNAME units under this subtree select their zone + account
# from common/domains.json + env_accounts.json (see account.hcl), so this file copies
# unchanged to every env.
locals {
  environment_instance = basename(get_terragrunt_dir())
  account_vars         = read_terragrunt_config(find_in_parent_folders("account.hcl"))
}
