# _singletons/pretty/validate-collector/service.hcl
locals {
  service = basename(get_terragrunt_dir())
}
