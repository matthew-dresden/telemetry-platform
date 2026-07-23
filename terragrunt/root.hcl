# terragrunt/root.hcl  (ROOT)
#
# Single source of truth for remote state and provider generation across the
# telemetry collector deployment (spec 02 Section 2, F1/F11 fix).
#
# Every leaf unit includes this file via:
#   include "root" {
#     path           = find_in_parent_folders("root.hcl")
#     expose         = true
#     merge_strategy = "deep"
#   }
#
# This file replaces the per-leaf duplication of the reference (flaw F1). It hoists
# remote_state, generate "provider", generate "backend", generate "versions", and all
# derivation locals into a single root so leaves add only dependency{} + inputs{}.
#
# Ledger decisions applied here:
#   D1  - canonical seven-layer hierarchy
#   D2  - account id basename-derived; fail-fast guard via common/accounts.json lookup
#   D3  - common/ is OUTSIDE the copy boundary; all paths anchored on get_repo_root()
#   D4  - NO second role assumption; OIDC-assumed role IS the deploy identity
#   D5  - S3-native locking (use_lockfile = true); no DynamoDB lock table (E8-F3-S1-T2)
#   D8  - backend carries no hardcoded profile (ambient credentials only)
#   D10 - S3 state hardening (KMS CMK, versioning, public-access-block, TLS, access logging)
#   B21 - hash-suffixed bucket name, collision-free, <=63 chars
#   D35 - Terragrunt 1.0.7 CLI surface only; no removed v0.x tokens
#   D36 - service_instance.hcl read leaf-relative, not via find_in_parent_folders

