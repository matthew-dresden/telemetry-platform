# ---------------------------------------------------------------------------
# collector-ingestion reference module
#
# Composes the full OTLP collector edge-to-service path:
#   vpc-network -> alb -> alb-listener -> ecs-app-cluster -> ecs-app-deploy (ADOT)
#   REUSED waf-webacl -> cloudfront-distribution -> aws_acm_certificate_validation
#   REUSED route53-record (R2)
#
# Design constraints:
#   D37: firehose_delivery_stream_arn, collector_service_fqdn, collector_pretty_fqdn,
#        prod_hosted_zone_id, certificate_arn, and domain_validation_options are all
#        INPUTS -- never computed outputs. This prevents the output-fed-as-input cycle.
#   D5:  Request shaping lives at the ADOT receiver, not WAF. The AOT_CONFIG_CONTENT
#        in the SSM SecureString carries max_request_body_size, memory_limiter, and
#        the content-type allowlist (application/x-protobuf, application/json).
#   D27.3 (superseded for structured-OTLP metrics): CloudWatch Logs remains the transport for every
#          signal. The metrics/structured pipeline (locals.tf) additionally accepts the OTLP
#          metrics signal and delivers it as CloudWatch EMF log events (awsemf exporter) on a
#          third stream in the SAME telemetry ingest log group -- still no AMP endpoint / no
#          prometheusremotewrite exporter, and /v1/traces is still unsupported.
#   docs/terragrunt-concepts.md: This reference performs certificate VALIDATION only.
#        ACM certificates are created exclusively by the acm-collector module.
#
# v1.0.2: namespace validation regex allows underscores (^[a-z0-9_-]+$). The
#         canonical prod namespace's service field is collector_ingestion -- '_'
#         joins words within a namespace field per the namespace contract. The
#         released v1.0.1 regex (^[a-z0-9-]+$) rejected it; this re-release
#         publishes the already-corrected regex. Backward-compatible: the new
#         pattern is a strict superset of the old one.
# v1.0.3: the internal ALB security group is now created by THIS reference
#         (aws_security_group.alb) instead of the composed alb primitive. The
#         primitive's managed SG performs a plan-time data "aws_vpc" lookup
#         (ec2:DescribeVpcs) to discover the VPC CIDR for its default egress
#         rule. This reference already builds the VPC (module.vpc_network) from
#         var.vpc_cidr_block, so it owns the CIDR and creates an equivalent SG
#         with NO plan-time AWS read. The adot_service ALB block sets
#         create_security_group = false + alb_security_group_ids, so the alb
#         primitive's data.aws_vpc count evaluates to 0 -- eliminating the only
#         live plan-time AWS read in the module. Security posture is preserved:
#         VPC-CIDR-scoped ingress + VPC-CIDR-scoped egress on the collector-owned
#         SG (byte-for-byte equivalent to the primitive's managed SG).
# v1.0.4: the collector CloudFront distribution now reaches the INTERNAL ALB
#         through a CloudFront VPC origin (cloudfront-distribution origin_type =
#         "vpc", vpc_origin_arn = module.adot_service.alb_arn) instead of a public
#         custom origin. This removes a P0 origin self-loop: the prior custom origin
#         used domain_name = collector_service_fqdn, which is a Route53 A-alias to
#         this SAME distribution, so CloudFront resolved its own domain and returned
#         HTTP 403 on every OTLP request; a public custom origin also could not reach
#         the scheme=internal ALB at all. With a VPC origin CloudFront routes by the
#         ALB ARN (private path), NOT by public DNS of domain_name, so there is no
#         loop and the internal ALB stays internal. The collector-owned ALB SG ingress
#         is tightened from the broad VPC-CIDR rule to the CloudFront origin-facing
#         managed prefix list (var.cloudfront_origin_facing_prefix_list_id) on the
#         HTTPS listener port (var.alb_https_listener_port) -- CloudFront is the single
#         public entry point. The prefix list id is an INPUT (no
#         data.aws_ec2_managed_prefix_list lookup) so the module keeps ZERO plan-time
#         AWS reads. The viewer DNS chain (collector.<pretty> / collector_service_fqdn
#         A-alias -> CloudFront) is unchanged; only the ORIGIN side changed.
# v1.4.0: input-driven ECS scaling + right-sizing for high-concurrency ingestion (e.g.
#         ~2000 concurrent Claude Code sessions). desired_count (previously hardcoded 1)
#         and enable_autoscaling (previously hardcoded false) on the adot_service
#         (ecs-app-deploy) call are now var.adot_desired_count / var.adot_enable_autoscaling,
#         and the ecs-app-deploy/ecs-service CPU target-tracking autoscaling
#         (predefined_metric_type = ECSServiceAverageCPUUtilization, already implemented
#         downstream) is wired via the new var.adot_autoscaling object. adot_desired_count
#         is the PRIMARY capacity lever -- ECS scale-out latency is minutes, so the
#         baseline must already carry the expected concurrent load; autoscaling is the
#         elastic cushion above it and the off-hours scale-in below it. adot_task_cpu
#         (512->1024) and adot_task_memory (1024->2048) defaults are right-sized to match,
#         with memory_limiter_limit_mib/memory_limiter_spike_limit_mib (900/200->1800/400)
#         raised proportionally. The awscloudwatchlogs / awscloudwatchlogs/structured
#         exporter sending_queue and the shared batch processor (locals.tf
#         adot_config_content) gain input-driven sizing (previously unsized) so exporter
#         throughput scales with the larger baseline. Deliberately deferred:
#         ALBRequestCountPerTarget/request-count autoscaling, which needs
#         alb_resource_label plumbing through ecs-app-deploy (out of scope here).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# VPC network -- composed vpc-network reference module.
# ---------------------------------------------------------------------------
module "vpc_network" {
  source = var.vpc_network_source

