# live/telemetry/us-east-1/bootstrap/sandbox_role/state-bootstrap/service.hcl
#
# Service layer for the sandbox state-bootstrap unit.
# Basename resolves to "state-bootstrap" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit creates the remote-state foundation for the sandbox account:
#   - state CMK (KMS customer managed key, alias/${account_id}-tfstate)
#   - access-log bucket (${account_id}-tfstate-access-logs)
#   - DynamoDB lock table with PITR enabled
#   - artifact bucket (S3 state bucket, ${account_id}-tfstate)
#
# Applied once with AWS_PROFILE=sandbox admin credentials. The own-state local-backend
# override (generate "backend" with contents = "") in the leaf terragrunt.hcl ensures
# the first apply runs against a local backend, because the hardened S3 backend it
# provisions does not yet exist (D40, spec 02 Section 4.6).
# After the first apply, the operator runs `terraform init -migrate-state` to move
# local state into the hardened S3 backend.
#
# Module source: providers/aws/references/state-bootstrap (spec 02 Section 3.5).
# Pre-tag local source is used during initial rollout; replace with an immutable
# ?ref=<semver> tag once the module is published and promoted to stable.
#
# Dependencies:
#   - references/state-bootstrap module (E1-F8-S1-T1) must be present in the repo
#     at providers/aws/references/state-bootstrap before this unit can be applied.
#   - account.hcl (E4-F1-S1-T2) must declare aws_account_id matching this account so the
#     root-generated provider allowed_account_ids guard triggers on wrong-profile
#     applies (D4).
#
# No _envcommon include is used at this service layer. All unit-specific inputs
# (lock-table schema, backend override) live in the leaf terragrunt.hcl inline (D46).

locals {
  # service resolves to the directory basename, i.e. "state-bootstrap".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
