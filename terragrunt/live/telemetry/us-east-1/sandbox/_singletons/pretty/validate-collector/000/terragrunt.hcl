# _singletons/pretty/validate-collector/000/terragrunt.hcl
#
# Writes the ACM DNS-validation CNAME for the PRETTY-name SAN (collector.${pretty_apex}) of the ACTIVE
# set's acm-collector certificate, into the CONFIG-DERIVED zone (env zone when pretty_apex==service_apex
# i.e. sandbox; else the shared root zone i.e. prod) - the same apex asymmetry as the pretty CNAME
# (D2 _pretty tier). This is why the pretty-SAN validation lives here in _pretty (config-derived
# account+zone) and NOT in the per-set validator (env-only) or _dns_owner (root-only).

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

terraform {
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/route53-record?ref=providers/aws/primitives/route53-record/v1.0.2" : "${get_repo_root()}//providers/aws/primitives/route53-record"
}

# the ACTIVE set's acm-collector cert (for its pretty-SAN domain_validation_options entry)
dependency "acm_collector" {
  config_path = "../../../../${local.env_active}/acm-collector/${local.acm_collector_active}"

  mock_outputs_allowed_terraform_commands = ["validate", "plan", "init", "destroy"]
  mock_outputs = {
    domain_validation_options = toset([
      {
        domain_name           = local.pretty_fqdn
        resource_record_name  = "_mock-collector-pretty-san.${local.pretty_apex}."
        resource_record_type  = "CNAME"
        resource_record_value = "mock-collector-pretty-acm-validation.acm-validations.aws."
      }
    ])
    certificate_arn = "arn:aws:acm:us-east-1:${local.aws_account_id}:certificate/mock-collector-cert-id"
  }
}

# env-zone zone_id source (consumed only when pretty_apex == service_apex). Declared
# unconditionally; mock_outputs keep validate/plan/init/destroy offline-safe.
dependency "dns_prod_zone" {
  config_path = "../../../../../bootstrap/${local.bootstrap_role}/dns-prod-zone/${local.dns_prod_zone_active}"

  mock_outputs_allowed_terraform_commands = ["validate", "plan", "init", "destroy"]
  mock_outputs = {
    zone_id      = "ZMOCKENVZONEID0001"
    name_servers = ["ns-mock-1.awsdns-01.com."]
  }
}

locals {
  account_vars         = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  env_active           = read_terragrunt_config("${get_terragrunt_dir()}/../../../../active.hcl").locals.active
  acm_collector_active = read_terragrunt_config("${get_terragrunt_dir()}/../../../../${local.env_active}/acm-collector/active.hcl").locals.active
  # Foundation-tier dns-prod-zone lives at bootstrap/<role>/ (a sibling of the env
  # subtree). bootstrap_role is derived from environment.hcl (never hardcoded, D31).
  bootstrap_role       = "${read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment}_role"
  dns_prod_zone_active = read_terragrunt_config("${get_terragrunt_dir()}/../../../../../bootstrap/${local.bootstrap_role}/dns-prod-zone/active.hcl").locals.active

  environment_name   = read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment
  domains            = jsondecode(file("${get_repo_root()}/terragrunt/common/domains.json"))
  domain_cfg         = lookup(local.domains, local.environment_name, null) != null ? local.domains[local.environment_name] : tobool("ERROR: env-class '${local.environment_name}' not found in common/domains.json.")
  service_apex       = local.domain_cfg["dns_service_apex"]
  pretty_apex        = local.domain_cfg["dns_pretty_apex"]
  pretty_in_env_zone = local.pretty_apex == local.service_apex
  pretty_fqdn        = "collector.${local.pretty_apex}"

  accounts              = jsondecode(file("${get_repo_root()}/terragrunt/common/accounts.json"))
  dns_owner_account_id  = [for acct_id, cfg in local.accounts : acct_id if lookup(cfg, "is_dns_owner", false) == true]
  dns_owner_entry       = length(local.dns_owner_account_id) > 0 ? local.accounts[local.dns_owner_account_id[0]] : tobool("ERROR: no account in common/accounts.json has is_dns_owner=true.")
  dns_owner_zone_id_raw = lookup(local.dns_owner_entry, "dns_owner_zone_id", null)
  dns_owner_zone_id     = (local.dns_owner_zone_id_raw != null && local.dns_owner_zone_id_raw != "<REAL_Z_ID>") ? local.dns_owner_zone_id_raw : tobool("ERROR: dns_owner_zone_id in common/accounts.json is missing or contains the placeholder '<REAL_Z_ID>'.")

  # Re-exposed root/envcommon locals so dependency mock_outputs (where the include
  # variable is not in scope) can reference them as local.* (copyability validate).
  aws_account_id = include.root.locals.aws_account_id
}

inputs = {
  # validation CNAME lands in the env zone (sandbox) or the shared root zone (prod)
  zone_id = local.pretty_in_env_zone ? dependency.dns_prod_zone.outputs.zone_id : local.dns_owner_zone_id
  name = {
    for dvo in dependency.acm_collector.outputs.domain_validation_options :
    dvo.domain_name => dvo
    if dvo.domain_name == local.pretty_fqdn
  }[local.pretty_fqdn].resource_record_name
  type = "CNAME"
  ttl  = 60
  records = [{
    for dvo in dependency.acm_collector.outputs.domain_validation_options :
    dvo.domain_name => dvo
    if dvo.domain_name == local.pretty_fqdn
  }[local.pretty_fqdn].resource_record_value]
  alias = null

  tags = include.root.locals.common_tags
}