  vpc_name       = var.vpc_name
  vpc_cidr_block = var.vpc_cidr_block

  public_subnets = [
    for s in local.public_subnets : {
      name              = s.name
      cidr_block        = s.cidr_block
      availability_zone = s.availability_zone
    }
  ]

  private_subnets = [
    for s in local.private_subnets : {
      name              = s.name
      cidr_block        = s.cidr_block
      availability_zone = s.availability_zone
    }
  ]

  nat_gateways                     = var.nat_gateways
  interface_endpoint_service_names = var.interface_endpoint_service_names
  interface_endpoint_subnet_names  = var.interface_endpoint_subnet_names
  s3_gateway_endpoint_service_name = var.s3_gateway_endpoint_service_name

  enable_dns_hostnames      = true
  enable_flow_logs          = var.enable_flow_logs
  flow_logs_iam_role_arn    = var.flow_logs_iam_role_arn
  flow_logs_destination_arn = var.flow_logs_destination_arn

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "vpc-network"
}

# ---------------------------------------------------------------------------
# ADOT ECS task security group (docs/terragrunt-concepts.md).
# Owned by this reference (it is the module that composes both vpc-network --
# which builds the VPC -- and ecs-app-deploy -- which consumes the task SG), so
# the SG is no longer an external input (which had no upstream creator in the
# live tree). It is attached to the VPC built by the composed vpc-network module
# and allows inbound OTLP/HTTP on the collector container port from inside the
# VPC (the internal ALB forwards to this port) plus all egress so the collector
# reaches Firehose via the VPC endpoints / NAT.
# ---------------------------------------------------------------------------
resource "aws_security_group" "adot_tasks" {
  name        = "${var.service_name}-adot-sg"
  description = "ECS task security group for the ADOT collector service ${var.service_name}"
  vpc_id      = module.vpc_network.vpc_id

  ingress {
    description = "OTLP/HTTP from inside the VPC (internal ALB to ADOT receiver)"
    from_port   = var.adot_container_port
    to_port     = var.adot_container_port
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr_block]
  }

  ingress {
    description = "ADOT health_check extension from inside the VPC (internal ALB target-group health check)"
    from_port   = var.adot_health_check_port
    to_port     = var.adot_health_check_port
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr_block]
  }

  # Egress is restricted to var.adot_egress_cidr_blocks, which defaults to the VPC
  # CIDR so the collector reaches Firehose, CloudWatch Logs, SSM, ECR, and S3 only
  # through in-VPC interface/gateway endpoints. Widen adot_egress_cidr_blocks only
  # when the collector must reach an endpoint that has no VPC endpoint (for example
  # pulling a public ECR image over NAT). The AWS SG rule description is capped at
  # 255 characters, so the full rationale lives in this comment.
  egress {
    description = "Outbound to AWS services via in-VPC interface/gateway endpoints (Firehose, CloudWatch Logs, SSM, ECR, S3) within adot_egress_cidr_blocks (defaults to the VPC CIDR)."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = var.adot_egress_cidr_blocks != null ? var.adot_egress_cidr_blocks : [var.vpc_cidr_block]
  }

  tags = merge(local.common_tags, { Name = "${var.service_name}-adot-sg" })
}

