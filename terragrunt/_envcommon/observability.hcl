# terragrunt/_envcommon/observability.hcl
#
# Shared input template for the observability service unit.
# Included by every sandbox and prod observability leaf via:
#   include "envcommon" {
#     path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/observability.hcl"
#     expose         = true
#     merge_strategy = "deep"
#   }
#
# Ledger decisions applied here:
#   D31 - sandbox and prod differ only by folder path plus inputs
#   D37 - inputs map to declared variables on references/observability
#   D41 - single SNS topic as the notification sink for alarms and cost-anomaly
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
}

inputs = {
  # D41: SNS topic name: namespace-scoped, single notification sink.
  topic_name = "${local.namespace}-observability"

  # CloudWatch dashboard name: namespace-scoped.
  dashboard_name = "${local.namespace}-telemetry"

  # Cost anomaly monitor: namespace-scoped display names.
  anomaly_monitor_name      = "${local.namespace}-cost-anomaly-monitor"
  anomaly_subscription_name = "${local.namespace}-cost-anomaly-alerts"
  # IMMEDIATE (not DAILY/WEEKLY): the cost-anomaly subscriber is the observability SNS
  # topic (D41 single sink). AWS Cost Anomaly Detection only supports SNS subscribers with
  # IMMEDIATE frequency; DAILY/WEEKLY are restricted to EMAIL subscribers.
  anomaly_subscription_frequency = "IMMEDIATE"
}
