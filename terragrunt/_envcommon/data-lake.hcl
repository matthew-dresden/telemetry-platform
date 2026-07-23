# terragrunt/_envcommon/data-lake.hcl
#
# Shared input template for the data-lake service unit.
# Included by every sandbox and prod data-lake leaf via:
#   include "envcommon" {
#     path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/data-lake.hcl"
#     expose         = true
#     merge_strategy = "deep"
#   }
#
# Ledger decisions applied here:
#   D31 - sandbox and prod differ only by folder path plus inputs
#   D37 - inputs map to declared variables on references/data-lake
#   D45 - identity and naming values derived from the namespace local (not hardcoded)

locals {
  # Namespace is derived here from the hierarchy layer files directly (D1, D36),
  # mirroring the root.hcl derivation. The hierarchy files are read in this
  # _envcommon's LEAF evaluation context via find_in_parent_folders (ancestors)
  # plus a leaf-relative read of service_instance.hcl (D36). root.hcl itself is
  # NOT read here: read_terragrunt_config(find_in_parent_folders("root.hcl"))
  # evaluates root.hcl's own find_in_parent_folders hierarchy reads relative to
  # root.hcl's directory (terragrunt/), where they cannot resolve, so reading root
  # standalone aborts parse. Deriving from the leaf-resolvable layer files keeps the
  # copy-any-level property intact (spec section 4.8, D1, D36).
  product_vars              = read_terragrunt_config(find_in_parent_folders("product.hcl"))
  region_vars               = read_terragrunt_config(find_in_parent_folders("region.hcl"))
  environment_vars          = read_terragrunt_config(find_in_parent_folders("environment.hcl"))
  environment_instance_vars = read_terragrunt_config(find_in_parent_folders("environment_instance.hcl"))
  service_vars              = read_terragrunt_config(find_in_parent_folders("service.hcl"))
  service_instance_vars     = read_terragrunt_config("${get_terragrunt_dir()}/service_instance.hcl")

  region_clean = replace(local.region_vars.locals.aws_region, "-", "")
  # Namespace field rule (mirrors root.hcl): "-" SEPARATES the 6 fields, "_" JOINS words
  # WITHIN a field. The per-field replace below only ever rewrites a multi-word service/tier
  # token (collector-ingestion -> collector_ingestion); region_clean already has no "-".
  # local.namespace is the CANONICAL "_"-in-field form (tags, networks.json key, "_"-legal
  # names); local.namespace_dns is the FLATTENED "_"->"-" form for "_"-hostile kinds (S3, DNS).
  namespace = join("-", [
    for f in [
      local.product_vars.locals.product,
      local.region_clean,
      local.environment_vars.locals.environment,
      local.environment_instance_vars.locals.environment_instance,
      local.service_vars.locals.service,
      local.service_instance_vars.locals.service_instance,
    ] : replace(f, "-", "_")
  ])
  namespace_dns = replace(local.namespace, "_", "-")

  # use_pinned_module_sources: read from account.hcl via a direct map index (no default,
  # no fallback) so a missing key aborts parse immediately (spec Section 4.3, AC-6).
  account_vars              = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  use_pinned_module_sources = local.account_vars.locals.use_pinned_module_sources

  # Tool-registry-driven ingestion (shared across envs, D31): how the cwl_split transform
  # maps a structured record's OTLP service.name to a lake `tool` partition value, and the
  # governed enum of `tool` partition values itself. Single source of truth in
  # common/tool-registry.json (paired with the collector-ingestion
  # structured_otlp_service_names derived from the SAME file); fails fast if the file or
  # its "tools" key is absent. Onboarding a new tool is a single edit to the registry --
  # see docs/onboarding-a-tool.md.
  registry = jsondecode(file("${get_repo_root()}/terragrunt/common/tool-registry.json"))
  tools    = lookup(local.registry, "tools", null) != null ? local.registry["tools"] : tobool("ERROR: key 'tools' not found in common/tool-registry.json -- add the tool registry entries before deploying.")

  # Derived: service.name -> tool map for every structured-otlp/both entry (spec:
  # service->tool map). Entries with ingestion="body-contract" carry no service_names key,
  # so lookup(...,[]) makes them inert here (contribute no map keys).
  service_tool_map = merge([for t in local.tools : { for sn in lookup(t, "service_names", []) : sn => t.tool }]...)

  # Derived: the full governed set of `tool` partition values (spec: enum tool values),
  # deduplicated. Every registered tool -- body-contract, structured-otlp, or both --
  # contributes its `tool` value to the Glue enum.
  tool_values = distinct([for t in local.tools : t.tool])
}

