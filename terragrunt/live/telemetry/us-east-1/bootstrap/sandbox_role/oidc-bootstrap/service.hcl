# live/telemetry/us-east-1/bootstrap/sandbox_role/oidc-bootstrap/service.hcl
#
# Service layer for the sandbox oidc-bootstrap unit.
# Basename resolves to "oidc-bootstrap" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit creates the sandbox GitHub Actions OIDC role via references/oidc-bootstrap:
#   - telemetry-platform-gha-tg-plan: GitHub Actions read-only plan role (sub: repo:...:*)
#
# WHY (multi-account CI, WALL 1):
# A single PR can touch leaves in MORE than one account (e.g. a providers/aws/** repin that
# both prod and sandbox leaves reference). The root terragrunt.hcl generates
# allowed_account_ids = ["<unit account>"] per unit, so planning a sandbox unit under the prod
# plan role fails with "AWS account ID not allowed". terragrunt-pr.yml runs one plan job per
# account; the sandbox plan job assumes THIS role. Sandbox is ci_deploy=false (D33), so there
# is NO sandbox apply role -- sandbox is plan-only in CI; applies are local/operator.
#
# OPERATOR PREREQUISITE (D40):
# The GitHub OIDC provider must pre-exist in the sandbox account (222222222222) before applying
# this unit. The provider is created OUT-OF-BAND by the operator (NOT by this unit; D40), e.g.
#   aws iam create-open-id-connect-provider --profile telemetry-sandbox \
#     --url https://token.actions.githubusercontent.com \
#     --client-id-list sts.amazonaws.com \
#     --thumbprint-list <prod's thumbprint>
# This unit CONSUMES the provider ARN via github_oidc_provider_arn (inherited from
# _envcommon/oidc-bootstrap.hcl); it does NOT create the provider.
#
# ENVCOMMON INCLUDE (iac/02 section 1.1 line 139):
# The leaf terragrunt.hcl uses `include "envcommon"` pointing at
# terragrunt/_envcommon/oidc-bootstrap.hcl, which provides the shared roles map (from
# common/oidc-roles.json, keyed by the derived sandbox account id 222222222222) and the
# github_oidc_provider_arn (from the derived basename account id). No leaf override is needed:
# the sandbox plan role is a pure OIDC role fully described by common/oidc-roles.json.
#
# Applied once with sandbox admin credentials (AWS_PROFILE=sandbox). Bootstrap-only thereafter.

locals {
  # service resolves to the directory basename, i.e. "oidc-bootstrap".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
