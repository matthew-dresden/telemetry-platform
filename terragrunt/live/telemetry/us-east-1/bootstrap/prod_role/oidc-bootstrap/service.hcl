# live/telemetry/us-east-1/bootstrap/prod_role/oidc-bootstrap/service.hcl
#
# Service layer for the prod oidc-bootstrap unit.
# Basename resolves to "oidc-bootstrap" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit creates the prod OIDC IAM roles via references/oidc-bootstrap:
#   - telemetry-platform-gha-tg-plan: GitHub Actions read-only plan role (sub: repo:...:*)
#   - telemetry-platform-gha-tg-apply: GitHub Actions apply role (sub: environment:prod-apply)
#
# The apply role (telemetry-platform-gha-tg-apply) also grants sts:AssumeRole on the root
# account (444444444444) telemetry-platform-dns-writer role, enabling cross-account DNS
# record writes for all prod service units that manage DNS entries (R1/R3/R5/R7/R9).
#
# OPERATOR PREREQUISITE (D40):
# The GitHub OIDC provider must pre-exist in the prod account (111111111111) before
# applying this unit. This unit CONSUMES the provider ARN via github_oidc_provider_arn
# (inherited from _envcommon/oidc-bootstrap.hcl); it does NOT create the provider.
#
# FIRST-DEPLOY ORDERING (docs/bootstrap-ordering.md):
#   1. Operator creates the GitHub OIDC provider in prod (pre-req)
#   2. Apply state-bootstrap (local backend first, then migrate state)
#   3. Apply THIS unit (oidc-bootstrap)
#   4. Publish role ARNs and artifact_bucket_name to repo variables
#
# The dependency on state-bootstrap is DOCUMENTED ORDERING ONLY -- no Terraform
# output edge exists between oidc-bootstrap and state-bootstrap (AC-4, spec AC-19).
#
# ENVCOMMON INCLUDE (iac/02 section 1.1 line 139):
# The leaf terragrunt.hcl uses `include "envcommon"` pointing at
# terragrunt/_envcommon/oidc-bootstrap.hcl, which provides the shared
# roles map (from common/oidc-roles.json) and github_oidc_provider_arn
# (from the derived basename account id). The leaf carries its own leaf inputs
# on top of the envcommon template (spec AC-14, AC-2).

locals {
  # service resolves to the directory basename, i.e. "oidc-bootstrap".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