# ---------------------------------------------------------------------------
# Internal ALB security group (collector-owned).
#
# This reference creates the internal ALB's security group itself rather than
# letting the composed alb primitive (reached via ecs-app-deploy) create it. The
# primitive's managed SG performs a plan-time data "aws_vpc" lookup
# (ec2:DescribeVpcs) to discover the VPC CIDR for its default egress rule. This
# reference already builds the VPC (module.vpc_network) from var.vpc_cidr_block,
# so it OWNS the CIDR and can create an equivalent SG with NO plan-time AWS read.
# The adot_service ALB block below therefore sets create_security_group = false
# and passes this SG via alb_security_group_ids, so the alb primitive's
# data.aws_vpc count evaluates to 0 -- eliminating the only live plan-time AWS
# read in the module (so the dns-owner cross-account terragrunt plan can succeed
# without real ec2:DescribeVpcs access).
#
# Security posture (least privilege, CloudFront is the single public entry point):
#   ingress: TCP var.alb_https_listener_port (the HTTPS listener port) from the AWS
#            CloudFront origin-facing managed prefix list ONLY
#            (var.cloudfront_origin_facing_prefix_list_id ==
#            com.amazonaws.global.cloudfront.origin-facing). The collector
#            distribution reaches this INTERNAL ALB through a CloudFront VPC origin
#            (module.cloudfront origin_type = "vpc"); CloudFront origin-facing
#            traffic is sourced from that managed prefix list, so allowing exactly
#            it on the listener port is the AWS-recommended, least-privilege rule.
#            Nothing else inside the VPC initiates connections to the internal ALB,
#            so the prior broad VPC-CIDR ingress is removed. The prefix list id is an
#            INPUT (not a data.aws_ec2_managed_prefix_list lookup) so the module keeps
#            ZERO plan-time AWS reads (the dns-owner cross-account plan must succeed
#            without ec2:DescribeManagedPrefixLists). The ALB to ENI/target traffic is
#            governed by the egress below + the adot_tasks SG ingress, not this rule.
#   egress : all protocols to the VPC CIDR -- an ALB only forwards to its in-VPC
#            registered targets, so VPC-scoped egress is the secure default
#            (var.vpc_cidr_block, the CIDR module.vpc_network builds the VPC from).
# ---------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "${var.alb_name}-alb-sg"
  description = "Managed security group for ALB ${var.alb_name}"
  vpc_id      = module.vpc_network.vpc_id

  ingress {
    description     = "HTTPS from the collector CloudFront distribution via the VPC origin (CloudFront origin-facing managed prefix list); CloudFront is the single public entry point to the internal ALB"
    from_port       = var.alb_https_listener_port
    to_port         = var.alb_https_listener_port
    protocol        = "tcp"
    prefix_list_ids = [var.cloudfront_origin_facing_prefix_list_id]
  }

  egress {
    description = "Allow outbound traffic to ALB targets within the VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr_block]
  }

  tags = merge(local.common_tags, { Name = "${var.alb_name}-alb-sg" })
}

