# terragrunt/_envcommon/acm-collector.hcl
#
# Shared input template for the acm-collector service unit.
# Included by every sandbox and prod acm-collector leaf via:
#   include "envcommon" {
#     path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/acm-collector.hcl"
#     expose         = true
#     merge_strategy = "deep"
#   }
#
# Ledger decisions applied here:
#   D19 - pretty-name SAN is MANDATORY on the collector cert
#   D24 - wait_for_validation = false; validation is created downstream by collector-ingestion
#   D37 - canonical input names: collector_service_fqdn, collector_pretty_fqdn
#   D45 - per-env domain apexes from terragrunt/common/domains.json via root
#          terragrunt.hcl local.domain_cfg (not hardcoded prod literals, not account.hcl)
#   D47 - FQDNs composed from dns_service_apex and dns_pretty_apex

locals {
  # D45/D47: per-env domain apexes sourced from terragrunt/common/domains.json keyed by
  # env-class. _envcommon shared templates have no include block in scope, so
  # include.root.locals.* is invalid here; and root.hcl cannot be read standalone via
  # read_terragrunt_config(find_in_parent_folders("root.hcl")) because root.hcl's own
  # find_in_parent_folders hierarchy reads resolve relative to root.hcl's directory
  # (terragrunt/) and abort parse. The apexes are therefore resolved here the same way
  # root.hcl does: read the env-class from environment.hcl (leaf-context find_in_parent_folders)
  # and look it up in common/domains.json (anchored on get_repo_root(), D3). The lookup
  # fails fast (no fallback) if the env-class key is absent (spec S3.5, S7). The same
  # template resolves to:
  # prod -> collector.prod.telemetry.example.com (service)
  # prod -> collector.telemetry.example.com (pretty, mandatory SAN, D19)
  # by these inputs alone (D31).
  environment_vars          = read_terragrunt_config(find_in_parent_folders("environment.hcl"))
  environment_name          = local.environment_vars.locals.environment
  environment_instance_vars = read_terragrunt_config(find_in_parent_folders("environment_instance.hcl"))
  environment_instance      = local.environment_instance_vars.locals.environment_instance
  domains                   = jsondecode(file("${get_repo_root()}/terragrunt/common/domains.json"))
  domain_cfg                = lookup(local.domains, local.environment_name, null) != null ? local.domains[local.environment_name] : tobool("ERROR: env-class '${local.environment_name}' not found in common/domains.json -- add a row for this env-class before deploying.")
  service_apex              = local.domain_cfg["dns_service_apex"]
  pretty_apex               = local.domain_cfg["dns_pretty_apex"]

  # D37: canonical names for the collector endpoints. These locals are referenced by the
  # inputs block below and expose the canonical names to any leaf that exposes this include.
  collector_service_fqdn = "collector-${local.environment_instance}.${local.service_apex}"
  collector_pretty_fqdn  = "collector.${local.pretty_apex}"

  # use_pinned_module_sources: read from account.hcl via a direct map index (no default,
  # no fallback) so a missing key aborts parse immediately (spec Section 4.3, AC-6).
  account_vars              = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  use_pinned_module_sources = local.account_vars.locals.use_pinned_module_sources

  # ---------------------------------------------------------------------------
  # Cross-account remote-state read grant (gated; ENABLED only where the dns-owner
  # account reads this unit's state cross-account).
  #
  # The dns-owner _singletons/pretty validate-collector unit reads THIS unit's
  # domain_validation_options (the pretty-SAN ACM validation CNAME) from the env-account
  # remote state. terragrunt treats a 403 reading that dependency state as FATAL
  # (mock_outputs rescue only an ABSENT state, not access-denied), so this unit grants the
  # dns-owner plan role READ on its own remote-state bucket via the acm-cert-managed
  # reference's gated aws_s3_bucket_policy (which replicates terragrunt's EnforcedTLS +
  # RootAccess statements, so terragrunt's additive backend policy management sees no drift).
  #
  # The cross-account read only happens when the pretty apex differs from the service apex
  # (prod): then the _singletons/pretty units run in the DNS-OWNER account and read this
  # state cross-account. When pretty_apex == service_apex (sandbox/qa) the pretty/validate
  # units run IN this account, so the in-account deploy role already has root access and the
  # grant stays OFF (envs-identical-code: the same template, gated by config alone -- D31).
  #
  # The KMS side needs no per-unit grant: the tfstate CMK is account-wide (one CMK per
  # account, alias/<account>-tfstate) and the dns-owner plan role already holds an
  # account-level kms:Decrypt grant on it (applied by the foundation dns-prod-zone unit),
  # covering every unit's state object in the account -- so only the bucket policy is enabled.
  #
  # The dns-owner plan role ARN is composed from the is_dns_owner account in
  # common/accounts.json + its plan_role_name -- never hardcoded (mirrors dns-prod-zone and
  # scripts.partition_units_by_account). Fail-fast (no fallback) if no account is marked
  # is_dns_owner=true (D2/D38, spec S7).
  enable_cross_account_state_read = local.pretty_apex != local.service_apex

  accounts                = jsondecode(file("${get_repo_root()}/terragrunt/common/accounts.json"))
  dns_owner_account_ids   = [for acct_id, cfg in local.accounts : acct_id if lookup(cfg, "is_dns_owner", false) == true]
  dns_owner_account_id    = length(local.dns_owner_account_ids) > 0 ? local.dns_owner_account_ids[0] : tobool("ERROR: no account in common/accounts.json has is_dns_owner=true -- mark the DNS-owner account before deploying this scope.")
  dns_owner_plan_role_arn = "arn:aws:iam::${local.dns_owner_account_id}:role/${local.accounts[local.dns_owner_account_id]["plan_role_name"]}"

  # THIS unit's own remote-state bucket name + owner account id, derived IDENTICALLY to
  # root.hcl (D10/B21) so the gated bucket policy targets this unit's real backend bucket.
  # The namespace is built from the hierarchy layer files the same way root.hcl builds it
  # ("-" SEPARATES the 6 fields; "_" JOINS words WITHIN a field), then flattened for the
  # "_"-hostile S3 name. The hash-suffix shortening matches root.hcl for names > 63 chars.
  aws_account_id        = local.account_vars.locals.aws_account_id
  product_vars          = read_terragrunt_config(find_in_parent_folders("product.hcl"))
  region_vars           = read_terragrunt_config(find_in_parent_folders("region.hcl"))
  service_vars          = read_terragrunt_config(find_in_parent_folders("service.hcl"))
  service_instance_vars = read_terragrunt_config("${get_terragrunt_dir()}/service_instance.hcl")
  region_clean          = replace(local.region_vars.locals.aws_region, "-", "")
  namespace = join("-", [
    for f in [
      local.product_vars.locals.product,
      local.region_clean,
      local.environment_name,
      local.environment_instance,
      local.service_vars.locals.service,
      local.service_instance_vars.locals.service_instance,
    ] : replace(f, "-", "_")
  ])
  namespace_dns         = replace(local.namespace, "_", "-")
  bucket_name_raw       = "${local.namespace_dns}-tfstate"
  bucket_name_shortened = length(local.bucket_name_raw) > 63 ? "${substr(local.namespace_dns, 0, 45)}-${substr(md5(local.namespace), 0, 8)}-tfstate" : local.bucket_name_raw
  state_bucket_name     = lower(replace(local.bucket_name_shortened, "_", "-"))
}

