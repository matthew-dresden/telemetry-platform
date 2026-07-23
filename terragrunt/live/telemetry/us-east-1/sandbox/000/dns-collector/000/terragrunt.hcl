# live/telemetry/us-east-1/<env>/000/dns-collector/000/terragrunt.hcl
#
# Service account dns-collector unit.
# Writes alias record R4 -- the public alias A record pointing the collector hostname
# at the collector CloudFront distribution -- via the primitives/route53-record
# primitive module (AC-3, E6-F5-S2-T1). Sources exactly ONE module per AC-3.
#
# ACCOUNT GUARD (D2/D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# The account id is resolved from account.hcl. A wrong-profile apply will fail fast because
# the active caller identity will not match the allowed account (D4).
#
# RECORD OWNERSHIP (AC-8 / D39):
# R4 is written ONLY by this unit. The collector-ingestion unit does NOT write R4 (D39).
# No other unit in any account declares R4 (AC-8). Single ownership is enforced by the
# dependency graph, not timers.
#
# ALIAS TARGET (D48):
# The alias target values (cloudfront_domain_name, cloudfront_hosted_zone_id) are read
# from the collector-ingestion unit using the UNPREFIXED output names. A prefixed read
# (e.g. collector_cloudfront_domain_name) yields a dangling reference that fails at
# plan/apply time (D48). The route53-record primitive requires the raw CloudFront values.
#
# EXACTLY-ONE-OF ALIAS CONTRACT (AC-3):
# The primitives/route53-record module enforces an exactly-one-of contract between
# the alias block and the records list. This unit sets:
#   alias = { name = ..., zone_id = ..., evaluate_target_health = false }
# and leaves records unset (null). Setting both alias and records causes the
# primitive's validation to fail with a clear, actionable error message.
#
# DEPENDENCY WIRING (D37):
# - dependency.dns_prod_zone: provides zone_id so R4 lands in the correct hosted zone.
# - dependency.collector_ingestion: provides the UNPREFIXED cloudfront_domain_name
#   and cloudfront_hosted_zone_id for the alias target (D48).
#
# STATE PREREQUISITE (D40):
# Run tf-state-preflight before plan/apply to confirm the sandbox state backend
# exists in the resolved service account. The root remote_state block will fail closed
# if the bucket does not exist.
#
# Applied with AWS_PROFILE=sandbox credentials.
# spec 02 Section 4.2, ledger D2, D4, D31, D37, D38, D39, D40, D45, D47, D48,
# AC-3, AC-8.

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
# dependencies: upstream unit outputs consumed as inputs (D37, D48)
# ---------------------------------------------------------------------------

dependency "dns_prod_zone" {
  config_path = "../../../../bootstrap/${local.bootstrap_role}/dns-prod-zone/${local.dns_prod_zone_active}"

  # mock_outputs allow plan/validate to resolve zone_id offline (D31, AC-8).
  # On apply the real zone_id is used (fail closed, D38).
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    zone_id = "ZMOCKZONEID00000001"
  }
}

dependency "collector_ingestion" {
  config_path = "../../collector-ingestion/${local.svc_instance}"

  # mock_outputs allow plan/validate to resolve alias target values offline (D48).
  # The UNPREFIXED output names are used -- a prefixed read yields a dangling reference.
  # On apply the real cloudfront_domain_name and cloudfront_hosted_zone_id are used.
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    cloudfront_domain_name    = "mock-collector-cf.cloudfront.net"
    cloudfront_hosted_zone_id = "Z2FDTNDATAQYW2"
  }
}

# ---------------------------------------------------------------------------
# locals: account identity, region, record naming (D31, D37, D45, D47)
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

  # Account-level locals: used for record name composition (D2/D45).
  account_vars = read_terragrunt_config(find_in_parent_folders("account.hcl"))

  # R4 record name: the collector hostname composed from the per-env apex (D45/D47).
  # Never a hardcoded prod literal -- composed from root locals dns_service_apex (D47).
  # root.hcl exposes dns_service_apex from common/domains.json keyed by env-class
  # (account.hcl is basename-only, so account_vars carries no dns_service_apex).
  collector_record_name = "collector-${include.root.locals.environment_instance}.${include.root.locals.dns_service_apex}"
}

# ---------------------------------------------------------------------------
# inputs: route53-record primitive module (AC-3, D37, D38, D47, D48)
#
# zone_id: the sandbox prod hosted zone from dns-prod-zone dependency (D38).
# name: the collector FQDN composed from root locals dns_service_apex (D45/D47).
# type: A record (alias target is a CloudFront distribution).
# alias: set to the UNPREFIXED cloudfront_domain_name and cloudfront_hosted_zone_id
#   from the collector-ingestion dependency (D48 -- prefixed read yields dangling ref).
#   evaluate_target_health = false per AWS CloudFront alias record requirement.
# records: not set (null) -- the exactly-one-of guard requires alias OR records, not both.
# ttl: not set for alias records (Route 53 ignores TTL for alias A records).
#
# The tags input uses include.root.locals.common_tags (not local.common_tags)
# because the root include block uses expose = true, making root locals accessible
# only via include.root.locals.<name> at leaf scope (spec 02).
# ---------------------------------------------------------------------------

inputs = {
  zone_id = dependency.dns_prod_zone.outputs.zone_id
  name    = local.collector_record_name
  type    = "A"

  alias = {
    name                   = dependency.collector_ingestion.outputs.cloudfront_domain_name
    zone_id                = dependency.collector_ingestion.outputs.cloudfront_hosted_zone_id
    evaluate_target_health = false
  }

  tags = include.root.locals.common_tags
}