# ---------------------------------------------------------------------------
# ECS cluster -- composed ecs-app-cluster reference module.
# ---------------------------------------------------------------------------
module "ecs_cluster" {
  source = var.ecs_cluster_source

  cluster_name              = var.cluster_name
  capacity_providers        = ["FARGATE"]
  enable_container_insights = true
  execution_role_name       = var.execution_role_name

  execution_role_managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
  ]

  # Grant the shared execution role ssm:GetParameters on the ADOT collector-config
  # SecureString it must read to start the task. The AmazonECSTaskExecutionRolePolicy
  # managed policy does not cover custom SSM parameters, so without this the task fails
  # to start (AccessDeniedException on ssm:GetParameters). The parameter uses the default
  # AWS-managed SSM key, so no kms:Decrypt grant is required. Scoped to the exact ARN.
  execution_role_inline_policies = {
    AdotConfigSsmRead = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "AdotConfigSsmGetParameters"
          Effect   = "Allow"
          Action   = ["ssm:GetParameters"]
          Resource = [aws_ssm_parameter.adot_config.arn]
        }
      ]
    })
  }

  alarms         = {}
  log_groups     = {}
  dashboard_name = "${var.cluster_name}-dashboard"
  dashboard_body = jsonencode({ widgets = [] })

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "ecs-app-cluster"

  depends_on = [module.vpc_network]
}

# ---------------------------------------------------------------------------
# ADOT SSM SecureString -- stores the ADOT AOT_CONFIG_CONTENT.
# Threaded into ecs-app-deploy so the ADOT container reads config from SSM.
# ---------------------------------------------------------------------------
resource "aws_ssm_parameter" "adot_config" {
  name  = local.adot_config_ssm_path
  type  = "SecureString"
  value = local.adot_config_content

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Telemetry CloudWatch Logs ingest hop.
#
# The awscloudwatchlogs ADOT exporter writes each OTLP log record body string
# (raw_log = true) into this dedicated group. A match-all subscription filter then
# forwards every record to the EXISTING data-lake Firehose (no new Firehose), which
# natively decompresses + strips the CloudWatch Logs envelope before Parquet
# conversion. The exporter target (locals.telemetry_log_group_name) and this group
# share one source so they never drift.
#
# KMS: encrypted with the telemetry-data CMK (var.telemetry_log_kms_key_arn). The
# data-lake CMK policy already grants logs.<region>.amazonaws.com via the ArnLike
# kms:EncryptionContext:aws:logs:arn condition covering this account/region's log
# groups, so no KMS policy change is needed for this group (prior fix).
#
# Retention: var.telemetry_log_retention_in_days (default 1) -- the durable copy is
# the Parquet lake, so a short retention on this transient hop is sufficient.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "telemetry_ingest" {
  name              = local.telemetry_log_group_name
  retention_in_days = var.telemetry_log_retention_in_days
  kms_key_id        = var.telemetry_log_kms_key_arn

  tags = local.common_tags
}

# Explicit log stream matching the awscloudwatchlogs exporter log_stream_name so the
# exporter does not rely on auto-create.
resource "aws_cloudwatch_log_stream" "telemetry_ingest" {
  name           = local.telemetry_log_stream_name
  log_group_name = aws_cloudwatch_log_group.telemetry_ingest.name
}

