# bootstrap/environment.hcl
#
# The bootstrap environment layer, shared by every per-account bootstrap (state-bootstrap +
# oidc-bootstrap). environment resolves to the basename "bootstrap" - reserved exclusively for
# account-bootstrap infrastructure (the remote-state foundation + the CICD OIDC roles). The
# per-account distinction is the env_instance layer below (bootstrap/<role>/), NOT a folder
# named for the account id (account abstracted out of the path, D2).
locals {
  environment = basename(get_terragrunt_dir())
}
