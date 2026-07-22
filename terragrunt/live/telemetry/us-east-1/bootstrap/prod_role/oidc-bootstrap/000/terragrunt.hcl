# live/telemetry/us-east-1/bootstrap/prod_role/oidc-bootstrap/000/terragrunt.hcl
#
# Prod account (111111111111) oidc-bootstrap unit.
# Creates telemetry-platform-gha-tg-plan and telemetry-platform-gha-tg-apply via
# references/oidc-bootstrap, using the shared envcommon template for roles and
# the github_oidc_provider_arn input (iac/02 section 1.1 line 139, AC-2).
#
# OPERATOR PREREQUISITE (D40):
# The GitHub OIDC provider must pre-exist in prod account (111111111111) before
# applying this unit. This unit CONSUMES the provider ARN via github_oidc_provider_arn
# (inherited from _envcommon/oidc-bootstrap.hcl); it does NOT create the provider
# (aws_iam_openid_connect_provider is not declared here). A missing or invalid ARN
# causes the oidc-bootstrap module variables.tf validation to fail fast (AC-1, D40).
#
# ACCOUNT GUARD (D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# The prod account id is 111111111111. A wrong-profile apply will fail fast.
#
# ROLES MAP FROM common/ via _envcommon (E8-F6-S2-T1, spec section 4.9, AC-16):
# The roles map is inherited from _envcommon/oidc-bootstrap.hcl, which sources it
# from common/oidc-roles.json keyed by the derived account id. This leaf overrides
# only the telemetry-platform-gha-tg-apply role to add its inline permission policy
# (AC-5); the plan role and apply role trust sub are fully resolved by envcommon.
#
# APPLY ROLE TRUST (AC-1, spec AC-14):
# The telemetry-platform-gha-tg-apply role's trust sub is bound to:
#   repo:example-org/telemetry-platform:environment:prod-apply
# This is declared in common/oidc-roles.json for the prod account entry.
# The envcommon resolves this sub from oidc-roles.json and passes it through
# as part of the roles map to the references/oidc-bootstrap module.
#
# APPLY ROLE PERMISSION POLICY -- cross-account DNS writes (AC-5, DoD line 202):
# The telemetry-platform-gha-tg-apply role must be able to assume the
# telemetry-platform-dns-writer role in the dns-owner account (account_role="dns-owner"
# in common/accounts.json) to perform cross-account Route53 writes
# (R1/R3/R5/R7/R9). This leaf resolves the dns-owner account id dynamically from
# common/accounts.json using the fail-fast lookup-then-tobool idiom (D4/D40) and
# constructs the dns-writer ARN without any hardcoded account id literal.
#
# DNS-OWNER ACCOUNT LOOKUP (D4, no hardcoded ARN):
# accounts.json is loaded with jsondecode(file(...)). A for expression filters
# to the single entry whose account_role == "dns-owner". If no match is found,
# tobool() fires an error at Terragrunt parse time before any AWS call (fail-fast,
# spec section 4.1, S3.5). The resolved account id is combined with the
# deploy_role_name from that entry to form the fully-qualified ARN.
#
# DOCUMENTED ORDERING (AC-4, spec AC-19):
# The dependency from this unit to state-bootstrap and to the operator-created
# OIDC provider is DOCUMENTED ORDERING ONLY -- no Terraform output edge exists.
# See docs/bootstrap-ordering.md for the first-deploy sequence.
#
# Applied once with prod admin credentials.
# spec 02 Section 4.6, ledger D40, D48.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

# ---------------------------------------------------------------------------
# envcommon include (iac/02 section 1.1 line 139, AC-2)
#
# The _envcommon/oidc-bootstrap.hcl template provides:
#   - github_oidc_provider_arn: ARN of the operator-created OIDC provider
#     derived from the basename account id (copy-safe, spec section 4.9)
#   - roles: per-account roles map loaded from common/oidc-roles.json
#     keyed by the derived account id (E8-F6-S2-T1, spec section 4.9)
#
# The leaf inputs block below overrides the envcommon roles map to add the
# inline permission policy on telemetry-platform-gha-tg-apply (AC-5).
# With merge_strategy = "deep" the leaf roles map entry is deep-merged into
# the envcommon roles map, so only the apply role is overridden here.
# ---------------------------------------------------------------------------
include "envcommon" {
  path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/oidc-bootstrap.hcl"
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # D16: bootstrap units always use in-repo local source, regardless of the prod
  # account.hcl use_pinned_module_sources toggle. The toggle is honored only by
  # service units. Bootstrap must be applyable pre-push (before the branch is
  # published and release tags exist). include.envcommon.locals.bootstrap_use_pinned_module_sources
  # is always false (declared in _envcommon/oidc-bootstrap.hcl), which unconditionally
  # resolves to the in-repo path (spec section 4.12, D16).
  source = include.envcommon.locals.bootstrap_use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/references/oidc-bootstrap?ref=providers/aws/references/oidc-bootstrap/v1.0.1" : "${get_repo_root()}//providers/aws/references/oidc-bootstrap"
}