locals {
  # ---- read every hierarchy layer (basename-derived) (D1, Section 1.3) ------
  product_vars              = read_terragrunt_config(find_in_parent_folders("product.hcl"))
  region_vars               = read_terragrunt_config(find_in_parent_folders("region.hcl"))
  account_vars              = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  environment_vars          = read_terragrunt_config(find_in_parent_folders("environment.hcl"))
  environment_instance_vars = read_terragrunt_config(find_in_parent_folders("environment_instance.hcl"))
  service_vars              = read_terragrunt_config(find_in_parent_folders("service.hcl"))
  # D36: service_instance.hcl lives in the LEAF dir alongside terragrunt.hcl, not an ancestor.
  # find_in_parent_folders searches ANCESTORS only and would fail to find it. Read it leaf-relative.
  # (Matches the sql-polyglot reference testrunner/000/terragrunt.hcl:8 read_terragrunt_config("service_instance.hcl").)
  service_instance_vars = read_terragrunt_config("${get_terragrunt_dir()}/service_instance.hcl")

  # ---- read common/ resolver (spec S4.1, D3) ---------------------------------
  # common.hcl is the shared entrypoint for base locals and the dns-owner zone-id
  # guard (fires at parse time for any caller of common.hcl). Anchored on
  # get_repo_root() so it resolves from any copied subtree depth (D3).
  common_vars = read_terragrunt_config("${get_repo_root()}/terragrunt/common/common.hcl")

  # ---- load JSON maps directly for per-scope keyed lookups (spec S4.1) ------
  # Per-scope keyed lookups MUST be performed in this root file (spec S4.3,
  # common.hcl constraint note): the caller-derived keys (aws_account_id,
  # environment_name) are not available in common.hcl's evaluation context.
  # Maps are loaded via get_repo_root()-anchored paths (D3).
  accounts = jsondecode(file("${get_repo_root()}/terragrunt/common/accounts.json"))
  domains  = jsondecode(file("${get_repo_root()}/terragrunt/common/domains.json"))
  contacts = jsondecode(file("${get_repo_root()}/terragrunt/common/contacts.json"))

  # ---- extract identity (mirrors ref terragrunt.hcl:12-17) ------------------
  product_family   = local.product_vars.locals.product
  region           = local.region_vars.locals.aws_region
  aws_account_id   = local.account_vars.locals.aws_account_id # resolved by account.hcl (env-keyed via env_accounts.json; account abstracted out of the folder path)
  environment_name = local.environment_vars.locals.environment

  # ---- named AWS profile (env-keyed, per-unit) -------------------------------
  # account.hcl resolves the named profile from common/env_accounts.json so a single
  # `run --all` over an env writes each unit to ITS account (service vs dns_owner) via the
  # generated provider's/backend's profile. Backward-compatible: account.hcl files that
  # predate the env-keyed migration carry no aws_profile, so this resolves to "" and the
  # provider falls back to ambient credentials (the prior D8 behavior) -- no broken parse.
  # Under CICD the runner authenticates via an OIDC-assumed role (ambient credentials), so
  # the named profile must be omitted from the generated provider/backend -- a named profile
  # would make the AWS SDK look up a non-existent profile in the CI runner. Setting
  # TG_USE_OIDC=true blanks the profile so ambient OIDC credentials are used; locally (unset)
  # the env-keyed named profile selects the account. Environment-agnostic: same artifact, the
  # credential source is injected at run time (12-factor).
  _use_oidc              = tobool(get_env("TG_USE_OIDC", "false"))
  aws_profile            = local._use_oidc ? "" : lookup(local.account_vars.locals, "aws_profile", "")
  _provider_profile_line = local.aws_profile != "" ? "profile             = \"${local.aws_profile}\"" : ""
  environment_instance   = local.environment_instance_vars.locals.environment_instance
  service                = local.service_vars.locals.service
  service_instance       = local.service_instance_vars.locals.service_instance

  # ---- resolve account config from common/accounts.json (spec S4.1, AC-4) --
  # Fail-fast: a derived account id absent from accounts.json aborts parsing
  # with an actionable message naming the id and file, before any AWS call.
  # No fallback is provided (spec S3.5, S7).
  # lookup() returns null for missing keys (safe); bracket access is used in the
  # truthy branch after the null guard confirms the key is present (safe).
  account_cfg = lookup(local.accounts, local.aws_account_id, null) != null ? local.accounts[local.aws_account_id] : tobool("ERROR: account id '${local.aws_account_id}' not found in common/accounts.json -- add a row for this account id before deploying this scope.")

  # ---- resolve domain config from common/domains.json (spec S4.1, AC-5) ----
  # Fail-fast: a missing env-class key aborts parsing with an actionable message
  # naming the scope and file. No fallback (spec S3.5, S7).
  domain_cfg = lookup(local.domains, local.environment_name, null) != null ? local.domains[local.environment_name] : tobool("ERROR: env-class '${local.environment_name}' not found in common/domains.json -- add a row for this env-class before deploying.")

  # ---- resolve contacts config from common/contacts.json (spec S4.1, AC-5) -
  # Fail-fast: a missing env-class key aborts parsing with an actionable message
  # naming the scope and file. No fallback (spec S3.5, S7).
  contacts_cfg = lookup(local.contacts, local.environment_name, null) != null ? local.contacts[local.environment_name] : tobool("ERROR: env-class '${local.environment_name}' not found in common/contacts.json -- add a row for this env-class before deploying.")

  # ---- compose scope values from resolved common/ config (spec S4.3) --------
  # These values were previously inherited from account.hcl literals (removed in
  # E8-F2-S1-T1). They are now resolved from common/ mappings so copying any
  # folder resolves its profile/role/domain/contacts purely from the derived path
  # plus the common/ data with zero edits to the copied files (spec G2/G3).

  # Per-account values (from account_cfg):
  account_role     = local.account_cfg["account_role"]     # e.g. "sandbox", "prod-infra"
  deploy_role_name = local.account_cfg["deploy_role_name"] # e.g. "telemetry-platform-gha-tg-apply"
  # Compose the deploy role ARN from the derived account id and the role name
  # from accounts.json. No second role assumption (D4): this is the OIDC-assumed
  # role; it is exposed here so leaf units and _envcommon files can reference it
  # via include.root.locals.deploy_role_arn.
  deploy_role_arn = "arn:aws:iam::${local.aws_account_id}:role/${local.deploy_role_name}"

  # ---- module source identity (exposed to leaves for pinned git sources) ------
  # Leaves reference include.root.locals.module_git_base in terraform.source so the
  # pinned in-repo module URL derives from platform.json (org/repo/host), not a
  # hardcoded org/repo literal. Local-source (use_pinned_module_sources=false) still
  # uses ${get_repo_root()}//providers/... unchanged.
  module_git_base = local.common_vars.locals.module_git_base

  # ---- cross-account CI role assumption (per-unit, OIDC only) -----------------
  # WALL 2 fix: a single CICD `run --all apply` can span more than one account (e.g. a
  # prod-scoped apply that also covers the prod _dns_owner/_pretty units, which live in the
  # shared dns-owner account). Under OIDC the runner holds ONE ambient role -- the deploy role
  # of the PRIMARY account it assumed (TG_CI_PRIMARY_ACCOUNT_ID, set by terragrunt-apply.yml).
  # That ambient role cannot satisfy a unit whose generated provider asserts
  # allowed_account_ids = ["<other account>"], and it cannot read/write that unit's remote
  # state in the other account.
  #
  # For any unit whose account differs from the primary CI account, the generated provider +
  # S3 backend assume that unit's account deploy role (local.deploy_role_arn) -- the SDK/CI
  # analog of the local named-profile model (D8): locally a `run --all` writes each unit to its
  # account via the env-keyed named profile; under OIDC the same cross-account write is done by
  # assuming the unit-account role. The primary account's own units assume nothing (the ambient
  # role already IS their deploy identity, preserving D4 for the common single-account case).
  #
  # The dns-owner deploy role (telemetry-platform-dns-writer) is reached by role-chaining FROM the
  # prod apply role, whose oidc-bootstrap inline policy grants sts:AssumeRole on it. The
  # dns-writer trust grants only sts:AssumeRole (not sts:TagSession), so session tagging is
  # disabled in both assume_role blocks below (mirrors the configure-aws-credentials
  # role-skip-session-tagging fix, #120).
  #
  # PRODUCTION-INERT for local runs and single-account CI: TG_CI_PRIMARY_ACCOUNT_ID is unset
  # locally (named-profile model handles cross-account) and equals the unit account for a
  # single-account CI scope, so _ci_cross_account is false and no assume_role is generated --
  # the provider/backend are byte-for-byte unchanged from before in those cases.
  _ci_primary_account_id = get_env("TG_CI_PRIMARY_ACCOUNT_ID", "")
  _ci_cross_account = (
    local._use_oidc &&
    local._ci_primary_account_id != "" &&
    local._ci_primary_account_id != local.aws_account_id
  )
  # Provider assume_role block (HCL fragment) injected into the generated provider when a
  # cross-account CI assumption is required; empty string otherwise. A double-quoted string
  # (not a heredoc) is used so the ternary's false branch (": ...") parses cleanly.
  _provider_assume_role_block = local._ci_cross_account ? "assume_role {\n        role_arn     = \"${local.deploy_role_arn}\"\n        session_name = \"tg-ci-${local.aws_account_id}\"\n      }" : ""
  # S3 backend assume_role config (a map merged into the backend config) when cross-account;
  # empty map otherwise. external_id is omitted; session tagging is left off by default
  # (the AWS SDK does not tag assume_role sessions unless tags are supplied).
  _backend_assume_role = local._ci_cross_account ? {
    assume_role = {
      role_arn     = local.deploy_role_arn
      session_name = "tg-ci-${local.aws_account_id}"
    }
  } : {}

  # Per-env-class domain values (from domain_cfg):
  dns_service_apex     = local.domain_cfg["dns_service_apex"]     # e.g. "sandbox.telemetry.example.com"
  dns_pretty_apex      = local.domain_cfg["dns_pretty_apex"]      # e.g. "telemetry.example.com"
  enable_custom_domain = local.domain_cfg["enable_custom_domain"] # bool: true for prod, false for sandbox

  # ---- derived (mirrors ref terragrunt.hcl:24) --------------------------------
  region_clean = replace(local.region, "-", "")

  # ---- fully qualified namespace (mirrors ref terragrunt.hcl:27-34) ----------
  # Field rule: "-" SEPARATES the 6 fields; "_" JOINS words WITHIN a field. So a
  # multi-word service dir (collector-ingestion) or a multi-word tier/role token
  # (sandbox_role, dns_owner) becomes a single "_"-joined field, and the 6 fields
  # are stitched with "-". region_clean already has no "-" (useast1), so the per-field
  # replace below only ever touches multi-word service/tier/role tokens.
  #
  # Two derived forms:
  #   local.namespace      -- CANONICAL: "_"-in-field. The tag value + the source for
  #                           every "_"-legal name (IAM, Glue, log groups, ...).
  #   local.namespace_dns  -- FLATTENED: all "_" -> "-". Fed to "_"-hostile kinds
  #                           (S3 buckets, DNS labels/hostnames, ACM, CloudFront).
  namespace = join("-", [
    for f in [
      local.product_family,
      local.region_clean,
      local.environment_name,
      local.environment_instance,
      local.service,
      local.service_instance,
    ] : replace(f, "-", "_")
  ])

  # Flattened form for "_"-hostile resource kinds (S3, DNS, ACM, CloudFront). The
  # canonical namespace above keeps "_" within fields; here every "_" collapses to
  # "-" so the result is a legal S3/DNS/hostname token.
  namespace_dns = replace(local.namespace, "_", "-")

  # ---- state key (mirrors ref terragrunt.hcl:41) -----------------------------
  relative_path = path_relative_to_include()

  # ---- S3 bucket name: hash-suffixed, collision-free shortening (D10/B21) ----
  #      Replaces the reference's lossy first-8-char substr scheme
  #      (ref terragrunt.hcl:53-70); the developer_name prefix path is removed
  #      (shared infra); the account id prefix is added so the two-account tree
  #      never collides across accounts.
  # State bucket name derived from the NAMESPACE (the dir tree), NOT the account
  # (operator decision: the bucket correlates to the tree and is globally unique
  # because the namespace embeds the env-class, e.g. sandbox vs prod). The account
  # id prefix is removed so a folder copied to a new env-class produces a bucket
  # name that follows the tree, never the account.
  # S3 is "_"-hostile, so the bucket name is built from local.namespace_dns (the
  # flattened "-"-only form), never the canonical "_"-in-field namespace.
  bucket_name_raw = "${local.namespace_dns}-tfstate"

  # When the raw name exceeds the S3 63-char limit, keep a 45-char namespace prefix
  # plus an 8-char md5 of the FULL namespace. The md5 suffix guarantees uniqueness
  # even when two units share the 45-char prefix (e.g. pretty-validate-collector vs
  # pretty-validate-portal), closing B21. Layout: <=45 prefix + "-" + 8 hex + "-tfstate" <= 63.
  # md5 is taken over the CANONICAL namespace (stable, "_"-in-field) so the hash is the
  # same regardless of the flattening; the prefix uses the flattened dns form.
  bucket_name_shortened = length(local.bucket_name_raw) > 63 ? (
    "${substr(local.namespace_dns, 0, 45)}-${substr(md5(local.namespace), 0, 8)}-tfstate"
  ) : local.bucket_name_raw

  final_bucket_name = lower(replace(local.bucket_name_shortened, "_", "-"))

  # ---- state-backend KMS CMK alias (D10/B19) ----------------------------------
  #      The CMK that encrypts state is created/owned by the state-bootstrap unit
  #      (spec 02 Section 2.3); the root references it by a deterministic alias so
  #      Terragrunt's auto-created bucket uses aws:kms with a customer-managed key.
  #      The state-bootstrap unit must create this alias by exact name.
  state_kms_key_alias = "alias/${local.aws_account_id}-tfstate"

  # ---- access-logging target bucket (D10/B19) ---------------------------------
  #      The state-bootstrap unit must create this bucket by exact name (spec 02 section 2.1).
  state_access_log_bucket = "${local.aws_account_id}-tfstate-access-logs"

  # ---- tier / account-role, DERIVED from the env-instance token (no hardcode) -
  # The 4th field (environment_instance) is the dir basename verbatim and has THREE
  # mutually-exclusive sub-classes, told apart by FORM:
  #   - all digits ("000", "001", ...) -> a numbered instance set      => tier "per-set"
  #   - "*_role" (sandbox_role/prod_role/qa_role/dns_owner_role)        => tier "bootstrap"
  #     (a per-account bootstrap role under bootstrap/)
  #   - a bare tier word ("shared" / "dns_owner" / "pretty")           => tier == that word
  #     (a once-per-env singleton tier under _singletons/)
  # Any other form fails LOUD (no fallback, D-fail-fast): an unrecognized env_instance
  # token means the tree grew a 4th-field shape the namespace scheme does not define.
  #
  # COPYABILITY-TEST SYNTHETIC INSTANCE (spec section 4.8, tg-copyability-test):
  # The copy-any-level proof copies an env-index scope to a synthetic basename that
  # carries the reserved "cpytst-" marker prefix in front of the numeric set
  # (e.g. "cpytst-001"). That basename becomes environment_instance for the copied
  # scope, so the per-set numeric branch must recognize it as a numbered instance set
  # or the whole root config aborts on tobool() and every include.root.locals.*
  # reference in the copied units fails. The leading "cpytst-" marker is reserved by
  # the harness (scripts.tg_copyability_test._CPYTST_PREFIX) and never appears in a
  # real deploy, so accepting an optional "cpytst-" prefix in front of the digits keeps
  # the classifier production-inert while keeping the copied env-index scope valid with
  # zero edits to the copied files.
  tier = can(regex("^(cpytst-)?[0-9]+$", local.environment_instance)) ? "per-set" : (
    endswith(local.environment_instance, "_role") ? "bootstrap" : (
      contains(["shared", "dns_owner", "pretty"], local.environment_instance) ? local.environment_instance : (
        tobool("ERROR: unrecognized environment_instance '${local.environment_instance}' -- expected a numeric set (NNN), a bootstrap role (*_role), or a singleton tier (shared|dns_owner|pretty).")
      )
    )
  )

  # ---- common tags: one metadata key per NAMESPACE FIELD on every resource -----
  # Operator decision (H1): every resource carries a tag per namespace field (key =
  # field name, value = field value, e.g. env = prod), all derived (never hardcoded),
  # applied centrally via the provider default_tags. Tags are full/unabbreviated so a
  # length-fitted or hashed resource NAME is always recoverable from the tags.
  common_tags = {
    terragrunt           = "true"
    terraform            = "true"
    product              = local.product_family
    product_family       = local.product_family
    region               = local.region
    account_id           = local.aws_account_id
    env                  = local.environment_name
    environment_name     = local.environment_name
    env_instance         = local.environment_instance
    environment_instance = local.environment_instance
    service              = local.service
    service_instance     = local.service_instance
    tier                 = local.tier
    account_role         = local.account_role
    namespace            = local.namespace
    # account_role above is the accounts.json account class (sandbox/prod-infra),
    # already a root local; included here as useful metadata alongside the tier.
  }

  # ---- reduced tag set for the AWS S3-OBJECT 10-tag cap (config.js) -----------
  # AWS caps S3 OBJECT tags at 10 (most resources allow 50). The full common_tags
  # above is 15 keys, so any S3 object inheriting it via the default provider's
  # default_tags can NEVER apply ("Object tags cannot be greater than 10"). The
  # aws.config_tags provider alias generated below carries THIS reduced set (7 keys)
  # as its default_tags so a tag-capped object stays at or under the limit. The
  # redundant duplicate keys are dropped -- product (== product_family), env_name/
  # env_instance long forms, account_id, region, and service_instance are all fully
  # recoverable from the retained `namespace` tag (which encodes the whole tree
  # position), so no information is lost. Resources using this alias add only a
  # couple of their own explicit tags, keeping the union within the 10-tag cap.
  s3_object_tags = {
    terraform      = "true"
    product_family = local.product_family
    env            = local.environment_name
    env_instance   = local.environment_instance
    service        = local.service
    tier           = local.tier
    namespace      = local.namespace
  }

  # ---- offline-validate backend switch (copyability proof, spec section 4.8) -
  # The tg-copyability-test harness copies a scope to a synthetic destination and
  # runs `terragrunt run --all -- validate` to prove the copied scope is valid with
  # zero edits. That proof is structural and must NOT contact AWS: the synthetic
  # scope's S3 state bucket does not exist, and dependency-output resolution would
  # run `terraform output` against an uninitialized S3 backend. Both fail offline.
  #
  # When TG_OFFLINE_BACKEND is truthy the generated backend below is `local` instead
  # of `s3`. A local backend lets `terraform validate` (and dependency `terraform
  # output`, which then returns no outputs so the dependency block's mock_outputs
  # engage) run with no AWS access. This switch is PRODUCTION-INERT: the variable is
  # unset in the CI apply workflow and for any real deploy, so the default ("false")
  # preserves the hardened S3 backend exactly as before. It is the documented
  # Terragrunt pattern for offline `run --all -- validate` in CI.
  offline_backend = tobool(get_env("TG_OFFLINE_BACKEND", "false"))

  # bootstrap_local_backend (D-15) -- one-time state-bootstrap first-apply override.
  #
  # The state-bootstrap leaf carries its own `generate "backend"` block that writes an
  # empty `backend "local" {}` to backend.tf when BOOTSTRAP_LOCAL_BACKEND=true, so the
  # very first apply (which CREATES the hardened S3 state backend) can run against a
  # local backend before that backend exists. That leaf block alone is insufficient:
  # this root `remote_state` block ALSO generates backend.tf and, because remote_state
  # generation is applied after standalone generate blocks, it OVERWRITES the leaf's
  # local backend with the S3 config -- so the first apply tries to read a not-yet-
  # existing S3 bucket and fails. To let the leaf override stand, the root remote_state
  # generation target is redirected to a Terraform-ignored ".disabled" path while this
  # flag is set (see remote_state.generate.path below). This is PRODUCTION-INERT: the
  # flag is unset for every normal apply and all of CICD, so the default ("false")
  # preserves the hardened S3 backend generation to backend.tf exactly as before.
  bootstrap_local_backend = tobool(get_env("BOOTSTRAP_LOCAL_BACKEND", "false"))

  # backend_type is a plain string ternary (both results are strings -- no type
  # mismatch). backend_config is selected by indexing a map keyed on the stringified
  # offline flag rather than a ternary: the local-vs-S3 config objects have different
  # attribute sets, and a ternary over them fails HCL's "inconsistent conditional
  # result types" check. Map indexing returns the selected object with its own type.
  backend_type = local.offline_backend ? "local" : "s3"
  backend_config = {
    "true"  = local.local_backend_config
    "false" = local.s3_backend_config
  }[tostring(local.offline_backend)]

  # Local backend: state in a cache-relative file (no AWS). The S3-only keys do not
  # apply, so they are omitted -- terragrunt rejects S3 keys on a `local` backend.
  local_backend_config = {
    path = "terraform.tfstate"
  }

  # S3 backend: the hardened production state backend (D10/B19), unchanged. This is
  # the only branch ever used for a real deploy (TG_OFFLINE_BACKEND unset).
  s3_backend_config = merge(local.aws_profile != "" ? { profile = local.aws_profile } : {}, local._backend_assume_role, {
    bucket  = local.final_bucket_name
    key     = "${local.relative_path}/terraform.tfstate"
    region  = local.region
    encrypt = true

    # S3-native conditional-write locking (spec section 4.3, decision D5, AC-14).
    # Supersedes the deprecated per-account lock table (removed in E8-F3-S1-T2).
    use_lockfile = true

    # ---- state bucket hardening (D10/B19) ------------------------------------
    # Customer-managed KMS CMK (created by the state-bootstrap unit, Section 2.3).
    bucket_sse_algorithm  = "aws:kms"
    bucket_sse_kms_key_id = local.state_kms_key_alias

    # Versioning ON (skip flag false), public access fully blocked (skip flag false),
    # TLS enforced (skip flag false), root-access bucket policy on (skip flag false).
    skip_bucket_versioning             = false
    skip_bucket_ssencryption           = false
    skip_bucket_public_access_blocking = false
    skip_bucket_enforced_tls           = false
    skip_bucket_root_access            = false

    # Access logging to a dedicated log bucket, partitioned by event date.
    accesslogging_bucket_name                         = local.state_access_log_bucket
    accesslogging_target_object_partition_date_source = "EventTime"
    accesslogging_target_prefix                       = "${local.final_bucket_name}/"

    # Tags on the state bucket.
    s3_bucket_tags = local.common_tags
  })
}