terraform {
  # Before `terragrunt destroy`, empty the data-lake S3 bucket so Terraform can delete it
  # even when it holds objects/versions (telemetry the Firehose has delivered). This keeps
  # the whole env stack destroy/recreate-able from terragrunt alone with NO manual S3 empty
  # step, independent of the module's force_destroy -- so it works identically for local
  # module sources (sandbox) and pinned immutable module sources (prod, where the published
  # module predates force_destroy passthrough). The empty logic lives in a common shell
  # script (terragrunt/common/scripts/empty-s3-bucket.sh), NOT inline HCL, and this shared
  # template invokes it identically for every env (D31). The named AWS profile from
  # account.hcl scopes the deletion to this unit's account; the bucket name is the
  # env-derived namespace (same value the inputs below pass to the module).
  before_hook "empty_data_lake_bucket_before_destroy" {
    commands     = ["destroy"]
    execute      = ["bash", "${get_repo_root()}/terragrunt/common/scripts/empty-s3-bucket.sh", "${local.namespace_dns}-data-lake", local.account_vars.locals.aws_profile]
    run_on_error = false
  }
}

inputs = {
  # S3 data lake bucket name derived from the namespace (globally unique, D10).
  # S3 is "_"-hostile -> use the flattened namespace_dns form.
  bucket_name = "${local.namespace_dns}-data-lake"

  # Allow `terragrunt destroy` to tear the data lake bucket down even when it holds
  # objects/versions, so the entire env stack is destroy/recreate-able from terragrunt
  # alone with no manual S3 empty step. Same value across envs (D31), so set here.
  force_destroy = true

  # Glue catalog database name: namespace-scoped, lowercase with underscores.
  glue_database_name = replace("${local.namespace}_telemetry", "-", "_")

  # Glue table name for Firehose format conversion (B4).
  glue_table_name = replace("${local.namespace}_events", "-", "_")

  # Tool-registry-driven ingestion: service.name -> tool map for the cwl_split reshape
  # (registry-derived from common/tool-registry.json; same value both envs, D31). The
  # reserved synthetic e2e marker maps to e2e-smoke so gate traffic stays siftable and
  # auto-cleaned.
  service_tool_map = local.service_tool_map

  # Registry-driven Glue `tool` partition enum (projection.tool.values): every registered
  # tool's `tool` value, so the enum stays in lockstep with the registry (no drift, no
  # module-default catch-all). Without this, the module's minimal default
  # (["e2e-smoke", "example-cli"]) would drop every registered structured-otlp tool value from the enum.
  glue_partition_projection_tool_values = local.tool_values

  # Kinesis Firehose delivery stream name: namespace-scoped.
  firehose_stream_name = "${local.namespace}-telemetry-events"

  # IAM role name for Firehose delivery (64-char limit; namespace provides uniqueness).
  # This single role is also the schema_configuration role for Parquet format
  # conversion: it trusts firehose.amazonaws.com and holds glue:GetTable* on the
  # format-conversion table, so no separate Glue conversion role is created.
  firehose_role_name = "${local.namespace}-firehose-delivery"

  # Lifecycle: transition raw events to GLACIER after 90 days.
  transition_storage_class = "GLACIER"
  transition_days          = 90
}