# ---------------------------------------------------------------------------
# locals: dns-owner account resolution from common/accounts.json (AC-5, D4)
#
# The dns-writer role lives in the account whose account_role == "dns-owner"
# in common/accounts.json (currently 444444444444). The account id is resolved
# dynamically so this leaf does not hardcode the account id -- copying to a new
# account or renaming the dns-owner account only requires updating accounts.json.
#
# Fail-fast idiom (spec section 4.1, S3.5):
#   1. Load all accounts from common/accounts.json.
#   2. Filter to entries where account_role == "dns-owner".
#   3. If no match, tobool() fires an error at parse time.
#   4. Construct the dns-writer ARN from the resolved account id and role name.
# ---------------------------------------------------------------------------
locals {
  # Account-level locals for toggle-driven source resolution (spec Section 4.3, AC-7, AC-8).
  account_vars = read_terragrunt_config(find_in_parent_folders("account.hcl"))

  _all_accounts = jsondecode(file("${get_repo_root()}/terragrunt/common/accounts.json"))

  # Filter to the single account whose account_role is "dns-owner".
  # keys() + for produces a list; we take index [0] after the null guard below.
  _dns_owner_account_ids = [
    for account_id, cfg in local._all_accounts :
    account_id
    if lookup(cfg, "account_role", "") == "dns-owner"
  ]

  # Fail fast if no dns-owner account is found in accounts.json.
  _dns_owner_account_id = length(local._dns_owner_account_ids) > 0 ? local._dns_owner_account_ids[0] : tobool(
    "ERROR: no account with account_role='dns-owner' found in common/accounts.json -- add a dns-owner entry before deploying oidc-bootstrap (AC-5, D4)."
  )

  # Resolve the deploy_role_name for the dns-owner account (telemetry-platform-dns-writer).
  _dns_owner_cfg        = local._all_accounts[local._dns_owner_account_id]
  _dns_writer_role_name = local._dns_owner_cfg["deploy_role_name"]

  # Fully-qualified dns-writer role ARN (no hardcoded account id).
  dns_writer_role_arn = "arn:aws:iam::${local._dns_owner_account_id}:role/${local._dns_writer_role_name}"

  # Inline permission policy granting sts:AssumeRole on the dns-writer role.
  # The policy JSON is constructed with jsonencode to avoid string escaping issues.
  # This policy is passed as an inline_policies override for the apply role only.
  _dns_assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowAssumeRoleDnsWriter"
        Effect   = "Allow"
        Action   = ["sts:AssumeRole"]
        Resource = [local.dns_writer_role_arn]
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# inputs: prod leaf inputs merged with envcommon template (AC-1, AC-2, AC-5, D40)
#
# The envcommon template provides github_oidc_provider_arn and roles (from
# common/oidc-roles.json keyed by prod account id 111111111111). The prod
# entry in oidc-roles.json contains exactly:
#   - telemetry-platform-gha-tg-plan (sub: repo:...:*)
#   - telemetry-platform-gha-tg-apply (sub: environment:prod-apply)
#
# This leaf overrides roles to add the inline permission policy on the apply
# role (AC-5). With merge_strategy = "deep" the override is merged into the
# envcommon roles map at the apply-role key level. The plan role is unchanged.
#
# inline_policies map contract (references/oidc-bootstrap/variables.tf):
#   inline_policies = optional(map(string), {})
# Each entry is: { "<policy-name>" = "<JSON policy document string>" }
#
# No Terraform dependency block on state-bootstrap (AC-4, spec AC-19).
# ---------------------------------------------------------------------------
inputs = {
  roles = {
    telemetry-platform-gha-tg-apply = {
      sub = "repo:example-org/telemetry-platform:environment:prod-apply"
      # The prod apply role IS the deploy identity (D4: the OIDC-assumed role is the
      # deploy identity, no second role assumption for the env's own account). It must
      # carry deploy permissions for every resource kind the env tree provisions
      # (S3, CloudFront, ECS, Lambda, KMS, IAM, Route53, WAF, Glue, Athena, QuickSight,
      # Budgets, SNS, CloudWatch, EC2/VPC, ...). AdministratorAccess matches the
      # established OIDC-deploy-identity pattern already used by the QA terratest role
      # (telemetry-platform-gha-terratest -> AdministratorAccess in common/oidc-roles.json).
      # Cross-account Route53 writes still go through the dns-writer role-chain below.
      managed_policy_arns = ["arn:aws:iam::aws:policy/AdministratorAccess"]
      inline_policies = {
        dns-writer-assume-role = local._dns_assume_role_policy
      }
      description = "GitHub Actions apply role for telemetry-platform (prod account)"
    }
  }

  # ---------------------------------------------------------------------------
  # Child module source overrides (AC-7, AC-8, spec Section 4.3, Section 5).
  # D16: bootstrap applies always use in-repo local sources (not pinned git URLs).
  # include.envcommon.locals.bootstrap_use_pinned_module_sources is always false,
  # so oidc_role_source always resolves to the in-repo path for bootstrap.
  # Passing null for a const=true variable causes a Terraform crash (panic: value is
  # null); the explicit in-repo path is always safe (D-16, spec Section 4.3).
  # ---------------------------------------------------------------------------
  oidc_role_source = include.envcommon.locals.bootstrap_use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/iam-role?ref=providers/aws/primitives/iam-role/v1.0.1" : "${get_repo_root()}//providers/aws/primitives/iam-role"

  tags = include.root.locals.common_tags
}
