# _singletons/pretty/collector/service.hcl
# Service layer for the active-set pretty CNAME unit. Basename resolves to "collector"
# (consumed by root.hcl for the remote_state key + per-field tags).
locals {
  service = basename(get_terragrunt_dir())
}
