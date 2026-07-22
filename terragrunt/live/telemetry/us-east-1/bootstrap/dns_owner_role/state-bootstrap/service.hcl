# live/telemetry/us-east-1/bootstrap/dns_owner_role/state-bootstrap/service.hcl
#
# Service layer for the root state-bootstrap unit.
# Basename resolves to "state-bootstrap" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit creates the remote-state foundation for the root DNS-owner account:
#   - state CMK (KMS customer managed key, alias/444444444444-tfstate)
#   - access-log bucket (444444444444-tfstate-access-logs)
#   - DynamoDB lock table with PITR enabled
#   - artifact bucket / S3 state bucket (444444444444-tfstate)
#   - artifact bucket exported as artifact_bucket_name output (D48)
#
# The artifact_bucket_name output is published to the PORTAL_ARTIFACT_BUCKET repo
# Actions variable after this unit is applied (docs/bootstrap-ordering.md, AC-7).
#
# Applied once with root admin credentials. The own-state local-backend override
# (generate "backend" with contents = "") in the leaf terragrunt.hcl ensures
# the first apply runs against a local backend, because the hardened S3 backend it
# provisions does not yet exist (D40, spec 02 Section 4.6).
# After the first apply, the operator runs `terraform init -migrate-state` to move
# local state into the hardened S3 backend.
#
# Module source: providers/aws/references/state-bootstrap (spec 02 Section 3.5).
#
# FIRST-DEPLOY PREREQUISITE: this unit must be applied before oidc-bootstrap, and
# after the operator creates the GitHub OIDC provider in the prod account.
# See docs/bootstrap-ordering.md for the full ordered sequence.
#
# No _envcommon include is used at this service layer. All unit-specific inputs
# (lock-table schema, backend override) live in the leaf terragrunt.hcl inline (D46).

locals {
  # service resolves to the directory basename, i.e. "state-bootstrap".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
