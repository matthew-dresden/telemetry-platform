# live/telemetry/us-east-1/<env>/000/collector-ingestion/000/terragrunt.hcl
#
# Service account collector-ingestion unit.
# Provisions the full OTLP collector edge-to-service stack: VPC network, internal ALB,
# ADOT ECS Fargate service, WAF WebACL (CLOUDFRONT scope, four managed rule groups),
# CloudFront distribution, and the ACM certificate validation waiter
# (aws_acm_certificate_validation) via the references/collector-ingestion reference module
# (AC-3, E1-F5-S2-T2). Sources exactly ONE module per AC-3: no primitive is sourced
# directly from this leaf.
#
# The collector-ingestion reference is deployed here with create_public_dns_record = false
# (set in _envcommon/collector-ingestion.hcl): the public collector hostname record is NOT
# created by this leaf. The dedicated dns-collector unit is the single owner of that record
# (R4), which it writes as an A ALIAS to the collector CloudFront distribution. A CNAME for
# the same name here would conflict with the A-alias and fail apply (D39, AC-8). This leaf
# creates ONLY the aws_acm_certificate_validation waiter (which creates no DNS record), so
# the collector still resolves an ISSUED certificate.
#
# ACCOUNT GUARD (D2/D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# The account id is resolved from account.hcl. A wrong-profile apply will fail fast because
# the active caller identity will not match the allowed account (D4).
#
# DEPENDENCY WIRING (D37/AC-3):
# - dependency.data_lake: consumes firehose_delivery_stream_arn and lake_kms_key_arn
#   from the sandbox data-lake unit (E6-F2-S1-T1). The firehose ARN is declared as an
#   INPUT per D37 -- never recomputed by this unit (prevents the output-fed-as-input
#   cycle). The lake CMK ARN is passed to waf_log_kms_key_arn per iac/04 I6.
# - dependency.dns_prod_zone: consumes zone_id from the sandbox dns-prod-zone unit
#   (E6-F1-S1-T2). Passed as prod_hosted_zone_id for module interface parity; with
#   create_public_dns_record = false no record is written here -- dns-collector owns R4.
# - dependency.identity: consumes ecs_task_role_arn and ecs_task_execution_role_arn
#   from the sandbox identity unit so the ADOT ECS service can call Firehose and SSM.
# - dependency.acm_collector: consumes certificate_arn and domain_validation_options
#   from the sandbox acm-collector unit (E6-F1-S1-T2). The validation options are
#   passed to the module's aws_acm_certificate_validation resource.
# - dependency.acm_validate_collector: declared so ordering is enforced in the graph --
#   R3 must exist in DNS before aws_acm_certificate_validation polls for ISSUED status.
#
# RECORD OWNERSHIP (AC-8 / D39):
# The public collector hostname record (R4) is written ONLY by the dedicated dns-collector
# unit (as an A ALIAS); this leaf does NOT write it (create_public_dns_record = false in
# _envcommon/collector-ingestion.hcl). The reference's create_public_dns_record toggle
# defaults to true so the standalone example/terratest fixture (which creates its own zone)
# still owns and provisions its public record; only the live leaves set it false. Single
# ownership is enforced by the dependency graph, not timers.
#
# ADOT RECEIVER SHAPING (D5/D44):
# max_request_body_size = 4194304 bytes and memory_limiter are supplied via
# _envcommon/collector-ingestion.hcl (D44 -- concrete defaults, no TODO placeholders).
# Request shaping lives at the ADOT receiver, not WAF (D5).
#
# WAF MANAGED RULE GROUPS (iac/04 I6, section 3.6):
# CommonRuleSet (p10), KnownBadInputs (p20), AmazonIpReputationList (p30), and
# AnonymousIpList (p40) are enabled in the references/collector-ingestion module.
# rate_limit_per_ip = 2000 req/5min (D44 -- concrete, no TODO). logging_enabled=true
# writes to the telemetry-data CMK (iac/04 I6) sourced from the data-lake dependency.
#
# NO-CLIENT-AUTH INGEST POSTURE (iac/04 section 6):
# The OTLP/HTTP ingestion endpoint is intentionally public with NO client credentials.
# There is no client secret, API key, or mTLS on the ingest path. This posture is
# confirmed and intentional per iac/04 section 6.
#
# ADOT CONFIG (D5/D44):
# The ADOT collector config is injected via the AOT_CONFIG_CONTENT SSM SecureString
# parameter (adot_config_ssm_param_name from _envcommon). The config is NEVER baked
# into the container image. max_request_body_size (4194304) and memory_limiter
# processor are set as concrete configured values (D44, D5).
#
# SINGLE MODULE CONTRACT (AC-3):
# This leaf sources ONLY references/collector-ingestion. No primitive is sourced
# directly. Grep the rendered config for a single source = to verify at refactor time.
#
# STATE PREREQUISITE (D40):
# Run tf-state-preflight before plan/apply to confirm the sandbox state backend
# exists in the resolved service account. The root remote_state block will fail closed
# if the bucket does not exist.
#
# Applied with AWS_PROFILE=sandbox credentials.
# spec 02 Section 4.2, ledger D2, D4, D5, D31, D37, D40, D44, D45, D47, AC-3, AC-8, AC-9.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

