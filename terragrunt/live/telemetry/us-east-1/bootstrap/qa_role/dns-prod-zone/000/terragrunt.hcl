# live/telemetry/us-east-1/bootstrap/<role>/dns-prod-zone/000/terragrunt.hcl
#
# FOUNDATION-TIER dns-prod-zone unit (relocated out of the disposable <env>/000
# instance sets into the stable bootstrap/<role>/ foundation tier).
#
# This unit owns three LONG-LIVED, stateful resources that must NEVER churn when a
# service-tree instance set is destroyed/recreated:
#   1. the per-env delegated public Route53 hosted zone (<env>.telemetry.example.com)
#   2. the platform "telemetry-config" Customer Managed Key (KMS CMK, alias/telemetry-config)
#   3. the SSM parameter seed (non-secret zone metadata)
# DNS *records* live in separate service units (dns-collector, dns-portal, pretty/*,
# dns-delegation, acm-validate-*) that consume zone_id/name_servers/kms_key_arn via a
# terragrunt `dependency` block. Those records are disposable; this zone + key are not.
#
# WHY THE BOOTSTRAP TIER (foundation):
# A `terragrunt run --all` over an env subtree (terragrunt/live/telemetry/us-east-1/<env>)
# never descends into this sibling bootstrap/ tier, so a service-tree destroy/recreate
# leaves the hosted zone (its nameservers) and the Customer Managed Key (its ARN)
# untouched -- no NS change, no DNS propagation window, no key recreation. The unit is
# operator-applied out-of-band (like state-bootstrap/oidc-bootstrap) and is excluded from
# the change-scoped CI apply (scripts/detect_terragrunt_units.py --exclude-bootstrap).
#
# Composition: sources exactly ONE reference module per AC-3 (references/dns-prod-zone),
# which composes the route53-zone, kms-key, and ssm-parameter primitives.
#
# IN-REPO MODULE SOURCE (D-16, spec section 4.11):
# Bootstrap-tier units ALWAYS source the in-repo module via get_repo_root() regardless of
# use_pinned_module_sources. The pin toggle governs service units only; bootstrap leaves
# are exempt from the pinned-source guard (scripts/tf_guard_pinned_sources.py skips any
# path containing a 'bootstrap' segment). This makes the unit applyable pre-push before any
# module git tag exists in the remote, and keeps the sandbox_role/prod_role leaves
# byte-identical (D31 parity): the account-class difference is resolved entirely by the
# bootstrap/<role>/account.hcl env_accounts.json lookup, never in this leaf.
#
# ACCOUNT GUARD (D2/D4):
# The root terragrunt.hcl generates the AWS provider with allowed_account_ids derived from
# bootstrap/<role>/account.hcl (env_accounts.json keyed by the role basename with "_role"
# stripped). A wrong-profile apply fails fast (D4).
#
# CUSTOM DOMAIN / ZONE NAME (D31, D45):
# The zone DNS name is resolved by _envcommon/dns-prod-zone.hcl from common/domains.json
# keyed by the env-class. In the bootstrap tier the env-class is recovered from the role
# basename (sandbox_role -> sandbox, prod_role -> prod, qa_role -> qa); no env-class literal
# is present in this leaf (D31).
#
# STATE PREREQUISITE (D40):
# The role's state-bootstrap unit must have been applied so the S3 state backend + lock
# exist in the resolved account. The root remote_state block fails closed otherwise.
#
# Applied with the role's AWS profile (telemetry-sandbox / telemetry-prod / telemetry-qa).
# spec 02 Section 4.2, ledger D2, D16, D31, D40, D45, D47, AC-3, AC-18.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

include "envcommon" {
  path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/dns-prod-zone.hcl"
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Bootstrap-tier units always source the in-repo module regardless of
  # use_pinned_module_sources (D-16, spec section 4.11). Using get_repo_root()
  # unconditionally makes this unit applyable pre-push before the module git tag
  # exists, and keeps the per-role leaves byte-identical. The module's child source
  # variables (zone_source/kms_source/ssm_source) default to the in-repo relative
  # paths (providers/aws/references/dns-prod-zone/variables.tf), so no *_source
  # overrides are needed here -- the relative-path defaults resolve correctly when the
  # reference module is sourced from get_repo_root().
  source = "${get_repo_root()}//providers/aws/references/dns-prod-zone"
}

# ---------------------------------------------------------------------------
# inputs: dns-prod-zone reference module (AC-3, D45, D47)
#
# zone_name and kms_key_principals are merged from _envcommon/dns-prod-zone.hcl (which
# resolves the env-class from the bootstrap role to key common/domains.json + the KMS
# principal set). Leaf-level inputs below extend the shared template.
#
# region: passed explicitly so the KMS ViaService policy scopes to us-east-1 (D47).
# ---------------------------------------------------------------------------
inputs = {
  region = include.root.locals.region

  # -------------------------------------------------------------------------
  # Teardown guard (AC-2, spec section 0.2). force_destroy = true allows the
  # delegated subdomain zone to be destroyed even when non-empty (stray records
  # remain) during a dev rebuild. Pre-prod / dev-disposable only.
  # -------------------------------------------------------------------------
  force_destroy = true

  tags = include.root.locals.common_tags
}
