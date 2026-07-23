# live/telemetry/us-east-1/bootstrap/qa_role/oidc-bootstrap/service.hcl
#
# Service layer for the QA oidc-bootstrap unit.
# Basename resolves to "oidc-bootstrap" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit creates exactly the telemetry-platform-gha-terratest IAM role in the QA
# account so GitHub Actions can assume it via OIDC to run terratest. The QA account
# has no service tree (D30/D40) -- this bootstrap unit is the entire QA footprint.
#
# The GitHub OIDC provider is an operator prerequisite (D40): it must pre-exist in the
# QA account before this unit is applied. The unit consumes (does not create) the provider
# via the github_oidc_provider_arn input. A missing provider ARN fails fast because the
# oidc-bootstrap module validates the ARN format in variables.tf.
#
# Applied once with QA admin credentials.
# spec 02 Section 4.6, ledger D40, D48.

locals {
  service = basename(get_terragrunt_dir())
}