include "envcommon" {
  path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/collector-ingestion.hcl"
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (spec Section 4.3, AC-7, AC-8):
  # use_pinned_module_sources=false (sandbox): in-repo local source via get_repo_root().
  # use_pinned_module_sources=true (prod): pinned immutable git URL with ?ref= tag.
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/references/collector-ingestion?ref=providers/aws/references/collector-ingestion/v1.4.0" : "${get_repo_root()}//providers/aws/references/collector-ingestion"
}

# ---------------------------------------------------------------------------
# dependencies: upstream unit outputs consumed as inputs (D37/AC-3)
# ---------------------------------------------------------------------------

dependency "data_lake" {
  config_path = "../../../_singletons/shared/data-lake/${local.svc_instance}"

  # mock_outputs allow plan/validate to run before data-lake is applied.
  # mock_outputs_allowed_terraform_commands restricts mocks to plan and validate
  # only -- apply and destroy always use real outputs (fail closed on apply, D31, AC-9).
  # ARNs derived from local.region and local.aws_account_id so a copied leaf presents
  # mocks for its own scope (AC-13).
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    firehose_delivery_stream_arn = "arn:aws:firehose:${local.region}:${local.aws_account_id}:deliverystream/mock-telemetry-events"
    lake_kms_key_arn             = "arn:aws:kms:${local.region}:${local.aws_account_id}:key/mock-lake-kms-key-id"
  }
}

dependency "dns_prod_zone" {
  config_path = "../../../../bootstrap/${local.bootstrap_role}/dns-prod-zone/${local.dns_prod_zone_active}"

  # mock_outputs allow plan/validate to resolve the zone_id offline (D31, AC-8).
  # On apply the real zone_id is used (fail closed, D38).
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    zone_id = "ZMOCKZONEID00000001"
  }
}

dependency "identity" {
  config_path = "../../../_singletons/shared/identity/${local.svc_instance}"

  # mock_outputs allow plan/validate to run before identity is applied.
  # mock_outputs_allowed_terraform_commands restricts mocks to plan and validate only.
  # IAM ARNs derived from local.aws_account_id (IAM is global -- no region) (AC-13).
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    ecs_task_role_arn           = "arn:aws:iam::${local.aws_account_id}:role/mock-telemetry-ecs-task"
    ecs_task_execution_role_arn = "arn:aws:iam::${local.aws_account_id}:role/mock-telemetry-ecs-execution"
  }
}

dependency "acm_collector" {
  config_path = "../../acm-collector/${local.svc_instance}"

  # mock_outputs allow plan/validate to run before acm-collector is applied.
  # On apply the real certificate_arn and domain_validation_options are used.
  # ARNs and FQDNs derived from resolved identity locals (AC-13) so a copied leaf
  # presents mocks for its own scope instead of the sandbox literals.
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    certificate_arn = "arn:aws:acm:${local.region}:${local.aws_account_id}:certificate/mock-collector-cert-id"
    domain_validation_options = toset([
      {
        domain_name           = "collector-${local.environment_instance}.${local.dns_service_apex}"
        resource_record_name  = "_mock-collector-san-cname.${local.dns_service_apex}."
        resource_record_type  = "CNAME"
        resource_record_value = "mock-collector-san-acm-validation.acm-validations.aws."
      }
    ])
  }
}