# ---------------------------------------------------------------------------
# CloudWatch Logs -> Firehose delivery role.
#
# CloudWatch Logs subscription filters assume THIS role (trust logs.<region>) to
# PutRecord/PutRecordBatch into the data-lake Firehose. It is SEPARATE from the ADOT
# task role and the Firehose delivery role. The trust includes an aws:SourceArn
# condition scoping the assumption to this telemetry log group's ARN, and the inline
# policy is scoped to the exact Firehose stream ARN (no wildcard).
# ---------------------------------------------------------------------------
resource "aws_iam_role" "cwl_to_firehose" {
  name = var.cwl_to_firehose_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudWatchLogsAssumption"
        Effect = "Allow"
        Principal = {
          Service = "logs.${local.region}.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        Condition = {
          # BUG-2 fix: CloudWatch Logs presents aws:SourceArn in TWO forms for this role -- the
          # BARE log-group ARN during PutSubscriptionFilter's synchronous test-message delivery
          # (filter creation), and the ARN WITH the trailing ':*' segment during ongoing runtime
          # delivery. The ArnLike condition must allow BOTH (a single-form pattern breaks one phase:
          # ':*'-only -> "Could not deliver test message" at creation; bare-only -> 0 records at
          # runtime). local.cwl_to_firehose_trust_source_arns carries both forms (ArnLike list = OR),
          # scoped to exactly this telemetry ingest log group. Verified empirically in qa.
          ArnLike = {
            "aws:SourceArn" = local.cwl_to_firehose_trust_source_arns
          }
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "cwl_to_firehose" {
  name = "${var.cwl_to_firehose_role_name}-firehose-put"
  role = aws_iam_role.cwl_to_firehose.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "FirehosePutRecords"
        Effect = "Allow"
        Action = [
          "firehose:PutRecord",
          "firehose:PutRecordBatch",
        ]
        Resource = var.firehose_delivery_stream_arn
      },
      {
        # The data-lake Firehose has server_side_encryption with the telemetry-data CMK
        # (CUSTOMER_MANAGED_CMK), so any PutRecord/PutRecordBatch caller must hold
        # kms:GenerateDataKey on that CMK -- otherwise CloudWatch Logs' PutSubscriptionFilter
        # test delivery fails ("Could not deliver test message ... caller might not have
        # sufficient permissions for the CMK"). The CMK key policy delegates to the account
        # root (RootAdminAccess), so this IAM grant is sufficient (no key-policy edit, no
        # cross-unit cycle). Scoped to the exact lake CMK ARN.
        Sid    = "FirehoseStreamCmkAccess"
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
        ]
        Resource = var.telemetry_log_kms_key_arn
      }
    ]
  })
}

# Match-all subscription filter forwarding the telemetry ingest group to the EXISTING
# data-lake Firehose via the CWL-to-Firehose role (filter_pattern = "" matches every
# log event).
resource "aws_cloudwatch_log_subscription_filter" "telemetry_ingest_to_firehose" {
  name            = "${var.service_name}-telemetry-ingest-to-firehose"
  log_group_name  = aws_cloudwatch_log_group.telemetry_ingest.name
  filter_pattern  = ""
  destination_arn = var.firehose_delivery_stream_arn
  role_arn        = aws_iam_role.cwl_to_firehose.arn

  depends_on = [
    aws_cloudwatch_log_group.telemetry_ingest,
    aws_iam_role_policy.cwl_to_firehose,
  ]
}

# ---------------------------------------------------------------------------
# ADOT ECS service -- composed ecs-app-deploy reference module.
# The ADOT container reads AOT_CONFIG_CONTENT from the SSM SecureString.
# ---------------------------------------------------------------------------
module "adot_service" {
  source = var.ecs_deploy_source

  env                = var.env
  namespace          = var.namespace
  service_name       = var.service_name
  cluster_arn        = module.ecs_cluster.cluster_arn
  execution_role_arn = module.ecs_cluster.execution_role_arn

  task_role_name            = var.task_role_name
  task_role_inline_policies = var.task_role_inline_policies