inputs = {
  # acm-cert-managed reference inputs (D37: compose from canonical locals above).
  # domain_name is the ACM primary domain (the service-name SAN, same-account validated).
  domain_name = local.collector_service_fqdn

  # D19: pretty-name SAN is MANDATORY. The cross-account validation CNAME (R3) for
  # this SAN is written by acm-validate-collector (account 444444444444) downstream.
  subject_alternative_names = [local.collector_pretty_fqdn]

  # D24: wait_for_validation = false. This unit is a PURE cert request.
  # aws_acm_certificate_validation is created by collector-ingestion (D26).
  wait_for_validation = false

  # Gated cross-account remote-state read (see the locals above). ENABLED only where the
  # dns-owner account reads this state cross-account (prod). The grantee is the single
  # dns-owner plan role, so the dns-owner _singletons/pretty validate-collector plan can
  # load this unit's domain_validation_options without a fatal 403. Empty grantee list
  # (sandbox/qa) => the reference creates no bucket policy. Only the bucket policy is
  # enabled; the account-wide tfstate CMK grant already covers KMS decrypt.
  tfstate_bucket_name              = local.state_bucket_name
  tfstate_bucket_account_id        = local.aws_account_id
  tfstate_bucket_read_grantee_arns = local.enable_cross_account_state_read ? [local.dns_owner_plan_role_arn] : []
}
