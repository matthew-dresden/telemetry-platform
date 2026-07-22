# terragrunt/_envcommon/collector-ingestion.hcl
#
# Shared input template for the collector-ingestion service unit.
# Included by every sandbox and prod collector-ingestion leaf via:
#   include "envcommon" {
#     path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/collector-ingestion.hcl"
#     expose         = true
#     merge_strategy = "deep"
#   }
#
# Ledger decisions applied here:
#   D31 - sandbox and prod differ only by folder path plus inputs (not by duplicated blocks)
#   D37 - canonical input names: collector_service_fqdn, collector_pretty_fqdn
#   D44 - concrete, input-driven abuse-limit defaults (no placeholders):
#           ADOT OTLP receiver max_request_body_size = 4194304 bytes
#           WAF rate_limit_per_ip = 2000 requests per 5-minute window
#   D45 - per-env domain apexes from common/domains.json (keyed by env-class), not hardcoded
#   D47 - domain FQDNs composed from per-env apexes (dns_service_apex, dns_pretty_apex)

locals {
  # Namespace is derived here from the hierarchy layer files directly (D1, D36),
  # mirroring the root.hcl derivation. root.hcl itself is NOT read via
  # read_terragrunt_config(find_in_parent_folders("root.hcl")) because root.hcl's own
  # find_in_parent_folders hierarchy reads resolve relative to root.hcl's directory
  # (terragrunt/) and abort parse. Deriving from the leaf-resolvable layer files keeps
  # the copy-any-level property intact (spec section 4.8, D1, D36).
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

  # D45/D47: per-env domain apexes and enable_custom_domain resolved from
  # common/domains.json keyed by env-class (the same source root.hcl uses), anchored on
  # get_repo_root() (D3). The lookup fails fast (no fallback) if the env-class key is
  # absent (spec S3.5, S7). The same template resolves to:
  # prod -> collector.prod.telemetry.example.com (service)
  # prod -> collector.telemetry.example.com (pretty)
  # sandbox (enable_custom_domain=true) -> collector.sandbox.telemetry.example.com
  # by these inputs alone (D31).
  environment_name     = local.environment_vars.locals.environment
  domains              = jsondecode(file("${get_repo_root()}/terragrunt/common/domains.json"))
  domain_cfg           = lookup(local.domains, local.environment_name, null) != null ? local.domains[local.environment_name] : tobool("ERROR: env-class '${local.environment_name}' not found in common/domains.json -- add a row for this env-class before deploying.")
  service_apex         = local.domain_cfg["dns_service_apex"]
  pretty_apex          = local.domain_cfg["dns_pretty_apex"]
  enable_custom_domain = local.domain_cfg["enable_custom_domain"]

  # VPC CIDR sourced from common/networks.json keyed by the full derived namespace
  # (spec S4.1, S3.6): this is a VPC-creating unit, so it keys the networks map. The
  # map is anchored on get_repo_root() (D3) and the lookup fails fast (no fallback) if
  # the namespace has no CIDR row (spec S3.5, S7).
  networks       = jsondecode(file("${get_repo_root()}/terragrunt/common/networks.json"))
  network_cfg    = lookup(local.networks, local.namespace, null) != null ? local.networks[local.namespace] : tobool("ERROR: namespace '${local.namespace}' not found in common/networks.json -- add a CIDR row for this VPC-creating unit's namespace before deploying.")
  vpc_cidr_block = local.network_cfg["vpc_cidr_block"]

  # CloudFront origin-facing managed prefix list id sourced from common/cloudfront.json
  # keyed by AWS region (the same get_repo_root() anchored, fail-fast read pattern used for
  # domains.json/networks.json above, D3). This is the AWS-managed prefix list
  # (com.amazonaws.global.cloudfront.origin-facing) allowed inbound on the internal ALB SG so
  # CloudFront can reach the ALB via the VPC origin (collector-ingestion v1.0.4 REQUIRED input).
  # The value is region-specific and AWS-managed (us-east-1 -> pl-3b927c52); supplying it as an
  # input keeps the module free of any plan-time AWS read. Keyed by region (not env) so sandbox
  # and prod resolve the SAME id for the same region -> identical inputs across envs (D31). The
  # lookup fails fast (no fallback) if the region key is absent (spec S3.5, S7).
  aws_region                              = local.region_vars.locals.aws_region
  cloudfront_prefix_lists                 = jsondecode(file("${get_repo_root()}/terragrunt/common/cloudfront.json"))
  cloudfront_origin_facing_prefix_list_id = lookup(local.cloudfront_prefix_lists, local.aws_region, null) != null ? local.cloudfront_prefix_lists[local.aws_region] : tobool("ERROR: region '${local.aws_region}' not found in common/cloudfront.json -- add a row mapping this region to its CloudFront origin-facing managed prefix list id before deploying.")

  # Tool-registry-driven structured-OTLP routing (shared across envs, D31): the OTLP
  # service.name values the collector routes to its raw_log=false structured pipeline so
  # log-record attributes (marketplaces/plugins/skills/MCPs usage, and any other
  # structured-OTLP tool's attributes) survive for the data-lake cwl_split reshape. Single
  # source of truth in common/tool-registry.json (paired with the data-lake
  # service_tool_map/glue_partition_projection_tool_values derived from the SAME file);
  # fails fast if the file or its "tools" key is absent. Onboarding a new tool is a single
  # edit to the registry -- see docs/onboarding-a-tool.md.
  registry = jsondecode(file("${get_repo_root()}/terragrunt/common/tool-registry.json"))
  tools    = lookup(local.registry, "tools", null) != null ? local.registry["tools"] : tobool("ERROR: key 'tools' not found in common/tool-registry.json -- add the tool registry entries before deploying.")

  # Derived: every service_names value across every registry entry, flattened and
  # deduplicated (spec: structured service names). Entries with ingestion="body-contract"
  # carry no service_names key, so lookup(...,[]) makes them inert here.
  structured_otlp_service_names = distinct(flatten([for t in local.tools : lookup(t, "service_names", [])]))

  # account.hcl carries the source toggle (D45/D47).
  account_vars = read_terragrunt_config(find_in_parent_folders("account.hcl"))

  # use_pinned_module_sources: read from account.hcl via a direct map index (no default,
  # no fallback) so a missing key aborts parse immediately (spec Section 4.3, AC-6).
  use_pinned_module_sources = local.account_vars.locals.use_pinned_module_sources
}

