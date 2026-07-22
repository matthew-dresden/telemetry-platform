# live/telemetry/us-east-1/<env>/000/acm-validate-collector/000/terragrunt.hcl
#
# Service account acm-validate-collector unit.
# Writes R3 -- the collector pretty-SAN ACM validation CNAME -- into the
# delegated sandbox subdomain zone via the primitives/route53-record primitive
# module (AC-3, AC-7, E6-F4-S1-T1). Sources exactly ONE module per AC-3.
#
# ACCOUNT GUARD (D2/D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# The account id is resolved from account.hcl. A wrong-profile apply fails fast because
# the active caller identity will not match the allowed account (D4).
#
# RECORD CONTRACT (AC-7, D19):
# This unit writes EXACTLY ONE CNAME -- R3 -- for the collector pretty-SAN
# (collector.sandbox.telemetry.example.com). The name and value are
# sourced from dependency.acm_collector.outputs.domain_validation_options by
# selecting the entry whose domain_name matches the pretty-SAN. The route53-record
# exactly-one-of alias/records guard is satisfied by setting alias = null and
# records = [dvo.resource_record_value] (AC-7). No alias block is used.
#
# SINGLE RECORD OWNERSHIP (AC-8):
# R3 is written ONLY by this unit. dns-delegation is NS-only and does not own R3.
# No other unit in any account writes R3 (AC-8).
#
# CROSS-ACCOUNT ZONE (AC-7, D38):
# The delegated sandbox subdomain zone id is sourced from
# dependency.dns_prod_zone.outputs.zone_id (fail-fast: the dependency block has
# no mock for zone_id on apply -- only plan/validate mocks are permitted). This
# local is the canonical fail-fast-guarded zone identity per D38.
#
# CUSTOM DOMAIN TOGGLE (D31, spec section 1.5):
# The route53-record primitive module (primitives/route53-record) does not declare
# a flag variable for this toggle. The toggle is gated at the terragrunt level:
# this unit is authored only when the env-class custom domain flag is true
# (domains.json sandbox row). No module input carries the flag (spec AC #3, D-7).
# This matches the prod root acm-validate-collector pattern.
#
# STATE PREREQUISITE (D40):
# Run tf-state-preflight (scripts.tf_state_preflight) before plan/apply to confirm the
# sandbox state backend (S3 bucket + DynamoDB lock table) was created by state-bootstrap.
# The root remote_state block will fail closed if the bucket does not exist.
#
# Applied with AWS_PROFILE=sandbox credentials.
# spec 02 Section 4.2, ledger D2, D4, D19, D26, D31, D37, D38, D40, D45, D47, AC-3, AC-7, AC-8.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (spec Section 4.3, AC-7, AC-8):
  # use_pinned_module_sources=false (sandbox): in-repo local source via get_repo_root().
  # use_pinned_module_sources=true (prod): pinned immutable git URL with ?ref= tag.
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/route53-record?ref=providers/aws/primitives/route53-record/v1.0.2" : "${get_repo_root()}//providers/aws/primitives/route53-record"
}

# ---------------------------------------------------------------------------
# dependencies: upstream unit outputs consumed as inputs (D37, AC-7, D26)
# ---------------------------------------------------------------------------

dependency "acm_collector" {
  config_path = "../../acm-collector/${local.svc_instance}"

  # mock_outputs allow plan/validate/init/destroy to run offline before acm-collector
  # is applied. The domain_validation_options mock returns a single-entry set that
  # satisfies the for expression in locals below without requiring live ACM outputs.
  # mock_outputs_allowed_terraform_commands restricts mocks to offline commands only --
  # apply never uses mocks (fail closed on apply, D31, AC-8).
  # mock values are derived from resolved identity locals (local.region, local.aws_account_id,
  # local.service_apex) so that a copied leaf presents mocks for its own scope (AC-13).
  mock_outputs_allowed_terraform_commands = ["validate", "plan", "init", "destroy"]
  mock_outputs = {
    domain_validation_options = toset([
      {
        domain_name           = "collector-${local.environment_instance}.${local.service_apex}"
        resource_record_name  = "_mock-collector-service-apex-cname.${local.service_apex}."
        resource_record_type  = "CNAME"
        resource_record_value = "mock-collector-service-apex-acm-validation.acm-validations.aws."
      }
    ])
    certificate_arn = "arn:aws:acm:${local.region}:${local.aws_account_id}:certificate/mock-collector-cert-id"
  }
}

dependency "dns_prod_zone" {
  config_path = "../../../../bootstrap/${local.bootstrap_role}/dns-prod-zone/${local.dns_prod_zone_active}"

  # mock_outputs allow plan/validate/init/destroy to resolve the zone_id offline.
  # On apply the real zone_id is read from the dns-prod-zone state (fail closed, D38).
  mock_outputs_allowed_terraform_commands = ["validate", "plan", "init", "destroy"]
  mock_outputs = {
    zone_id = "ZMOCKZONEID00000001"
  }
}

