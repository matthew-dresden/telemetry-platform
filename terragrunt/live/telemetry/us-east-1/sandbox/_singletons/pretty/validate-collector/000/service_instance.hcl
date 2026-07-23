# _singletons/pretty/validate-collector/000/service_instance.hcl
locals {
  service_instance = basename(get_terragrunt_dir())
}
