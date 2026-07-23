# live/telemetry/us-east-1/<env>/000/environment_instance.hcl
#
# Sandbox environment instance layer for the resolved service account.
# Basename resolves to "000" per the canonical layer idiom (spec 02 Section 1.3).
# This layer sits between the sandbox environment and the per-unit service directories.
#
# Instance numbering convention:
#   "000" is the first (and only) instance of the sandbox environment in this account.
#   Additional environment instances (e.g. "001") would represent isolated parallel
#   sandbox runs. The "000" sentinel keeps the namespace consistent with the service
#   and service_instance layers below it (spec 02 Section 1.3).
#
# The environment_instance local is consumed by the root terragrunt.hcl remote_state
# key derivation. The enable_custom_domain toggle is resolved by the root terragrunt.hcl
# from common/domains.json keyed by env-class (false for "sandbox", true for "prod");
# it is NOT read from account.hcl (account.hcl exposes only aws_account_id per D2).
# Service units inherit enable_custom_domain via include.root.locals (D31).
#
# Integration-validation evidence contract (AC-19):
#   This layer records the three integration-validation evidence pillars required
#   before any prod deploy may begin (D30/D32):
#
#   apply_result          -- set to "" until a real make tg-apply succeeds
#   smoke_outcome         -- set to "" until the OTLP->Firehose->S3/Glue/Athena->
#                            QuickSight smoke is verified
#   destroy_confirmation  -- set to "" until a real make tg-destroy leaves zero
#                            orphaned resources
#
#   All three are empty-string sentinels at the baseline (no apply recorded).
#   After a successful apply/smoke/destroy cycle the operator records the run
#   datetime in ISO-8601 UTC into each field via a direct commit. The validation
#   tooling (make tg-validate) treats any non-empty value as evidence received.
#   Hardcoded literal results ("success", "pass", "clean") are forbidden (D31);
#   only ISO-8601 UTC timestamps are accepted as recorded evidence.
#
#   The evidence block is machine-readable so the apply/destroy cycle is auditable
#   against this file in version history.
#
# AC-18 parity contract:
#   The sandbox subtree (<service-account>/sandbox/000) is reconciled against the UNION
#   of both prod subtrees:
#     prod service subtree:  <service-account>/prod/000
#     DNS-owner subtree:     <dns-owner-account>/prod/000
#   Parity holds when every sandbox unit name present in the prod union pins the
#   IDENTICAL ?ref= module-source pin, and no sandbox unit sources a module/version
#   absent from the prod union.
#   Allowed delta (prod-only by design, DNS-owner-account-homed):
#     dns-delegation, dns-collector-pretty, dns-portal-pretty
#   These three units are NOT required to have a sandbox counterpart.
#   Only permitted differences between sandbox and prod:
#     - account-id basename (account_vars / aws_account_id per D2)
#     - environment basename
#     - per-environment inputs (enable_custom_domain from common/domains.json, sizes,
#       domains per D31/D48)
#     - the three DNS-owner delta units above
#   Any SHARED unit with a divergent ?ref= pin or any sandbox unit whose
#   module/version is absent from the prod union fails the parity check.
#
# Real-AWS only (D30): no LocalStack anywhere in this subtree.
# Local apply only (D33): cycle runs under AWS_PROFILE=sandbox; no GitHub Actions
# workflow and no OIDC role are used for the sandbox integration gate.

locals {
  # The 4th namespace field (environment_instance) is the dir basename VERBATIM.
  # It has three mutually-exclusive sub-classes (root.hcl tier local classifies them):
  #   - numeric set ("000", "001", ...)        = a per-set serving instance (this 000/ dir)
  #   - bare tier word (shared|dns_owner|pretty) = a once-per-env singleton tier (_singletons/)
  #   - "*_role" (sandbox_role|...|dns_owner_role) = a per-account bootstrap role (bootstrap/)
  # The basename IS the final token after the folder renames (-/_ field rule), so no
  # trimprefix/replace is needed -- it is read uniformly as the basename across all sub-classes.
  # Read by the root terragrunt.hcl to construct the state-backend key prefix + per-field tags.
  environment_instance = basename(get_terragrunt_dir())

  # account_vars provides aws_account_id (D2 wrong-account guard). It is also
  # referenced in the AC-18 parity contract header above to make the account-id
  # sourcing mechanism explicit. enable_custom_domain is NOT sourced here; it is
  # resolved by the root terragrunt.hcl from common/domains.json keyed by env-class
  # (false for "sandbox", true for "prod") and exposed via include.root.locals (D31).
  account_vars = read_terragrunt_config(find_in_parent_folders("account.hcl"))

  # Integration-validation evidence contract (AC-19 / D30 / D32 / D33)
  #
  # All three fields are empty-string sentinels at the baseline (no apply yet).
  # After a successful apply/smoke/destroy cycle the operator records the ISO-8601
  # UTC run datetime into each field by committing this file. Hardcoded result
  # literals ("success", "pass", "clean") are forbidden (D31) -- only ISO-8601
  # UTC timestamps are accepted as recorded evidence values.
  #
  # apply_result: ISO-8601 UTC timestamp of a successful make tg-apply against
  #   <service-account> with no LocalStack fallback (D30). Empty until first real apply.
  apply_result = ""

  # smoke_outcome: ISO-8601 UTC timestamp of a successful OTLP -> Firehose -> S3 ->
  #   Glue -> Athena -> QuickSight portal smoke run. Empty until first real smoke.
  smoke_outcome = ""

  # destroy_confirmation: ISO-8601 UTC timestamp of a successful make tg-destroy that
  #   left zero orphaned resources across all exercised services. Empty until first
  #   real destroy cycle.
  destroy_confirmation = ""
}