# Backend: S3 state with S3-native conditional-write locking (use_lockfile = true).
# Terragrunt AUTO-CREATES the bucket on init, HARDENED per D10/B19.
# DynamoDB lock table removed in E8-F3-S1-T2 (decision D5, spec section 4.3):
# use_lockfile requires Terraform/OpenTofu >= 1.10, satisfied by the pinned 1.15.5.
# (D4/D8: no profile; no second role assumption)
remote_state {
  # Default: the hardened S3 backend (local.backend_type/backend_config resolve to
  # the S3 branch when TG_OFFLINE_BACKEND is unset). When the env var is truthy
  # (tg-copyability-test only) they resolve to a `local` backend so the structural
  # validate and dependency-output resolution run with no AWS access (spec section
  # 4.8). See the backend-selection locals above. The S3 branch is the only one ever
  # used for a real deploy -- the offline branch never affects an apply.
  backend = local.backend_type
  generate = {
    # Normally generates the backend to backend.tf (S3 for a real deploy, local when
    # TG_OFFLINE_BACKEND is truthy). During the one-time state-bootstrap first apply
    # (BOOTSTRAP_LOCAL_BACKEND=true, D-15) the target is redirected to a Terraform-
    # ignored ".disabled" path so it does NOT overwrite the bootstrap leaf's own
    # `generate "backend"` block (which writes the empty local backend to backend.tf).
    # This is PRODUCTION-INERT: the flag is unset for every normal apply and all of
    # CICD, so the path resolves to backend.tf exactly as before.
    path      = local.bootstrap_local_backend ? "backend_remote_state_disabled.tf.disabled" : "backend.tf"
    if_exists = "overwrite_terragrunt"
  }
  config = local.backend_config
}

