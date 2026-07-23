# live/telemetry/us-east-1/sandbox/000/acm-collector/service.hcl
#
# Service layer for the sandbox acm-collector unit.
# Basename resolves to "acm-collector" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit requests the TLS certificate for the collector endpoint. It sources
# the primitives/acm-certificate primitive module (AC-3) with wait_for_validation=false
# so the request returns immediately without blocking on DNS validation (D24). The
# certificate carries the mandatory pretty SAN (D19) alongside the per-service domain.
# Validation is created downstream in a separate unit to avoid the dependency cycle
# described in D24 and D26.
#
# Module source: providers/aws/primitives/acm-certificate (spec 02 Section 3.5).
# Pre-tag local source is used during initial rollout; replace with an immutable
# ?ref=<semver> tag once the module is published and promoted to stable.
#
# Dependencies:
#   - primitives/acm-certificate module (E1-F3-S1-T1) must be present in the repo
#     at providers/aws/primitives/acm-certificate before this unit can be applied.
#   - state-bootstrap unit (E4-F1-S1-T1) must have been applied so the remote state
#     backend and lock table exist (D40).
#   - dns_service_apex and dns_pretty_apex resolve from terragrunt/common/domains.json
#     via root terragrunt.hcl local.domain_cfg; the _envcommon/acm-collector.hcl template
#     resolves the apexes via local.root.locals.dns_service_apex /
#     local.root.locals.dns_pretty_apex (root loaded with
#     read_terragrunt_config(find_in_parent_folders("root.hcl"))).
#     account.hcl supplies only aws_account_id (D45/D47).
#
# The _envcommon/acm-collector.hcl shared template is included by the leaf terragrunt.hcl
# (not here at the service layer). No _envcommon include is used at this layer.

locals {
  # service resolves to the directory basename, i.e. "acm-collector".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