# ---------------------------------------------------------------------------
# locals: account identity and pretty-SAN selection (D31, D37, D45, D47)
# ---------------------------------------------------------------------------

locals {
  # Instance-relative local: resolves to the basename of this service-instance directory
  # (e.g. "000") so that every same-tier sibling dependency config_path interpolates
  # the owning instance index rather than a hardcoded literal (spec section 4.4,
  # AC-FUNC-001, AC-FUNC-002). Terragrunt evaluates locals before dependency blocks,
  # so local.svc_instance is valid inside config_path expressions (spec section 4.4).
  # Copying this folder to index "001" causes svc_instance to resolve to "001",
  # wiring all sibling deps at the new index with zero edits (spec section G5, AC-FUNC-003).
  svc_instance = basename(get_terragrunt_dir())

  # Foundation-tier dns-prod-zone lives at bootstrap/<role>/ (a sibling of the env
  # subtree), OUT of the disposable 000 instance sets. bootstrap_role is derived from
  # environment.hcl (sandbox -> sandbox_role, prod -> prod_role, qa -> qa_role); never
  # hardcoded (D31). The foundation active set is read from its own active.hcl so a copied
  # service instance still resolves the foundation's active set (not the consumer index).
  bootstrap_role       = "${read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment}_role"
  dns_prod_zone_active = read_terragrunt_config("${get_terragrunt_dir()}/../../../../bootstrap/${local.bootstrap_role}/dns-prod-zone/active.hcl").locals.active

  # Account-level locals for toggle-driven source resolution (spec Section 4.3, AC-7, AC-8).
  account_vars = read_terragrunt_config(find_in_parent_folders("account.hcl"))

  # Account id and region resolved from identity locals for mock ARN construction (AC-13, D31).
  aws_account_id = local.account_vars.locals.aws_account_id
  region         = include.root.locals.region

  # D45/D47: per-env SERVICE-apex FQDN composed from the per-env apex (never hardcoded).
  # This per-env-account validator writes the validation CNAME for the SERVICE-apex
  # collector domain (collector.<service_apex>) into the per-env zone (dns-prod-zone),
  # which owns that domain. The pretty-SAN domain (collector.<pretty_apex>) is validated
  # separately by the DNS-owner-account validator into the DNS-owner zone. In env-classes
  # where service_apex == pretty_apex (e.g. sandbox) the cert carries a single deduplicated
  # dvo and this selects that same entry, so the change is a no-op there (D31, D45).
  service_apex           = include.root.locals.dns_service_apex
  collector_service_fqdn = "collector-${include.root.locals.environment_instance}.${local.service_apex}"

  # Re-exposed root/envcommon locals so dependency mock_outputs (where the include
  # variable is not in scope) can reference them as local.* (copyability validate).
  environment_instance = include.root.locals.environment_instance
}

# ---------------------------------------------------------------------------
# inputs: route53-record primitive module (AC-3, D37, AC-7)
#
# type = CNAME: R3 is a DNS validation CNAME record (D19, AC-7).
# name: ACM-emitted validation CNAME name from the per-env service-apex dvo entry (AC-7).
# records: ACM-emitted validation CNAME value from the per-env service-apex dvo entry (AC-7).
# alias = null: exactly-one-of guard requires records set and alias null (AC-7).
# zone_id: the delegated per-env subdomain zone from dns-prod-zone (D38, fail-fast).
#
# PER-ENV SERVICE-APEX dvo SELECTION (D19, AC-7):
# The service-apex domain_validation_options entry for R3 is selected here in inputs
# (not locals) because it references dependency.acm_collector.outputs, which
# terragrunt only resolves at input-evaluation time -- a locals block cannot
# reference dependency outputs (terragrunt v1.0.7: "dependency is not defined").
# The for expression selects exactly the entry whose domain_name matches the
# collector service FQDN (local.collector_service_fqdn, a pure local). This per-env
# validator owns the service-apex CNAME in the per-env zone; the pretty-SAN CNAME is
# owned by the DNS-owner-account validator. If no entry matches, the result is an empty map
# and the map lookup fails fast at plan time with a meaningful Terraform error --
# no silent fallback (D31). The same map-lookup expression yields the single dvo
# object for both name and records.
#
# The tags input uses include.root.locals.common_tags (not local.common_tags)
# because the root include block uses expose = true, making root locals
# accessible only via include.root.locals.<name> at leaf scope (spec 02).
# ---------------------------------------------------------------------------

inputs = {
  zone_id = dependency.dns_prod_zone.outputs.zone_id
  name = {
    for dvo in dependency.acm_collector.outputs.domain_validation_options :
    dvo.domain_name => dvo
    if dvo.domain_name == local.collector_service_fqdn
  }[local.collector_service_fqdn].resource_record_name
  type = "CNAME"
  ttl  = 60
  records = [{
    for dvo in dependency.acm_collector.outputs.domain_validation_options :
    dvo.domain_name => dvo
    if dvo.domain_name == local.collector_service_fqdn
  }[local.collector_service_fqdn].resource_record_value]
  alias = null

  tags = include.root.locals.common_tags
}
