# live/telemetry/us-east-1/<env>/000/acm-collector/000/terragrunt.hcl
#
# Service account acm-collector unit.
# Requests the TLS certificate for the collector endpoint via the
# references/acm-cert-managed reference module (AC-3, E1-F3-S1-T1), which wraps the
# acm-certificate primitive and adds the gated, default-OFF cross-account remote-state
# read used by the dns-owner pretty/validate plan. Sources exactly ONE module per AC-3.
#
# ACCOUNT GUARD (D2/D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# The account id is resolved from account.hcl. A wrong-profile apply will fail fast because
# the active caller identity will not match the allowed account (D4).
#
# CERTIFICATE REQUEST CONTRACT (D19, D24, D26):
# wait_for_validation = false so the request returns immediately. DNS validation
# is created downstream in a separate acm-validate-collector unit (D26) to avoid
# the dependency cycle described in D24.
#
# MANDATORY PRETTY SAN (D19):
# subject_alternative_names includes the pretty (custom) FQDN sourced from
# _envcommon/acm-collector.hcl, which composes it from local.root.locals.dns_pretty_apex
# (root loaded via read_terragrunt_config; dns_pretty_apex resolved from
# terragrunt/common/domains.json via root local.domain_cfg; account.hcl
# supplies only aws_account_id).
# The cross-account validation CNAME for this SAN is written by acm-validate-collector
# in the prod-dns account downstream (D26).
#
# NO DANGLING INPUTS (AC-3):
# references/acm-cert-managed does not declare a hosted-zone identifier variable.
# Passing an undeclared input violates the single-module contract (AC-3).
#
# STATE PREREQUISITE (D40):
# Run tf-state-preflight (scripts.tf_state_preflight) before plan/apply to confirm the
# sandbox state backend (S3 bucket + DynamoDB lock table) was created by state-bootstrap.
# The root remote_state block will fail closed if the bucket does not exist.
#
# Applied with AWS_PROFILE=sandbox credentials.
# spec 02 Section 4.2, ledger D2, D4, D19, D24, D26, D31, D37, D40, D45, D47, AC-3, AC-7.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

include "envcommon" {
  path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/acm-collector.hcl"
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (spec Section 4.3, AC-7, AC-8):
  # use_pinned_module_sources=false (sandbox): in-repo local source via get_repo_root().
  # use_pinned_module_sources=true (prod): pinned immutable git URL with ?ref= tag.
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/references/acm-cert-managed?ref=providers/aws/references/acm-cert-managed/v0.1.0" : "${get_repo_root()}//providers/aws/references/acm-cert-managed"
}

# ---------------------------------------------------------------------------
# locals: account identity for toggle-driven source resolution (spec Section 4.3, AC-7, AC-8)
# ---------------------------------------------------------------------------

locals {
  account_vars = read_terragrunt_config(find_in_parent_folders("account.hcl"))
}

# ---------------------------------------------------------------------------
# inputs: acm-cert-managed reference module (AC-3, D37, D45, D47)
#
# domain_name and subject_alternative_names are merged from
# _envcommon/acm-collector.hcl (via the "envcommon" include above), which also wires the
# gated cross-account remote-state read inputs (tfstate_bucket_*) enabled only in prod.
# wait_for_validation = false is also supplied by the shared template (D24).
# The tags input uses include.root.locals.common_tags (not local.common_tags)
# because the root include block uses expose = true, making root locals
# accessible only via include.root.locals.<name> at leaf scope (spec 02).
#
# No hosted-zone identifier is passed: references/acm-cert-managed does not declare
# that variable and passing it would be a dangling input violating AC-3.
# ---------------------------------------------------------------------------

inputs = {
  tags = include.root.locals.common_tags
}
