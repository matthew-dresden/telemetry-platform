# live/telemetry/us-east-1/sandbox/000/collector-ingestion/service.hcl
#
# Service layer for the sandbox collector-ingestion unit.
# Basename resolves to "collector-ingestion" per the canonical layer idiom
# (spec 02 Section 1.3).
#
# This unit provisions the full OTLP collector edge-to-service stack:
#   VPC network, internal ALB, ADOT ECS Fargate service, WAF WebACL (CLOUDFRONT scope),
#   CloudFront distribution, ACM certificate validation (aws_acm_certificate_validation),
#   and the same-account service-SAN validation CNAME (record R2), via a single call to
#   the references/collector-ingestion reference module (AC-3, E1-F5-S2-T2).
#
# The reference module is the canonical composition point; no primitive is sourced
# directly from this unit (AC-3).
#
# Module source: providers/aws/references/collector-ingestion (spec 02 Section 3.5).
# Pre-tag local source is used during initial rollout; replace with an immutable
# ?ref=<semver> tag once the module is published and promoted to stable.
#
# Record ownership (AC-8):
#   R2 -- same-account service-SAN validation CNAME -- is written ONLY by this unit.
#   No other unit in any account writes R2 (AC-8). The dns-prod-zone unit owns only
#   the hosted zone; acm-validate-collector owns R3. Single ownership is enforced by
#   the dependency graph, not timers.
#
# No-client-auth ingest posture (iac/04 section 6):
#   The OTLP/HTTP ingestion endpoint is intentionally public with NO client credentials.
#   There is no client secret, API key, or mTLS on the ingest path. This posture is
#   confirmed and intentional per iac/04 section 6.
#
# Dependencies:
#   - references/collector-ingestion module (E1-F5-S2-T2) must be present in the repo
#     at providers/aws/references/collector-ingestion before this unit can be applied.
#   - sandbox data-lake unit (E6-F2-S1-T1) must be applied and expose
#     firehose_delivery_stream_arn (consumed as a dependency input, never recomputed).
#   - sandbox dns-prod-zone unit (E6-F1-S1-T2) must be applied so the hosted zone
#     for R2 exists and zone_id is available via terragrunt dependency.
#   - sandbox identity unit must be applied and expose ecs_task_role_arn and
#     ecs_task_execution_role_arn for the ADOT ECS service.
#   - sandbox acm-collector unit (E6-F1-S1-T2) must expose certificate_arn and
#     domain_validation_options for the collector certificate.
#   - sandbox acm-validate-collector unit (E6-F4-S1-T1) must be applied so R3 is
#     written before aws_acm_certificate_validation waits for ISSUED status.
#   - state-bootstrap unit (E4-F1-S1-T1) must have been applied so the remote
#     state backend and lock table exist (D40).
#   - _envcommon/collector-ingestion.hcl supplies collector_service_fqdn,
#     collector_pretty_fqdn, max_request_body_size, rate_limit_per_ip,
#     vpc_cidr_block, ecs_task_assume_role_policy_json,
#     ecs_execution_assume_role_policy_json, and adot_task_firehose_policy_json
#     with no placeholder values (D44).
#
# The _envcommon/collector-ingestion.hcl shared template is included by the leaf
# terragrunt.hcl (not here at the service layer). No _envcommon include is used at
# this layer.

