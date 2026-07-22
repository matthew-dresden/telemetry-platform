# _singletons/pretty/collector/000/terragrunt.hcl
#
# Active-set PRETTY CNAME for the collector endpoint (D5 -- the blue/green switch).
# Writes:  collector.${pretty_apex}  CNAME  collector-${env_active}.${service_apex}
# i.e. the friendly name points at the ACTIVE instance set's real, set-scoped record. Promote
# flips <env>/active.hcl (env_active) and re-applies this unit so the CNAME retargets the new set.
#
# ZONE ASYMMETRY (config-derived, single unit for every env -- D2 _pretty tier decision):
#   pretty_apex == service_apex (sandbox) -> the pretty name lives in the env's OWN zone; zone_id
#     comes from the foundation bootstrap/<role>/dns-prod-zone dependency, and account.hcl resolves the env service
#     account.
#   pretty_apex != service_apex (prod)    -> the pretty name lives in the shared ROOT zone; zone_id
#     comes from dns_owner_zone_id (common/accounts.json), and account.hcl resolves the dns-owner
#     account.
# The dns_prod_zone dependency is declared unconditionally (it resolves in every env; its output is
# consumed only in the env-zone case) so a single file serves both envs with no count=0 dead unit.
#
# CNAME TARGET is a NAME (not an alias to a resource): it is composed from common/domains.json +
# <env>/active.hcl, so no dependency on the per-set dns-collector unit is required (AC-7, no hardcoded fqdn).

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (Section 4.3): local in-repo path when use_pinned_module_sources=false
  # (sandbox/root), pinned immutable git URL when true (prod).
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/route53-record?ref=providers/aws/primitives/route53-record/v1.0.2" : "${get_repo_root()}//providers/aws/primitives/route53-record"
}

# env-zone zone_id source (used only when pretty_apex == service_apex). Declared
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
  account_vars = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  # Foundation-tier dns-prod-zone lives at bootstrap/<role>/ (a sibling of the env
  # subtree). bootstrap_role is derived from environment.hcl (never hardcoded, D31).
  bootstrap_role       = "${read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment}_role"
  dns_prod_zone_active = read_terragrunt_config("${get_terragrunt_dir()}/../../../../../bootstrap/${local.bootstrap_role}/dns-prod-zone/active.hcl").locals.active

  # env class + apexes (never hardcoded; from common/domains.json)
  environment_name   = read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment
  domains            = jsondecode(file("${get_repo_root()}/terragrunt/common/domains.json"))
  domain_cfg         = lookup(local.domains, local.environment_name, null) != null ? local.domains[local.environment_name] : tobool("ERROR: env-class '${local.environment_name}' not found in common/domains.json.")
  service_apex       = local.domain_cfg["dns_service_apex"]
  pretty_apex        = local.domain_cfg["dns_pretty_apex"]
  pretty_in_env_zone = local.pretty_apex == local.service_apex

  # the ACTIVE instance set (the pretty CNAME target) -- from <env>/active.hcl, no hardcoded number
  env_active  = read_terragrunt_config("${get_terragrunt_dir()}/../../../../active.hcl").locals.active
  pretty_fqdn = "collector.${local.pretty_apex}"
  target_fqdn = "collector-${local.env_active}.${local.service_apex}"

  # root-zone id (used only when pretty_apex != service_apex) -- the is_dns_owner account zone (D38)
  accounts              = jsondecode(file("${get_repo_root()}/terragrunt/common/accounts.json"))
  dns_owner_account_id  = [for acct_id, cfg in local.accounts : acct_id if lookup(cfg, "is_dns_owner", false) == true]
  dns_owner_entry       = length(local.dns_owner_account_id) > 0 ? local.accounts[local.dns_owner_account_id[0]] : tobool("ERROR: no account in common/accounts.json has is_dns_owner=true.")
  dns_owner_zone_id_raw = lookup(local.dns_owner_entry, "dns_owner_zone_id", null)
  dns_owner_zone_id     = (local.dns_owner_zone_id_raw != null && local.dns_owner_zone_id_raw != "<REAL_Z_ID>") ? local.dns_owner_zone_id_raw : tobool("ERROR: dns_owner_zone_id in common/accounts.json is missing or contains the placeholder '<REAL_Z_ID>'.")
}

inputs = {
  # zone the pretty CNAME lands in: env zone (sandbox) or shared root zone (prod)
  zone_id = local.pretty_in_env_zone ? dependency.dns_prod_zone.outputs.zone_id : local.dns_owner_zone_id
  name    = local.pretty_fqdn
  type    = "CNAME"
  ttl     = 300
  records = [local.target_fqdn]

  tags = include.root.locals.common_tags
}
