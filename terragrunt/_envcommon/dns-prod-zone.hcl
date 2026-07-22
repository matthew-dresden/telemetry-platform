# terragrunt/_envcommon/dns-prod-zone.hcl
#
# Shared input template for the dns-prod-zone service unit.
# Included by every sandbox and prod dns-prod-zone leaf via:
#   include "envcommon" {
#     path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/dns-prod-zone.hcl"
#     expose         = true
#     merge_strategy = "deep"
#   }
#
# Ledger decisions applied here:
#   D31 - sandbox and prod differ only by folder path plus inputs
#   D37 - canonical name: prod_hosted_zone_id is the SSM parameter key under which the
#         zone id is published; downstream consumers reference it by this exact name
#   D45 - domain zone name derived from common/domains.json dns_service_apex (not hardcoded)

locals {
  # Namespace is derived here from the hierarchy layer files directly (D1, D36),
  # mirroring the root.hcl derivation. The hierarchy files are read in this
  # _envcommon's LEAF evaluation context via find_in_parent_folders (ancestors)
  # plus a leaf-relative read of service_instance.hcl (D36). root.hcl itself is
  # NOT read here: read_terragrunt_config(find_in_parent_folders("root.hcl"))
  # evaluates root.hcl's own find_in_parent_folders hierarchy reads relative to
  # root.hcl's directory (terragrunt/), where they cannot resolve, so reading root
  # standalone aborts parse. Deriving from the leaf-resolvable layer files keeps the
  # copy-any-level property intact (spec section 4.8, D1, D36).
  product_vars              = read_terragrunt_config(find_in_parent_folders("product.hcl"))
  region_vars               = read_terragrunt_config(find_in_parent_folders("region.hcl"))
  environment_vars          = read_terragrunt_config(find_in_parent_folders("environment.hcl"))
  environment_instance_vars = read_terragrunt_config(find_in_parent_folders("environment_instance.hcl"))
  service_vars              = read_terragrunt_config(find_in_parent_folders("service.hcl"))
  service_instance_vars     = read_terragrunt_config("${get_terragrunt_dir()}/service_instance.hcl")

  region_clean = replace(local.region_vars.locals.aws_region, "-", "")
  # Namespace field rule (mirrors root.hcl): "-" SEPARATES the 6 fields, "_" JOINS words
  # WITHIN a field. The per-field replace below only ever rewrites a multi-word service/tier
  # token (collector-ingestion -> collector_ingestion); region_clean already has no "-".
  # local.namespace is the CANONICAL "_"-in-field form (tags, networks.json key, "_"-legal
  # names); local.namespace_dns is the FLATTENED "_"->"-" form for "_"-hostile kinds (S3, DNS).
  namespace = join("-", [
    for f in [
      local.product_vars.locals.product,
      local.region_clean,
      local.environment_vars.locals.environment,
      local.environment_instance_vars.locals.environment_instance,
      local.service_vars.locals.service,
      local.service_instance_vars.locals.service_instance,
    ] : replace(f, "-", "_")
  ])
  namespace_dns = replace(local.namespace, "_", "-")

  # account.hcl carries aws_account_id; use_pinned_module_sources gates the source.
  account_vars   = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  aws_account_id = local.account_vars.locals.aws_account_id

  # Zone name resolved from common/domains.json keyed by env-class (D45/D47): the
  # prod zone DNS name is the per-env-class dns_service_apex (prod ->
  # prod.telemetry.example.com). domains.json is anchored on get_repo_root()
  # so it resolves from any copied subtree depth (D3). The lookup fails fast (no
  # fallback) if the env-class key is absent (spec S3.5, S7).
  #
  # Env-class derivation (foundation-tier aware): in the service tree the env-class IS
  # the environment basename (sandbox/prod/qa). When the dns-prod-zone unit lives in the
  # FOUNDATION (bootstrap) tier its environment basename is "bootstrap" and the env-class
  # is carried by the environment_instance role token (sandbox_role -> sandbox), so recover
  # it by stripping the "_role" suffix. This keys off the SAME "_role" form root.hcl's tier
  # classifier uses -- no env-class literal and no "bootstrap" literal is hardcoded (D31).
  _env_basename       = local.environment_vars.locals.environment
  _env_instance_token = local.environment_instance_vars.locals.environment_instance
  environment_name    = endswith(local._env_instance_token, "_role") ? trimsuffix(local._env_instance_token, "_role") : local._env_basename
  domains             = jsondecode(file("${get_repo_root()}/terragrunt/common/domains.json"))
  domain_cfg          = lookup(local.domains, local.environment_name, null) != null ? local.domains[local.environment_name] : tobool("ERROR: env-class '${local.environment_name}' not found in common/domains.json -- add a row for this env-class before deploying.")
  route53_zone_domain = local.domain_cfg["dns_service_apex"]

  # Deploy role ARN composed from the derived account id and the deploy_role_name in
  # common/accounts.json (D2/D4, mirroring root.hcl). The OIDC-assumed deploy role is
  # the deploy identity (no second role assumption, D4). accounts.json is anchored on
  # get_repo_root() so it resolves from any copied subtree depth (D3). The lookup fails
  # fast (no fallback) if the derived account id is absent (spec S3.5, S7).
  accounts        = jsondecode(file("${get_repo_root()}/terragrunt/common/accounts.json"))
  account_cfg     = lookup(local.accounts, local.aws_account_id, null) != null ? local.accounts[local.aws_account_id] : tobool("ERROR: account id '${local.aws_account_id}' not found in common/accounts.json -- add a row for this account id before deploying this scope.")
  deploy_role_arn = "arn:aws:iam::${local.aws_account_id}:role/${local.account_cfg["deploy_role_name"]}"

  # use_pinned_module_sources: read from account.hcl via a direct map index (no default,
  # no fallback) so a missing key aborts parse immediately (spec Section 4.3, AC-6).
  use_pinned_module_sources = local.account_vars.locals.use_pinned_module_sources

  # ---------------------------------------------------------------------------
  # Cross-account remote-state read grants (foundation-tier gated module inputs).
  #
  # The DNS-owner-account plan role reads each env's foundation dns-prod-zone remote
  # state (name_servers / zone_id) cross-account for the per-env _singletons/dns_owner
  # dns-delegation and _singletons/pretty units (which live in the dns-owner account).
  # Terragrunt treats a 403 reading that dependency state as FATAL -- mock_outputs rescue
  # only an ABSENT state, not access-denied -- so the foundation unit grants that role:
  #   - kms:Decrypt on THIS account's tfstate CMK via an ADDITIVE aws_kms_grant (a grant
  #     can never remove the key's root access, so it is lockout-safe), and
  #   - read on THIS unit's own remote-state bucket via an aws_s3_bucket_policy that
  #     replicates terragrunt's EnforcedTLS + RootAccess statements verbatim (no drift)
  #     then adds the cross-account read statements.
  # Both are gated by the module: an empty grantee list (the standalone-module default)
  # creates neither and performs no plan-time AWS read.
  #
  # The dns-owner plan role ARN is composed from the is_dns_owner account in
  # common/accounts.json + its plan_role_name -- never hardcoded (mirrors the
  # dns-delegation dns_owner_zone_id derivation and scripts.partition_units_by_account).
  # Fail-fast (no fallback) if no account is marked is_dns_owner=true (D2/D38, spec S7).
  dns_owner_account_ids   = [for acct_id, cfg in local.accounts : acct_id if lookup(cfg, "is_dns_owner", false) == true]
  dns_owner_account_id    = length(local.dns_owner_account_ids) > 0 ? local.dns_owner_account_ids[0] : tobool("ERROR: no account in common/accounts.json has is_dns_owner=true -- mark the DNS-owner account before deploying this scope.")
  dns_owner_plan_role_arn = "arn:aws:iam::${local.dns_owner_account_id}:role/${local.accounts[local.dns_owner_account_id]["plan_role_name"]}"

  # THIS unit's own remote-state CMK alias + S3 bucket name, derived IDENTICALLY to
  # root.hcl (D10/B21) so the gated grant + bucket policy target this unit's real backend
  # resources. The bucket name uses the hash-suffixed shortening for names > 63 chars.
  state_cmk_alias       = "alias/${local.aws_account_id}-tfstate"
  bucket_name_raw       = "${local.namespace_dns}-tfstate"
  bucket_name_shortened = length(local.bucket_name_raw) > 63 ? "${substr(local.namespace_dns, 0, 45)}-${substr(md5(local.namespace), 0, 8)}-tfstate" : local.bucket_name_raw
  state_bucket_name     = lower(replace(local.bucket_name_shortened, "_", "-"))
}