dependency "acm_validate_collector" {
  config_path = "../../acm-validate-collector/${local.svc_instance}"

  # acm-validate-collector writes R3 into DNS. This dependency enforces graph ordering:
  # aws_acm_certificate_validation must not poll for ISSUED until R3 is in DNS (AC-9).
  # mock_outputs are provided so plan and validate can run before acm-validate-collector
  # is applied (D31). No meaningful outputs are consumed -- the dependency enforces ordering only.
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs                            = {}
}

dependency "cloudfront_logs" {
  config_path = "../../../_singletons/shared/cloudfront-logs/${local.svc_instance}"

  # The cloudfront-logs unit provisions the SSE-S3 (AES256), ACL-enabled destination
  # bucket REQUIRED by the collector-ingestion module's access_log_bucket_name input
  # (CloudFront standard logging, references/collector-ingestion variables.tf:222). The
  # bucket name is consumed as an INPUT, never recomputed (the collector's own namespace
  # differs from the cloudfront-logs namespace, so it cannot self-derive the name). The
  # dependency also enforces apply ordering: the bucket must exist before the CloudFront
  # distribution attaches it as a logging destination.
  # mock_outputs allow plan/validate to run before cloudfront-logs is applied; on apply
  # the real output is used (fail closed, D31, AC-9). The mock is a syntactically valid
  # S3 bucket name (3-63 chars, lowercase, starts/ends alphanumeric) so the module's
  # access_log_bucket_name validation passes offline. The mock is never used on apply
  # (restricted to plan/validate above), so a static placeholder is sufficient and is not
  # an identity literal (no account id / region / env -- exempt mock_outputs block).
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    cloudfront_logs_bucket_name = "mock-cloudfront-logs-bucket"
  }
}

dependency "vpc_flow_logs" {
  config_path = "../../../_singletons/shared/vpc-flow-logs/${local.svc_instance}"

  # The vpc-flow-logs unit provisions the KMS-encrypted CloudWatch log group + IAM delivery
  # role REQUIRED by the collector-ingestion module when enable_flow_logs = true (its
  # secure-by-default; the composed vpc-network primitive's aws_flow_log consumes both by
  # ARN). The ARNs are consumed as INPUTS, never recomputed (D37), and the dependency
  # enforces apply ordering: the role + log group must exist before CreateFlowLogs.
  # mock_outputs allow plan/validate to run before vpc-flow-logs is applied; on apply the
  # real outputs are used (fail closed, D31, AC-9). The mock ARNs are derived from the
  # resolved region/account locals so a copied leaf presents mocks for its own scope (AC-13)
  # and satisfy the module's ^arn:aws:iam: / ^arn:aws: validation offline.
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    flow_logs_iam_role_arn    = "arn:aws:iam::${local.aws_account_id}:role/mock-vpc-flow-logs-role"
    flow_logs_destination_arn = "arn:aws:logs:${local.region}:${local.aws_account_id}:log-group:/vpc/mock/flow-logs:*"
  }
}

# ---------------------------------------------------------------------------
# locals: account identity, namespace, region, trust policies (D31, D37, D45, D47)
# ---------------------------------------------------------------------------