inputs = {
  # D37: canonical input names -- composed from the per-env apex (D45/D47).
  collector_service_fqdn = "collector-${local.environment_instance_vars.locals.environment_instance}.${local.service_apex}"
  collector_pretty_fqdn  = "collector.${local.pretty_apex}"

  # D31: enable_custom_domain controls whether ACM + DNS resources are created.
  # prod: true (domains.json); sandbox: false by default.
  enable_custom_domain = local.enable_custom_domain

  # Single-owner DNS (AC-8 / D39): in the LIVE tree the dedicated dns-collector unit
  # is the SOLE owner of the public collector hostname record (R4), which it writes as
  # an A ALIAS to the collector CloudFront distribution. The collector-ingestion
  # reference must NOT also create a CNAME for the same name, or apply fails with
  # "conflicting RRSet of type CNAME with the same DNS name already exists". Setting
  # create_public_dns_record = false here (shared by every sandbox and prod
  # collector-ingestion leaf via this _envcommon include, so sandbox and prod stay at
  # parity, D31) drops the reference's public route53_record. The certificate
  # validation waiter is unaffected (it creates no DNS record) so the collector still
  # consumes an ISSUED cert. The standalone example/terratest fixture does NOT include
  # this template, so its default (true) is unchanged and it remains self-contained.
  create_public_dns_record = false

  # SSM parameter names only (non-secret strings derived from namespace). The module
  # reads values via aws_ssm_parameter data sources at apply time (Section 3.2).
  waf_allow_cidrs_ssm_param_name = "/${local.namespace}/ingestion/waf/allow-cidrs"
  adot_config_ssm_param_name     = "/${local.namespace}/ingestion/adot/collector-config"

  # D44: concrete, input-driven abuse-limit defaults.
  # ADOT OTLP HTTP receiver: maximum request body size in bytes.
  # Sized from the event-volume baseline (~250 usage events / active client / day, D27.2).
  max_request_body_size = 4194304

  # D44: WAF rate-based rule: maximum requests per source IP per 5-minute window.
  # Concrete default per D44; public ingest endpoint uses rate-based + managed rules (D5).
  rate_limit_per_ip = 2000

  # Structured-OTLP telemetry routing: service names routed to the raw_log=false structured
  # pipeline (registry-derived from common/tool-registry.json; same value both envs, D31).
  # Every other service (example-cli and any flat-body top-level-`tool` contract tool) stays on
  # the existing raw_log=true path byte-unchanged.
  structured_otlp_service_names = local.structured_otlp_service_names

  # ADOT task SG egress: widen to 0.0.0.0/0 so the Fargate task (in a private subnet) can
  # pull the public ADOT collector image (public.ecr.aws/aws-observability/aws-otel-collector)
  # over NAT. The module's secure default restricts egress to the VPC CIDR (in-VPC endpoints
  # only), but public.ecr.aws is NOT served by the private-registry ECR interface endpoints,
  # so the image manifest pull times out (CannotPullContainerError) without internet egress.
  # This is exactly the case the module documents for widening adot_egress_cidr_blocks
  # (variables.tf:232). Egress-only (no ingress widening), behind NAT, in a private subnet;
  # set here so sandbox and prod stay identical (D31).
  adot_egress_cidr_blocks = ["0.0.0.0/0"]

  # VPC networking input passed to the vpc-network reference (REUSED vpc + NEW-LOCAL
  # primitives, D6). The CIDR block is per-deployment and sourced from common/networks.json
  # keyed by the derived namespace so sandbox and prod use different network address
  # spaces without template changes (D31/D47, spec S4.1).
  vpc_cidr_block = local.vpc_cidr_block

  # CloudFront origin-facing managed prefix list id (collector-ingestion v1.0.4 REQUIRED
  # input). Resolved from common/cloudfront.json keyed by region (above) so the value is
  # region-derived, not hardcoded in the leaf, and identical across sandbox and prod for the
  # same region (D31). Allowed inbound on the internal ALB SG (HTTPS listener port) so
  # CloudFront reaches the internal ALB through the VPC origin; the module performs no
  # plan-time AWS read. alb_https_listener_port is left at the module default (443).
  cloudfront_origin_facing_prefix_list_id = local.cloudfront_origin_facing_prefix_list_id
}