locals {
  # service resolves to the directory basename, i.e. "collector-ingestion".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())

  # ---------------------------------------------------------------------------
  # Hierarchy locals: account id, region, namespace (D45/D47).
  # All ARNs in this file are derived from these locals so no hardcoded account
  # id, region, or environment-specific string appears in the policy (D31/D45).
  # Mirrors the prod collector-ingestion service.hcl so the ARNs resolve to the
  # sandbox account/region/namespace.
  # ---------------------------------------------------------------------------
  account_vars   = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  aws_account_id = local.account_vars.locals.aws_account_id

  region = read_terragrunt_config(find_in_parent_folders("region.hcl")).locals.aws_region

  # Namespace is derived here from the leaf-resolvable hierarchy layer files directly
  # (D1, D36, mirroring root.hcl). root.hcl is NOT read via
  # read_terragrunt_config(find_in_parent_folders("root.hcl")) because root.hcl's own
  # find_in_parent_folders hierarchy reads resolve relative to root.hcl's directory
  # (terragrunt/) and abort parse. The service_instance component is the leaf basename
  # via get_original_terragrunt_dir() (this service.hcl evaluates with the service dir as
  # get_terragrunt_dir(); the original unit being processed is the leaf). Deriving from
  # the leaf-resolvable layer files keeps the copy-any-level property intact (spec 4.8, D1, D36).
  product          = read_terragrunt_config(find_in_parent_folders("product.hcl")).locals.product
  environment_name = read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment
  env_instance     = read_terragrunt_config(find_in_parent_folders("environment_instance.hcl")).locals.environment_instance
  service_name     = basename(get_terragrunt_dir())
  region_clean     = replace(local.region, "-", "")
  # Namespace field rule (mirrors root.hcl): "-" separates the 6 fields, "_" joins words
  # within a field (collector-ingestion -> collector_ingestion). namespace is canonical.
  namespace = join("-", [
    for f in [
      local.product,
      local.region_clean,
      local.environment_name,
      local.env_instance,
      local.service_name,
      basename(get_original_terragrunt_dir()),
    ] : replace(f, "-", "_")
  ])
  namespace_dns = replace(local.namespace, "_", "-")

  # ---------------------------------------------------------------------------
  # Scoped resource ARNs for adot_task_firehose_policy_json (AC-FIX-01..AC-FIX-03).
  # Each ARN is derived from namespace/region/aws_account_id so the policy is
  # input-driven and least-privilege for the sandbox ADOT ECS task role.
  # ---------------------------------------------------------------------------

  # Telemetry CloudWatch Logs ingest group ARN for the TelemetryCloudWatchLogsWrite
  # statement. The collector-ingestion module's awscloudwatchlogs ADOT exporter writes
  # here; the group is named /telemetry/<env>/ingest/otlp-logs (env = environment_name).
  # The :* suffix covers every log stream in the group. The ADOT task calls
  # logs:CreateLogStream + logs:PutLogEvents -- it NO LONGER writes to Firehose (the
  # CloudWatch Logs subscription filter delivers to Firehose via a separate role).
  telemetry_env           = read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment
  telemetry_log_group_arn = "arn:aws:logs:${local.region}:${local.aws_account_id}:log-group:/telemetry/${local.namespace}/ingest/otlp-logs:*"

  # AC-FIX-02: SSM parameter path ARN for SSMGetAdotConfig statement.
  # The ADOT config is stored at /<namespace>/ingestion/adot/collector-config.
  # The /* suffix covers the parameter and any subpaths.
  ssm_adot_param_prefix = "/${local.namespace}/ingestion/adot"
  ssm_adot_param_arn    = "arn:aws:ssm:${local.region}:${local.aws_account_id}:parameter${local.ssm_adot_param_prefix}/*"

  # AC-FIX-03: telemetry-data KMS CMK alias ARN for KMSDecryptForSSMAndLogs statement.
  # The CMK alias is created by the data-lake unit and named per iac/04 section 3.7.
  # It decrypts the SSM SecureString (ssm ViaService) and is granted to the telemetry
  # log group via the data-lake CMK policy (logs.<region> ArnLike condition).
  telemetry_data_cmk_alias = "alias/telemetry-data"
  telemetry_data_cmk_arn   = "arn:aws:kms:${local.region}:${local.aws_account_id}:${local.telemetry_data_cmk_alias}"

  # ---------------------------------------------------------------------------
  # ECS task trust policy (iac/04 section 3.1).
  # Trusts the ecs-tasks.amazonaws.com service principal for the ADOT ECS task role.
  # ---------------------------------------------------------------------------
  ecs_task_assume_role_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowECSTasksAssumption"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  # ---------------------------------------------------------------------------
  # ECS execution role trust policy (iac/04 section 3.1).
  # Trusts the ecs-tasks.amazonaws.com service principal for the execution role.
  # ---------------------------------------------------------------------------
  ecs_execution_assume_role_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowECSTasksAssumption"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  # ---------------------------------------------------------------------------
  # ADOT ECS task role inline policy (iac/04 section 3.1, AC-FIX-01..AC-FIX-04).
  # All three statements use namespace-derived scoped ARNs from the locals above.
  # No wildcard Resource is present. The ARN construction is performed entirely in
  # this service.hcl via namespace-derived locals; the references/collector-ingestion
  # module does NOT re-scope these ARNs.
  # ---------------------------------------------------------------------------
  adot_task_firehose_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # The awscloudwatchlogs ADOT exporter calls CreateLogStream + PutLogEvents on the
        # dedicated telemetry ingest group. The awsemf metrics exporter additionally calls
        # DescribeLogStreams on that group at startup (to resolve the EMF stream's upload
        # sequence token) -- without it the exporter fails to start. Scoped to that group's
        # ARN (:* covers all streams). The task no longer writes to Firehose -- the CloudWatch
        # Logs subscription filter delivers to Firehose via a separate role.
        Sid    = "TelemetryCloudWatchLogsWrite"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
        ]
        Resource = local.telemetry_log_group_arn
      },
      {
        Sid    = "SSMGetAdotConfig"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
        ]
        Resource = local.ssm_adot_param_arn
      },
      {
        # Decrypt the ADOT-config SSM SecureString. The kms:ViaService is narrowed to ssm
        # only now that the task writes to CloudWatch Logs (the awscloudwatchlogs exporter
        # writes plaintext events; CloudWatch Logs encrypts them server-side using the CMK
        # grant in the data-lake CMK policy for the logs.<region> principal, not the task
        # role). The obsolete firehose ViaService path is removed.
        Sid    = "KMSDecryptForSSM"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = local.telemetry_data_cmk_arn
        Condition = {
          StringLike = {
            "kms:ViaService" = [
              "ssm.${local.region}.amazonaws.com",
            ]
          }
        }
      },
    ]
  })
}