# Provider generation (mirrors ref terragrunt.hcl:104-112; D4: no second role assumption, default_tags + account guard)
# D8: backend carries no hardcoded profile (ambient credentials only: OIDC role in CI, AWS_PROFILE locally).
# allowed_account_ids is derived from the basename account id (copy-safe) -- spec S3.6, AC-15.
#
# A SECOND aliased provider (aws.config_tags) is generated for every unit. It is identical
# to the default provider (same region, profile, assume_role, account guard) EXCEPT its
# default_tags carry the reduced local.s3_object_tags (<= the AWS 10-tag S3-OBJECT cap)
# instead of the full 15-key common_tags. The portal reference module routes
# aws_s3_object.portal_config (config.js) through this alias so the object's effective tag
# count stays within the cap -- the full common_tags would push it over 10 and the object
# could never apply ("Object tags cannot be greater than 10"). For every other unit the
# alias is simply an unused provider configuration (Terraform permits this), so it is inert
# tree-wide and only takes effect where a resource sets provider = aws.config_tags.
generate "provider" {
  path      = "provider_generated.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<-EOF
    provider "aws" {
      region              = "${local.region}"
      ${local._provider_profile_line}
      ${local._provider_assume_role_block}
      allowed_account_ids = ["${local.aws_account_id}"]
      default_tags {
        tags = ${jsonencode(local.common_tags)}
      }
    }

    provider "aws" {
      alias               = "config_tags"
      region              = "${local.region}"
      ${local._provider_profile_line}
      ${local._provider_assume_role_block}
      allowed_account_ids = ["${local.aws_account_id}"]
      default_tags {
        tags = ${jsonencode(local.s3_object_tags)}
      }
    }
  EOF
}

# Pin Terraform version repo-wide. required_providers is declared in each module's
# own versions.tf -- emitting a second required_providers block here would cause
# Terraform 1.15.5 to error with "Duplicate required providers configuration"
# because the generated file lands in the same cached module directory as the
# module's own versions.tf (D-16, spec section 4.11). Version constraint only.
generate "versions" {
  path      = "versions_generated.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<-EOF
    # Generated by Terragrunt root.hcl -- version constraint only.
    # required_providers is declared in versions.tf alongside this file.
    terraform {
      required_version = ">= 1.15.5"
    }
  EOF
}

# Repo-wide default inputs every module accepts (overridable per leaf).
inputs = {
  tags      = local.common_tags
  namespace = local.namespace
  region    = local.region
}
