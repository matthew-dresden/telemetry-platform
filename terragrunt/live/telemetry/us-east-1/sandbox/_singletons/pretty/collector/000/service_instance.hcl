# _singletons/pretty/collector/000/service_instance.hcl
# Service-instance layer. Basename resolves to "000" (the canonical singleton for the pretty
# CNAME unit). The dependency block lives in terragrunt.hcl (not here) per the Terragrunt v1.x
# root.hcl read-order constraint documented in the dns-delegation unit.
locals {
  service_instance = basename(get_terragrunt_dir())
}
