# live/telemetry/us-east-1/sandbox/000/identity/service.hcl
#
# Service layer for the sandbox identity unit.
# Basename resolves to "identity" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit maps IAM Identity Center groups (Viewer, Author, Admin) to the
# QuickSight, Athena, and portal access roles in the sandbox account via a single
# call to the references/identity reference module (AC-3). The reference module is
# the canonical composition point; no primitive is sourced directly from this unit.
#
# Module source: providers/aws/references/identity (spec 02 Section 3.5).
# Pre-tag local source is used during initial rollout; replace with an immutable
# ?ref=<semver> tag once the module is published and promoted to stable.
#
# Dependencies:
#   - references/identity module (E1-F7-S2-T2) must be present in the repo
#     at providers/aws/references/identity before this unit can be applied.
#   - state-bootstrap unit (E4-F1-S1-T1) must have been applied so the remote state
#     backend and lock table exist (D40).
#   - _envcommon/identity.hcl must define the Viewer/Author/Admin group names and
#     QuickSight role names so the leaf inputs carry no inline literals (D8/D37).
#
# The _envcommon/identity.hcl shared template is included by the leaf terragrunt.hcl
# (not here at the service layer). No _envcommon include is used at this layer.

locals {
  # service resolves to the directory basename, i.e. "identity".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