locals {
  # Instance-relative local: resolves to the basename of this service-instance directory
  # (e.g. "000") so that every same-tier sibling dependency config_path interpolates
  # the owning instance index rather than a hardcoded literal (spec section 4.4,
  # AC-FUNC-001, AC-FUNC-002). Terragrunt evaluates locals before dependency blocks,
  # so local.svc_instance is valid inside config_path expressions (spec section 4.4).
  # Copying this folder to index "001" causes svc_instance to resolve to "001",
  # wiring all sibling deps at the new index with zero edits (spec section G5, AC-FUNC-003).
  svc_instance = basename(get_terragrunt_dir())

  # Foundation-tier dns-prod-zone lives at bootstrap/<role>/ (a sibling of the env
  # subtree), OUT of the disposable 000 instance sets. bootstrap_role is derived from
  # environment.hcl (sandbox -> sandbox_role, prod -> prod_role, qa -> qa_role); never
  # hardcoded (D31). The foundation active set is read from its own active.hcl so a copied
  # service instance still resolves the foundation's active set (not the consumer index).
  bootstrap_role       = "${read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment}_role"
  dns_prod_zone_active = read_terragrunt_config("${get_terragrunt_dir()}/../../../../bootstrap/${local.bootstrap_role}/dns-prod-zone/active.hcl").locals.active

  # Account-level locals -- used for ARN construction and trust policies (D2/D45).
  account_vars   = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  aws_account_id = local.account_vars.locals.aws_account_id

  # Region from root -- used for ARN construction (D47).
  region = include.root.locals.region

  # Namespace from root -- used to derive resource names and the networks.json CIDR
  # lookup key (D37/D45). The namespace for this unit is the fully qualified string
  # composed by the root terragrunt.hcl (e.g. "telemetry-useast1-prod-000-collector_ingestion-000";
  # the service field uses "_" within the field per the namespace -/_ rule).
  namespace  = include.root.locals.namespace
  env_active = read_terragrunt_config("${get_terragrunt_dir()}/../../../active.hcl").locals.active
  is_active  = include.root.locals.environment_instance == local.env_active

  # ---------------------------------------------------------------------------
  # VPC CIDR from networks.json (fail-fast on missing namespace key).
  #
  # Both sandbox and prod account.hcl files are basename-only (aws_account_id only)
  # after E8-F2-S1-T1. The _envcommon/collector-ingestion.hcl vpc_cidr_block already
  # resolves from networks.json, but local.account_vars.locals.vpc_cidr_block is a
  # dead path in both environments. This leaf derives the CIDR from this networks.json
  # lookup so the leaf-local subnet layout and the inputs{} override resolve correctly.
  # ---------------------------------------------------------------------------
  networks_map   = jsondecode(file("${get_repo_root()}/terragrunt/common/networks.json"))
  vpc_cidr_block = lookup(local.networks_map, local.namespace, null) != null ? lookup(local.networks_map, local.namespace, {})["vpc_cidr_block"] : tobool("ERROR: namespace '${local.namespace}' not found in terragrunt/common/networks.json -- add a vpc_cidr_block row for this VPC-creating unit before deploying.")

  # Service-layer locals from service.hcl -- trust policies and inline policy (D8).
  # Declared in service.hcl so they are never inline literals in this leaf (AC-13).
  service_vars                          = read_terragrunt_config(find_in_parent_folders("service.hcl"))
  ecs_task_assume_role_policy_json      = local.service_vars.locals.ecs_task_assume_role_policy_json
  ecs_execution_assume_role_policy_json = local.service_vars.locals.ecs_execution_assume_role_policy_json
  adot_task_firehose_policy_json        = local.service_vars.locals.adot_task_firehose_policy_json

  # ---------------------------------------------------------------------------
  # Resource naming: all names derived from the namespace (D31/D37/D45).
  # No literal account ids, env names, or region strings in the name values.
  # ---------------------------------------------------------------------------
  vpc_name     = "${local.namespace}-vpc"
  cluster_name = "${local.namespace}-cluster"
  service_name = "${local.namespace}-adot"
  # ALB names are capped at 32 chars by AWS (alphanumeric + hyphens). The full
  # namespace ("telemetry-useast1-sandbox-000-collector-ingestion-000") is 53
  # chars, so derive a deterministic, collision-safe <=32 name using the repo's
  # hash-suffix scheme (D10/B21): 19-char namespace prefix + 8-char md5 suffix +
  # "-alb" = 32 chars. The hash keeps the name unique per namespace so copied
  # leaves at a new instance index do not collide.
  alb_name       = "${substr(local.namespace, 0, 19)}-${substr(md5(local.namespace), 0, 8)}-alb"
  log_group_name = "/ecs/${local.namespace}-adot"
  task_role_name = "${local.namespace}-adot-task"
  exec_role_name = "${local.namespace}-adot-exec"

  # IAM role CloudWatch Logs assumes to deliver the telemetry ingest group's
  # subscription-filter records to the data-lake Firehose (<=64 char IAM limit;
  # namespace provides uniqueness; "-cwl-fh" suffix keeps it short).
  cwl_to_firehose_role_name = "${local.namespace}-cwl-fh"

  # ---------------------------------------------------------------------------
  # Subnet layout for the VPC (D6/D31/D47).
  # Two AZs, two public + two private subnets. CIDRs derived from local.vpc_cidr_block
  # sourced from networks.json (sandbox CIDR: 10.1.0.0/16, non-overlapping with prod
  # 10.0.0.0/16). If the vpc_cidr_block changes, the subnets automatically use the
  # correct prefix. The AZ suffixes (a/b) are the stable pair for us-east-1.
  # ---------------------------------------------------------------------------
  vpc_cidr_prefix = join(".", slice(split(".", split("/", local.vpc_cidr_block)[0]), 0, 2))

  subnet_layout = [
    {
      name              = "${local.namespace}-public-a"
      cidr_block        = "${local.vpc_cidr_prefix}.0.0/24"
      availability_zone = "${local.region}a"
      public            = true
    },
    {
      name              = "${local.namespace}-public-b"
      cidr_block        = "${local.vpc_cidr_prefix}.1.0/24"
      availability_zone = "${local.region}b"
      public            = true
    },
    {
      name              = "${local.namespace}-private-a"
      cidr_block        = "${local.vpc_cidr_prefix}.10.0/24"
      availability_zone = "${local.region}a"
      public            = false
    },
    {
      name              = "${local.namespace}-private-b"
      cidr_block        = "${local.vpc_cidr_prefix}.11.0/24"
      availability_zone = "${local.region}b"
      public            = false
    },
  ]

  # ---------------------------------------------------------------------------
  # vpc-network composition inputs derived from the spec network topology (D6,
  # iac/04 S1a-S1d/S2). The collector-ingestion reference composes vpc-network,
  # which REQUIRES these inputs (references/collector-ingestion/variables.tf:245,
  # 269, 279, 289). They are NOT stored in any config file: the spec derives them
  # from the VPC topology already declared above (local.subnet_layout) plus the
  # region (D47). Sourcing them here -- rather than hardcoding service strings --
  # keeps every value traceable to local.region and local.subnet_layout, so a CIDR
  # or region change propagates automatically (D31/D47).
  #
  # nat_gateways: one NAT per public subnet for HA (iac/04 S1b "One NAT per AZ").
  # Each references a public subnet by NAME from local.subnet_layout (no literals).
  # ---------------------------------------------------------------------------
  public_subnet_names  = [for s in local.subnet_layout : s.name if s.public]
  private_subnet_names = [for s in local.subnet_layout : s.name if !s.public]

  nat_gateways = [
    for name in local.public_subnet_names : {
      name               = "${name}-nat"
      public_subnet_name = name
      connectivity_type  = "public"
    }
  ]

  # Interface VPC endpoint service names (iac/04 S1d): ECR (api+dkr), SSM,
  # ssmmessages, and CloudWatch Logs interface endpoints in this region. The
  # private OTLP path uses these endpoints so the ADOT tasks pull the image and
  # ship logs without traversing the public internet. Region-interpolated (D47).
  interface_endpoint_service_names = [
    "com.amazonaws.${local.region}.ssm",
    "com.amazonaws.${local.region}.ssmmessages",
    "com.amazonaws.${local.region}.ecr.api",
    "com.amazonaws.${local.region}.ecr.dkr",
    "com.amazonaws.${local.region}.logs",
  ]

  # Interface endpoints attach to the private subnets (iac/04 S1d/S1a: private
  # subnets host ECS tasks + Firehose ENIs). Names sourced from subnet_layout.
  interface_endpoint_subnet_names = local.private_subnet_names

  # S3 gateway endpoint service name for this region (iac/04 S1d: S3 gateway
  # endpoint is the free default). Region-interpolated (D47).
  s3_gateway_endpoint_service_name = "com.amazonaws.${local.region}.s3"

  # ADOT collector image (iac/04 I5): the AWS-published ADOT collector image,
  # pinned tag (input-driven, never baked). Single source of truth referenced by
  # both the adot_image module input and the container definition below (DRY).
  adot_image = "public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1"

  # ---------------------------------------------------------------------------
  # ADOT container definition (D5/D44).
  # The container reads the ADOT config from the AOT_CONFIG_CONTENT SSM SecureString
  # at runtime (adot_config_ssm_param_name from _envcommon). The config is NEVER
  # baked into the image. The image is pinned to the official ADOT collector image.
  #
  # The container name MUST equal local.service_name (the namespace-derived
  # "<namespace>-adot" name): the ecs-app-deploy reference wires the ECS service's
  # load_balancer.container_name to var.service_name (ecs-app-deploy/main.tf), so a
  # divergent container name makes the ALB target reference a container that does not
  # exist in the task definition and CreateService fails with InvalidParameterException
  # ("The container <name> does not exist in the task definition"). Deriving the name
  # from local.service_name keeps the task-definition container and the ALB target
  # aligned (matching the reference's own examples/default fixture).
  # ---------------------------------------------------------------------------
  adot_container_definitions = jsonencode([
    {
      name        = local.service_name
      image       = local.adot_image
      essential   = true
      environment = []
      secrets = [
        {
          name = "AOT_CONFIG_CONTENT"
          # Must match the SSM SecureString the collector-ingestion module creates
          # (local.adot_config_ssm_path = /telemetry/<env>/ingest/adot-config) and the
          # ssm:GetParameters grant on the execution role. env is the env-class from root.
          valueFrom = "/telemetry/${include.root.locals.namespace}/ingest/adot-config"
        }
      ]
      portMappings = [
        {
          containerPort = 4318
          protocol      = "tcp"
        },
        {
          # ADOT health_check extension port. Exposed on the task ENI so the internal ALB
          # target-group health check can reach 0.0.0.0:13133/ (HTTP 200). The OTLP/HTTP
          # receiver on 4318 returns 404 on "/", so the ALB probes 13133 instead -- without
          # this mapping the target reports Target.ResponseCodeMismatch [404] and the ECS
          # task cycles. The collector-ingestion reference health_check extension binds
          # 0.0.0.0:13133 and its ALB target group + adot_tasks SG use adot_health_check_port.
          containerPort = 13133
          protocol      = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/${local.namespace}-adot"
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "adot"
        }
      }
    }
  ])

  # Re-exposed root/envcommon locals so dependency mock_outputs (where the include
  # variable is not in scope) can reference them as local.* (copyability validate).
  dns_service_apex     = include.root.locals.dns_service_apex
  environment_instance = include.root.locals.environment_instance
}

