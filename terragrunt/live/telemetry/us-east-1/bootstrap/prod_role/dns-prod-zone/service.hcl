# live/telemetry/us-east-1/bootstrap/<role>/dns-prod-zone/service.hcl
#
# Service layer for the foundation-tier dns-prod-zone unit.
# Basename resolves to "dns-prod-zone" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit creates the per-env delegated public Route53 hosted zone, the platform
# "telemetry-config" KMS customer-managed key, and the SSM parameter seed via a single
# call to the references/dns-prod-zone reference module (AC-3). The reference module is
# the canonical composition point; no primitive is sourced directly from this unit.
#
# Module source: providers/aws/references/dns-prod-zone (in-repo, bootstrap convention D-16).
#
# Dependencies:
#   - references/dns-prod-zone module must be present in the repo at
#     providers/aws/references/dns-prod-zone before this unit can be applied.
#   - the role's state-bootstrap unit must have been applied so the remote state backend
#     and S3-native lock exist (D40).
#   - bootstrap/<role>/account.hcl resolves the account/profile from common/env_accounts.json;
#     _envcommon/dns-prod-zone.hcl derives the zone domain from common/domains.json keyed by
#     the env-class recovered from the role basename (D45/D47).
#
# The _envcommon/dns-prod-zone.hcl shared template is included by the leaf terragrunt.hcl
# (not here at the service layer). No _envcommon include is used at this layer.

locals {
  # service resolves to the directory basename, i.e. "dns-prod-zone".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