  # ADOT config-change redeploy trigger (deploy-correctness fix).
  #
  # The ADOT config is delivered BY-REFERENCE: the task reads the SSM SecureString
  # (aws_ssm_parameter.adot_config) at task STARTUP via the container-def secrets
  # valueFrom, so a config-only change updates the SSM parameter but does NOT alter
  # the task definition. Without a task-definition delta ECS never rolls the service,
  # and the running collector keeps serving the OLD config (empirically confirmed in
  # sandbox). Embedding sha256(local.adot_config_content) as an environment variable
  # in EVERY container makes any config change produce a new task-definition revision,
  # which rolls the ECS service declaratively (immutable-deploy) -- the new tasks then
  # read the updated SSM value at startup.
  container_definitions = jsonencode([
    for c in jsondecode(var.container_definitions) : merge(c, {
      environment = concat(try(c.environment, []), [
        { name = "ADOT_CONFIG_SHA256", value = sha256(local.adot_config_content) }
      ])
    })
  ])

  # Scaling: adot_desired_count is the PRIMARY capacity lever (the baseline carries the
  # expected concurrent load, e.g. ~2000 concurrent Claude Code sessions); adot_autoscaling
  # is the elastic cushion above it and the cost lever below it (off-hours scale-in). ECS
  # scale-out latency is on the order of minutes, so the running floor -- not a future
  # scaling event -- is what keeps up with load (see variables.tf for the full rationale).
  desired_count            = var.adot_desired_count
  task_cpu                 = var.adot_task_cpu
  task_memory              = var.adot_task_memory
  subnet_ids               = module.vpc_network.private_subnet_ids
  security_group_ids       = [aws_security_group.adot_tasks.id]
  vpc_id                   = module.vpc_network.vpc_id
  assign_public_ip         = false
  enable_autoscaling       = var.adot_enable_autoscaling
  autoscaling              = var.adot_autoscaling
  log_group_retention_days = 30
  log_group_kms_key_arn    = var.telemetry_log_kms_key_arn

  alb = {
    name           = var.alb_name
    internal       = true
    alb_subnet_ids = module.vpc_network.public_subnet_ids
    # The internal ALB security group is owned by THIS reference
    # (aws_security_group.alb) so the alb primitive performs no plan-time
    # data "aws_vpc" lookup. create_security_group = false makes the primitive's
    # data.aws_vpc count = 0; ingress_cidr_blocks is unused by the primitive in
    # that mode (the VPC-CIDR-scoped ingress lives on the collector-owned SG).
    alb_security_group_ids = [aws_security_group.alb.id]
    create_security_group  = false
    idle_timeout           = 60
    target_groups = [
      {
        name        = local.adot_target_group_name
        port        = var.adot_container_port
        protocol    = "HTTP"
        target_type = "ip"
        # Health check probes the ADOT health_check extension, NOT the OTLP/HTTP receiver.
        # The OTLP receiver on adot_container_port (4318) does not serve a health path and
        # returns 404 on "/" and "/health/status" (DOCKER-VALIDATED), so probing it gives
        # Target.ResponseCodeMismatch [404] and the ECS task cycles. The basic health_check
        # extension on adot_health_check_port (13133) serves HTTP 200 on "/" when the
        # pipeline is healthy, so the ALB probes that port + path instead. Single source of
        # truth is local.adot_target_group_health_check (locals.tf), also exposed via the
        # *_echo outputs so configured values and Terratest assertions never drift (DRY).
        health_check = local.adot_target_group_health_check
      }
    ]
  }

  alb_listeners = {
    listeners = [
      {
        name     = "${var.service_name}-https"
        port     = var.alb_https_listener_port
        protocol = "HTTPS"
        # HTTPS listeners require a non-empty ssl_policy (alb-listener primitive
        # validation). Input-driven; default is a current TLS 1.3 ELB policy.
        ssl_policy = var.alb_ssl_policy
        default_action = {
          type             = "forward"
          target_group_arn = ""
        }
        certificate_arn = var.certificate_arn
      }
    ]
    listener_rules = []
  }

  alarms = {}

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "ecs-app-deploy"