# ---------------------------------------------------------------------------
# inputs: collector-ingestion reference module (AC-3, D37, D44, D45, D47)
#
# D37 inputs (consumed from upstream unit outputs, never recomputed):
#   firehose_delivery_stream_arn: from dependency.data_lake (never recomputed, D37).
#   waf_log_kms_key_arn: telemetry-data CMK from dependency.data_lake (iac/04 I6).
#   prod_hosted_zone_id: from dependency.dns_prod_zone (module interface parity; no record
#     written here -- dns-collector owns the public record R4, AC-8/D39).
#   certificate_arn: from dependency.acm_collector (D26, iac/04 section 1.A).
#   domain_validation_options: from dependency.acm_collector (D26).
#
# Naming inputs (collector_service_fqdn, collector_pretty_fqdn, max_request_body_size,
# rate_limit_per_ip, vpc_cidr_block, enable_custom_domain, adot_config_ssm_param_name,
# waf_allow_cidrs_ssm_param_name) are merged from _envcommon/collector-ingestion.hcl
# (via the "envcommon" include above). Leaf-level inputs extend the shared template
# with sandbox-specific values.
#
# D44: max_request_body_size = 4194304 and rate_limit_per_ip = 2000 are concrete
# defaults supplied by _envcommon/collector-ingestion.hcl (no TODO placeholders).
# D44: adot_receiver_max_request_body_size is a deployment-unique value sourced from
# terraform.tfvars (spec section 4.5, AC-FUNC-002); retune by editing terraform.tfvars.
# D5: memory_limiter_limit_mib and memory_limiter_spike_limit_mib use the module
# defaults (900 MiB / 200 MiB) which are concrete values meeting D44.
#
# The tags input uses include.root.locals.common_tags (not local.common_tags)
# because the root include block uses expose = true (spec 02).
# ---------------------------------------------------------------------------

