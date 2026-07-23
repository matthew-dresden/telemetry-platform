# live/telemetry/us-east-1/<env>/_singletons/shared/athena/service.hcl
#
# Service layer for the shared athena unit.
# Basename resolves to "athena" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit provisions the cost-capped Athena workgroup used to query the telemetry
# data lake (Glue/Athena). It sources the athena-workgroup primitive directly and
# exports the namespace-derived workgroup name (workgroup_name) consumed by the
# observability unit's Athena scanned-bytes CloudWatch alarm dimension.
#
# This unit replaces the retired analytics unit as the owner of the Athena workgroup.
# It keeps ONLY the vendor-neutral, cost-capped workgroup; the QuickSight/SPICE
# consumer surface the analytics reference module carried has been removed.
#
# The service local is consumed by the root terragrunt.hcl remote_state key derivation
# and by the namespace derivation.

locals {
  # service resolves to the directory basename, i.e. "athena".
  service = basename(get_terragrunt_dir())
}