  # Gate on the cert validation waiter: the HTTPS listener below attaches var.certificate_arn,
  # which must be ISSUED first (otherwise UnsupportedCertificate on a cold apply).
  depends_on = [module.ecs_cluster, aws_ssm_parameter.adot_config, aws_acm_certificate_validation.collector]
}

# ---------------------------------------------------------------------------
# WAF WebACL -- REUSED waf-webacl primitive with CLOUDFRONT scope.
# Configured per docs/terragrunt-concepts.md with three managed rule groups at
# p10/p20/p30 (AnonymousIpList omitted -- open-ingestion posture, see locals.tf),
# rate_limit_per_ip, logging_enabled=true, and log_kms_key_arn.
# ---------------------------------------------------------------------------
module "waf_webacl" {
  source = var.waf_webacl_source

  name           = "${var.service_name}-waf"
  scope          = "CLOUDFRONT"
  default_action = "allow"

  # Three managed rule groups per docs/terragrunt-concepts.md, sourced from the
  # single-source-of-truth local (DRY -- the waf_managed_rule_names + waf_managed_rule_groups_echo
  # outputs derive from the same local so the configuration and the Terratest assertions never
  # drift). Every group's override_action is "none" (fully enforced); the ONLY neutralized rule is
  # AWSManagedRulesCommonRuleSet's SizeRestrictions_BODY sub-rule, retargeted to "count" via the
  # waf-webacl v1.1.0 rule_action_override capability so legitimate large OTLP bodies (up to the
  # 4 MiB ADOT receiver cap, D5) are not 403'd while every other rule stays enforced (BUG-1). See
  # locals.tf for the full rationale.
  managed_rule_groups = local.waf_managed_rule_groups

  # Rate limiting per D44 -- IP-based only. The waf-webacl primitive supports
  # only the rate_limit_per_ip argument; per-header rate limiting is not declared
  # by the primitive and must not be passed (AC-FIX-T7-4).
  rate_limit_per_ip = var.rate_limit_per_ip

  # WAF logging (docs/terragrunt-concepts.md): the reused waf-webacl owns its OWN CloudWatch
  # log group (create_log_group), named aws-waf-logs-<name> per the AWS requirement,
  # encrypted with the telemetry-data CMK (docs/terragrunt-concepts.md). The web ACL owns its logging
  # destination, so no external destination ARN is supplied.
  logging_enabled       = true
  create_log_group      = true
  log_retention_in_days = var.waf_log_retention_in_days
  log_kms_key_arn       = var.waf_log_kms_key_arn

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "waf-webacl"
}

# ---------------------------------------------------------------------------
# CloudFront distribution -- composed cloudfront-distribution primitive.
# The WAF WebACL ARN is wired from module.waf_webacl.web_acl_arn.
# ---------------------------------------------------------------------------
module "cloudfront" {
  source = var.cloudfront_source

  enabled     = true
  aliases     = var.is_active ? [var.collector_service_fqdn, var.collector_pretty_fqdn] : [var.collector_service_fqdn]
  price_class = "PriceClass_100"
  web_acl_id  = module.waf_webacl.web_acl_arn

  # VPC origin: CloudFront reaches the INTERNAL ALB (module.adot_service.alb_arn)
  # privately through a CloudFront VPC origin instead of over the public internet.
  # This removes the prior origin self-loop: the previous "custom" origin used
  # domain_name = collector_service_fqdn, which is a Route53 A-alias to THIS same
  # distribution, so CloudFront resolved its own domain and looped (HTTP 403). With a
  # VPC origin CloudFront routes by the ALB ARN (the vpc origin), NOT by public DNS of
  # domain_name -- so no loop -- and it can reach the INTERNAL (scheme=internal) ALB
  # that a public custom origin cannot. domain_name stays = collector_service_fqdn
  # because it is used only for the Host header + TLS SNI/cert validation: the ALB
  # HTTPS-listener certificate (var.certificate_arn) covers collector_service_fqdn, so
  # the https-only origin TLS handshake validates (no 502). https_port matches the ALB
  # HTTPS listener (var.alb_https_listener_port).
  origin = {
    domain_name                   = var.collector_service_fqdn
    origin_id                     = "${var.namespace}-adot"
    origin_type                   = "vpc"
    vpc_origin_arn                = module.adot_service.alb_arn
    http_port                     = 80
    https_port                    = var.alb_https_listener_port
    custom_origin_protocol_policy = "https-only"
    custom_origin_ssl_protocols   = ["TLSv1.2"]
    origin_keepalive_timeout      = 5
    origin_read_timeout           = 30
  }