inputs = merge({
  # D37: firehose ARN consumed from data-lake dependency output -- never recomputed (D37).
  firehose_delivery_stream_arn = dependency.data_lake.outputs.firehose_delivery_stream_arn

  # WAF logging to telemetry-data CMK per iac/04 I6.
  waf_log_kms_key_arn = dependency.data_lake.outputs.lake_kms_key_arn

  # Telemetry CloudWatch Logs ingest hop (awscloudwatchlogs exporter -> /telemetry/<env>/
  # ingest/otlp-logs -> subscription filter -> data-lake Firehose). The ingest log group
  # is encrypted with the same telemetry-data CMK as the WAF logs; the data-lake CMK
  # policy already grants logs.<region> via the ArnLike EncryptionContext condition that
  # covers this account/region's log groups, so no KMS policy change is required.
  telemetry_log_kms_key_arn = dependency.data_lake.outputs.lake_kms_key_arn

  # IAM role CloudWatch Logs assumes to deliver subscription-filter records to Firehose
  # (namespace-derived, separate from the ADOT task role and the Firehose delivery role).
  cwl_to_firehose_role_name = local.cwl_to_firehose_role_name

  # CloudFront standard-access-logging destination bucket (v1.0.2 REQUIRED input). Consumed
  # from the cloudfront-logs dependency output -- never recomputed (the collector's own
  # namespace differs from the cloudfront-logs namespace, so the name cannot be self-derived,
  # D37). The cloudfront-logs unit creates the SSE-S3 (AES256), ACL-enabled bucket CloudFront
  # standard logging requires. Wired in the leaf (not _envcommon) because it references a
  # dependency output, matching how firehose_delivery_stream_arn et al. are wired; both env
  # leaves are byte-identical so the wiring stays identical across envs (D31).
  access_log_bucket_name = dependency.cloudfront_logs.outputs.cloudfront_logs_bucket_name

  # VPC Flow Logs (collector-ingestion enable_flow_logs is true secure-by-default and
  # REQUIRES these two ARNs; the composed vpc-network primitive's aws_flow_log consumes them
  # -- iam_role_arn can't be empty for a cloud-watch-logs destination). Consumed from the
  # vpc-flow-logs dependency outputs -- never recomputed (D37). The KMS-encrypted log group +
  # delivery role live in that peer unit (AC-3: this leaf sources exactly one module and adds
  # no primitive). Wired in the leaf (references dependency outputs); both env leaves are
  # byte-identical so the wiring stays identical across envs (D31).
  enable_flow_logs          = true
  flow_logs_iam_role_arn    = dependency.vpc_flow_logs.outputs.flow_logs_iam_role_arn
  flow_logs_destination_arn = dependency.vpc_flow_logs.outputs.flow_logs_destination_arn

  # Hosted zone from dns-prod-zone dependency (AC-8, D37). Retained for module interface
  # parity; with create_public_dns_record = false no record is written here -- dns-collector
  # owns the public record R4.
  prod_hosted_zone_id = dependency.dns_prod_zone.outputs.zone_id

  # ACM certificate inputs from acm-collector dependency (D26, iac/04 section 1.A).
  certificate_arn = dependency.acm_collector.outputs.certificate_arn
  domain_validation_options = [for dvo in dependency.acm_collector.outputs.domain_validation_options : {
    domain_name           = dvo.domain_name
    resource_record_name  = dvo.resource_record_name
    resource_record_type  = dvo.resource_record_type
    resource_record_value = dvo.resource_record_value
  }]

  # Environment name for the ADOT SSM parameter path (/telemetry/<env>/ingest/adot-config).
  # Sourced from environment.hcl via root locals to stay input-driven (D31/D45).
  env       = include.root.locals.environment_name
  namespace = local.namespace
  is_active = local.is_active

  # Resource naming: all derived from namespace (D31/D37/D45).
  vpc_name       = local.vpc_name
  cluster_name   = local.cluster_name
  service_name   = local.service_name
  alb_name       = local.alb_name
  log_group_name = local.log_group_name

  # IAM role names for the ADOT ECS task (D37).
  # task_role_name must be <= 64 chars (IAM limit). The namespace is truncated
  # by the module's variable validation; using the leaf-local naming ensures
  # uniqueness across envs (D31).
  task_role_name      = local.task_role_name
  execution_role_name = local.exec_role_name

  # Trust policies for the ECS task and execution roles (sourced from service.hcl, D8).
  execution_role_assume_policy_json = local.ecs_execution_assume_role_policy_json
  task_role_assume_policy_json      = local.ecs_task_assume_role_policy_json

  # ADOT task role inline policies: Firehose + SSM + KMS access (iac/04 section 3.1).
  task_role_inline_policies = {
    AdotFirehoseSSMPolicy = local.adot_task_firehose_policy_json
  }

  # Subnet layout: derived from networks.json vpc_cidr_block (D6/D31/D47).
  subnet_layout = local.subnet_layout

  # VPC CIDR block override: explicitly set from networks.json-derived local to
  # override the dead _envcommon account.hcl read (both account.hcl files are
  # basename-only after E8-F2-S1-T1). Sandbox CIDR: 10.1.0.0/16.
  vpc_cidr_block = local.vpc_cidr_block

  # ADOT container definition (D5/D44): image pinned, config from SSM SecureString.
  container_definitions = local.adot_container_definitions

  # ADOT collector image (iac/04 I5): required by the composed ecs-app-deploy path.
  # Same pinned image as the container definition (single source: local.adot_image).
  adot_image = local.adot_image

  # vpc-network composition inputs (iac/04 S1a-S1d/S2), derived from the VPC
  # topology + region above. The collector-ingestion reference passes these
  # straight to the composed vpc-network module (D6).
  nat_gateways                     = local.nat_gateways
  interface_endpoint_service_names = local.interface_endpoint_service_names
  interface_endpoint_subnet_names  = local.interface_endpoint_subnet_names
  s3_gateway_endpoint_service_name = local.s3_gateway_endpoint_service_name

  # D44: ADOT receiver max body size (bytes). adot_receiver_max_request_body_size is a
  # deployment-unique value sourced from terraform.tfvars (spec section 4.5, AC-FUNC-002).
  # Terraform auto-loads terraform.tfvars after Terragrunt copies the unit directory into
  # the module working directory (spec section 1.1). To retune ADOT sizing for this
  # deployment, edit terraform.tfvars only.
  # Note: _envcommon/collector-ingestion.hcl max_request_body_size and this module variable
  # adot_receiver_max_request_body_size are the SAME ADOT OTLP HTTP receiver body-size value
  # (4194304 bytes = 4 MiB) wired through; adot_receiver_max_request_body_size is the only
  # declared module variable (references/collector-ingestion/variables.tf:107).

  # Tags from the root include (expose=true pattern -- not local.common_tags).
  tags = include.root.locals.common_tags
  },
  # ---------------------------------------------------------------------------
  # Child module source overrides (AC-7, AC-8, spec Section 4.3, Section 5).
  # When use_pinned_module_sources=true (prod): every in-repo child source is set to
  # its pinned git URL so the composed module tree is fully pinned.
  # When use_pinned_module_sources=false (sandbox/QA/root): the *_source keys are
  # OMITTED entirely so the module's relative-path defaults apply (spec Section 4.3,
  # Leaf behavior toggle=false: "Does NOT set any *_source inputs"). They MUST be
  # omitted -- not set to null -- because a terraform child module `source` argument
  # is a const-typed variable; passing an explicit null overrides the relative-path
  # default and crashes `terraform init` ("panic: value is null" in module install).
  # A conditional merge adds the keys only in the pinned (prod) context.
  # ---------------------------------------------------------------------------
  local.account_vars.locals.use_pinned_module_sources ? {
    vpc_network_source    = "${include.root.locals.module_git_base}//providers/aws/references/vpc-network?ref=providers/aws/references/vpc-network/v1.0.2"
    ecs_cluster_source    = "${include.root.locals.module_git_base}//providers/aws/references/ecs-app-cluster?ref=providers/aws/references/ecs-app-cluster/v1.0.2"
    ecs_deploy_source     = "${include.root.locals.module_git_base}//providers/aws/references/ecs-app-deploy?ref=providers/aws/references/ecs-app-deploy/v1.0.2"
    waf_webacl_source     = "${include.root.locals.module_git_base}//providers/aws/primitives/waf-webacl?ref=providers/aws/primitives/waf-webacl/v1.1.0"
    cloudfront_source     = "${include.root.locals.module_git_base}//providers/aws/primitives/cloudfront-distribution?ref=providers/aws/primitives/cloudfront-distribution/v1.1.1"
    route53_record_source = "${include.root.locals.module_git_base}//providers/aws/primitives/route53-record?ref=providers/aws/primitives/route53-record/v1.0.2"
  } : {}
)
