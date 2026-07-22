# terragrunt/_envcommon/identity.hcl
#
# Shared input template for the identity service unit.
# Included by every sandbox and prod identity leaf via:
#   include "envcommon" {
#     path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/identity.hcl"
#     expose         = true
#     merge_strategy = "deep"
#   }
#
# Ledger decisions applied here:
#   D8  - viewer/author/admin group names for IAM Identity Center (not hardcoded prod literals)
#   D31 - sandbox and prod differ only by folder path plus inputs
#   D37 - inputs map to declared variables on references/identity
#   D45 - per-env account id and naming derived from account.hcl (not hardcoded)
#   D47 - per-env domain values sourced from account.hcl, not hardcoded prod literals

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

  # account.hcl carries aws_account_id and deploy_role_arn (D45/D47).
  account_vars   = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  aws_account_id = local.account_vars.locals.aws_account_id

  # use_pinned_module_sources: read from account.hcl via a direct map index (no default,
  # no fallback) so a missing key aborts parse immediately (spec Section 4.3, AC-6).
  use_pinned_module_sources = local.account_vars.locals.use_pinned_module_sources
}

inputs = {
  # D8: IAM Identity Center group names (org-standard; same across envs).
  viewer_group_name = "telemetry-viewer"
  author_group_name = "telemetry-author"
  admin_group_name  = "telemetry-admin"

  # QuickSight permission-set role names (64-char limit, namespace-agnostic).
  analyst_role_name = "TelemetryAnalyst"
  admin_role_name   = "TelemetryAdmin"

  # Permission-set account id: sourced from account.hcl (D45/D47, not a hardcoded prod literal).
  permission_set_account_id = local.aws_account_id

  # ECS task and execution role names: namespace-derived for uniqueness.
  ecs_task_role_name           = "${local.namespace}-ecs-task"
  ecs_task_execution_role_name = "${local.namespace}-ecs-execution"
}