  default_cache_behavior = {
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
  }

  acm_certificate_arn      = var.certificate_arn
  minimum_protocol_version = "TLSv1.2_2021"

  # CloudFront standard (legacy) access logging to the centralized access-log bucket.
  # The destination bucket is owned outside this module and passed in by name;
  # CloudFront standard logging requires an SSE-S3 (AES256), ACL-enabled destination
  # bucket, so this module does not create or encrypt it.
  logging_config = {
    bucket          = "${var.access_log_bucket_name}.s3.amazonaws.com"
    prefix          = "${var.namespace}/cloudfront/"
    include_cookies = false
  }

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "cloudfront-distribution"

  # acm_certificate_arn must be ISSUED before the distribution references it.
  depends_on = [module.waf_webacl, module.adot_service, aws_acm_certificate_validation.collector]
}

# ---------------------------------------------------------------------------
# ACM certificate validation -- validates the passed-in certificate.
# Per docs/terragrunt-concepts.md, this reference NEVER creates an aws_acm_certificate.
# Certificates are created exclusively by the acm-collector module.
# ---------------------------------------------------------------------------
resource "aws_acm_certificate_validation" "collector" {
  certificate_arn = var.certificate_arn

  validation_record_fqdns = [
    for dvo in var.domain_validation_options : dvo.resource_record_name
  ]

  # No module depends_on: this waiter needs only the certificate ARN and the validation-record
  # FQDNs (both inputs), so it runs EARLY and polls ACM until the certificate reaches ISSUED.
  # The certificate consumers -- the ALB HTTPS listener in module.adot_service and
  # module.cloudfront -- depend on THIS resource so they never attach a still-PENDING
  # certificate. Previously this waiter depended on module.cloudfront (placing it last), so the
  # listener/distribution attached the raw cert before validation and a cold apply failed with
  # UnsupportedCertificate whenever validation had not yet completed.
}

# ---------------------------------------------------------------------------
# Public DNS record -- REUSED route53-record primitive pointing the collector
# pretty FQDN at the CloudFront distribution. Uses CNAME record type with the
# CloudFront distribution domain name as the record value, satisfying the
# route53-record primitive's records interface.
#
# SINGLE-OWNER TOGGLE (create_public_dns_record):
# When deployed as a standalone example/terratest fixture (default true), this
# reference owns the public DNS record: the fixture creates its own Route53 zone
# and nothing else points the collector hostname at CloudFront, so it must be
# created here.
#
# When deployed as a LIVE leaf (set false in _envcommon/collector-ingestion.hcl +
# leaf), the dedicated dns-collector unit is the SINGLE owner of the public
# collector hostname record (R4), which it writes as an A ALIAS to the same
# CloudFront distribution. Creating a CNAME for the same name here would conflict
# with the A-alias ("conflicting RRSet of type CNAME with the same DNS name") and
# fail apply (D39, AC-8). The toggle therefore drops this record via count so
# exactly one unit owns the public collector record.
# ---------------------------------------------------------------------------
module "route53_record" {
  source = var.route53_record_source
  count  = var.create_public_dns_record ? 1 : 0

  zone_id = var.prod_hosted_zone_id
  name    = var.collector_pretty_fqdn
  type    = "CNAME"
  ttl     = var.route53_record_ttl
  records = [module.cloudfront.distribution_domain_name]

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "route53-record"

  depends_on = [module.cloudfront, aws_acm_certificate_validation.collector]
}