inputs = {
  # Zone name resolved from common/domains.json (D45): prod -> prod.telemetry.example.com.
  zone_name = local.route53_zone_domain

  # KMS CMK alias suffix for the prod DNS/cert customer-managed key (iac/04 section 3.7 row S4).
  # The kms-key primitive prefixes 'alias/' automatically.
  kms_alias = "telemetry-config"

  # SSM parameters seeded by the dns-prod-zone unit (non-secret zone metadata).
  # D37: prod_hosted_zone_id is the canonical SSM parameter key under which the zone id
  # is published. Downstream consumers (collector-ingestion, portal) read the zone id
  # by passing prod_hosted_zone_id = dependency.dns_prod_zone.outputs.zone_id at leaf level.
  ssm_parameters = {
    "/${local.namespace}/dns/prod_hosted_zone_id" = {
      type  = "String"
      value = "PENDING"
    }
  }

  # KMS key principals: the deploy identity needs kms:Decrypt for SSM SecureString reads.
  # The account root principal is ALWAYS included -- the AWS-recommended KMS baseline that
  # delegates key-policy control to the account's IAM policies and satisfies the KMS CreateKey
  # lockout-safety check regardless of which principal performs the apply (NOT a wildcard;
  # access is still governed by IAM). When the account is CI-deployed (ci_deploy=true, e.g.
  # prod) the OIDC deploy role is additionally granted directly so the CI deploy identity has
  # tight kms:Decrypt/GenerateDataKey access without relying solely on a broad IAM policy.
  # When the account is NOT CI-deployed (ci_deploy=false, e.g. sandbox) that OIDC deploy role
  # does not exist (granting a non-existent role fails CreateKey with
  # MalformedPolicyDocumentException), so only root is granted. Account id is derived (D2).
  kms_key_principals = local.account_cfg["ci_deploy"] ? [
    local.deploy_role_arn,
    "arn:aws:iam::${local.aws_account_id}:root",
    ] : [
    "arn:aws:iam::${local.aws_account_id}:root",
  ]

  # ---------------------------------------------------------------------------
  # Cross-account remote-state read grants (gated; see the locals above). Enabling
  # these makes the foundation dns-prod-zone unit grant the DNS-owner plan role
  # kms:Decrypt on this account's tfstate CMK (additive aws_kms_grant) and read on
  # this unit's own state bucket (aws_s3_bucket_policy that coexists with terragrunt's
  # EnforcedTLS + RootAccess). The grantee list is the single dns-owner plan role, so a
  # cross-account dns-delegation / _pretty plan can load this zone's name_servers without
  # a fatal 403. Empty grantee lists (the module default) would create neither resource.
  # ---------------------------------------------------------------------------
  tfstate_cmk_alias                = local.state_cmk_alias
  tfstate_cmk_decrypt_grantee_arns = [local.dns_owner_plan_role_arn]
  tfstate_bucket_name              = local.state_bucket_name
  tfstate_bucket_account_id        = local.aws_account_id
  tfstate_bucket_read_grantee_arns = [local.dns_owner_plan_role_arn]
}
