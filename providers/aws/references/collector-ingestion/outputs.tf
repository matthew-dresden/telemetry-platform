# ---------------------------------------------------------------------------
# Primary outputs (AC-1) -- four unprefixed outputs per spec.
# CloudFront outputs sourced from module.cloudfront per the D37 output contract.
# ---------------------------------------------------------------------------

output "cloudfront_domain_name" {
  description = "CloudFront distribution domain name (e.g. d1234abcdef.cloudfront.net). Sourced unprefixed from module.cloudfront.distribution_domain_name per AC-1."
  value       = module.cloudfront.distribution_domain_name
}

output "cloudfront_hosted_zone_id" {
  description = "CloudFront distribution hosted zone ID for Route53 alias records. Sourced unprefixed from module.cloudfront.distribution_hosted_zone_id per AC-1."
  value       = module.cloudfront.distribution_hosted_zone_id
}

output "alb_arn" {
  description = "ARN of the Application Load Balancer fronting the ADOT ECS service."
  value       = module.adot_service.alb_arn
}

output "cloudfront_vpc_origin_id" {
  description = "ID of the CloudFront VPC origin the distribution uses to reach the internal ALB privately (origin_type = vpc). Non-null whenever the collector edge is wired correctly; a null value would indicate the distribution reverted to a public custom origin (the self-loop regression)."
  value       = module.cloudfront.vpc_origin_id
}

output "ecs_service_name" {
  description = "Name of the ADOT ECS service."
  value       = module.adot_service.service_name
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster hosting the ADOT service. The ClusterName dimension for CloudWatch ECS alarms (consumed by the observability unit, D37)."
  value       = module.ecs_cluster.cluster_name
}

output "alb_arn_suffix" {
  description = "ARN suffix (app/<name>/<id>) of the ALB fronting the ADOT ECS service. The LoadBalancer dimension for CloudWatch ApplicationELB alarms (consumed by the observability unit, D37)."
  value       = module.adot_service.alb_arn_suffix
}

output "waf_web_acl_id" {
  description = "ID of the WAFv2 web ACL protecting the collector. The WebACL dimension for CloudWatch WAFV2 alarms (consumed by the observability unit, D37)."
  value       = module.waf_webacl.web_acl_id
}

# ---------------------------------------------------------------------------
# Echo outputs for Terratest contract assertions.
# These expose internal values so the test suite can assert D37, D44, D5, D27.3,
# and docs/terragrunt-concepts.md compliance without deploying to live AWS infrastructure.
# ---------------------------------------------------------------------------

output "firehose_delivery_stream_arn_echo" {
  description = "Echo of the firehose_delivery_stream_arn input (D37 contract assertion: this must be an input, never a computed output)."
  value       = var.firehose_delivery_stream_arn
}

output "waf_managed_rule_names" {
  description = "Comma-separated list of WAF managed rule group names in priority order. Exposed for Terratest docs/terragrunt-concepts.md assertions."
  value       = local.waf_managed_rule_names
}

output "waf_managed_rule_groups_echo" {
  description = "JSON-encoded WAF managed rule groups configuration (name, vendor_name, priority, group-level override_action, and per-sub-rule rule_action_overrides). Exposed for Terratest assertions that the AWSManagedRulesCommonRuleSet SizeRestrictions_BODY sub-rule is overridden to count -- so large OTLP request bodies (up to the 4 MiB ADOT receiver cap, D5) are counted rather than 403'd -- while every group's override_action stays \"none\" (every other managed rule enforced). docs/terragrunt-concepts.md, BUG-1."
  value       = jsonencode(local.waf_managed_rule_groups)
}

output "waf_rate_limit_per_ip_echo" {
  description = "Echo of the rate_limit_per_ip input value. Exposed for Terratest D44 concrete-value assertions."
  value       = tostring(var.rate_limit_per_ip)
}

output "waf_logging_enabled_echo" {
  description = "Echo of the WAF logging_enabled flag (always true per docs/terragrunt-concepts.md). Exposed for Terratest assertions."
  value       = "true"
}

output "waf_log_kms_key_arn_echo" {
  description = "Echo of the waf_log_kms_key_arn input (telemetry-data CMK). Exposed for Terratest docs/terragrunt-concepts.md assertions."
  value       = var.waf_log_kms_key_arn
}

output "adot_config_content_echo" {
  description = "Rendered ADOT AOT_CONFIG_CONTENT YAML. Exposed for Terratest D5 (memory_limiter, content-type, max_request_body_size), D27.3 (logs pipeline plus the metrics/structured EMF pipeline), and structured-OTLP logs/metrics routing (filter/keep_structured, filter/keep_structured_metrics) assertions."
  value       = local.adot_config_content
}

output "adot_config_sha256_echo" {
  description = "sha256 fingerprint of the rendered ADOT config (local.adot_config_content), the same value embedded as the ADOT_CONFIG_SHA256 environment variable in every container definition passed to the adot_service task definition. The config is delivered by-reference (SSM SecureString read at task startup), so this embedded fingerprint is what forces a new task-definition revision -- and therefore a declarative ECS service roll -- on any config-only change. Exposed for Terratest DRY assertions against the applied task definition."
  value       = sha256(local.adot_config_content)
}

# ---------------------------------------------------------------------------
# ECS scaling echo outputs (high-concurrency ingestion sizing). Exposed so Terratest
# can assert the configured values without re-deriving them, and cross-check them
# against the REAL applied ECS service / Application Auto Scaling state via the AWS SDK.
# ---------------------------------------------------------------------------

output "adot_desired_count_echo" {
  description = "Echo of the adot_desired_count input (the baseline/initial ECS desired task count, the PRIMARY capacity lever). Exposed for Terratest assertions against the applied ECS service DesiredCount."
  value       = var.adot_desired_count
}

output "adot_enable_autoscaling_echo" {
  description = "Echo of the adot_enable_autoscaling input. Exposed for Terratest assertions that CPU target-tracking Application Auto Scaling is enabled by default."
  value       = var.adot_enable_autoscaling
}

output "adot_autoscaling_echo" {
  description = "JSON-encoded echo of the adot_autoscaling input (min_capacity, max_capacity, cpu_target_percent, scale_in_cooldown, scale_out_cooldown). Exposed for Terratest assertions against the applied aws_appautoscaling_target / aws_appautoscaling_policy state."
  value       = jsonencode(var.adot_autoscaling)
}

output "vpc_id" {
  description = "The ID of the VPC created by the composed vpc-network module."
  value       = module.vpc_network.vpc_id
}

output "internet_gateway_id" {
  description = "The ID of the Internet Gateway created by the composed vpc-network module (public route gateway, docs/terragrunt-concepts.md)."
  value       = module.vpc_network.internet_gateway_id
}

output "adot_security_group_id" {
  description = "The ID of the ADOT ECS task security group created in-module (docs/terragrunt-concepts.md). Attached to the composed vpc-network VPC; egress-all with OTLP/HTTP ingress on the collector container port from inside the VPC."
  value       = aws_security_group.adot_tasks.id
}

# ---------------------------------------------------------------------------
# ADOT health-check echo outputs for Terratest contract assertions.
# The ALB target-group health check must probe the ADOT health_check extension on
# adot_health_check_port (13133) at path "/" matcher 200, NOT the OTLP/HTTP receiver
# on adot_container_port (4318) which returns 404 on "/". These expose the configured
# health-check values + the task SG ingress ports so the suite asserts the fix without
# deploying to live AWS.
# ---------------------------------------------------------------------------

output "adot_health_check_port_echo" {
  description = "Echo of adot_health_check_port. The ALB target-group health check port and the ADOT health_check extension bind port (0.0.0.0:<port>). Default 13133."
  value       = tostring(var.adot_health_check_port)
}

output "adot_target_group_health_check_path_echo" {
  description = "Echo of the ALB target-group health check path. Must be \"/\" (the basic ADOT health_check extension serves 200 on \"/\"), never the OTLP receiver path /health/status."
  value       = local.adot_target_group_health_check.path
}

output "adot_target_group_health_check_port_echo" {
  description = "Echo of the ALB target-group health check port. Must equal adot_health_check_port (13133), never \"traffic-port\" (which resolves to the 4318 OTLP target port)."
  value       = local.adot_target_group_health_check.port
}

output "adot_target_group_health_check_matcher_echo" {
  description = "Echo of the ALB target-group health check success matcher. Must be \"200\" (the basic health_check extension returns 200 on \"/\" when healthy)."
  value       = local.adot_target_group_health_check.matcher
}

output "adot_security_group_ingress_ports_echo" {
  description = "Comma-separated sorted list of the TCP ingress from_port values on the in-module ADOT task security group. Must include both adot_container_port (4318 OTLP/HTTP) and adot_health_check_port (13133 health check) so the internal ALB can reach both."
  value       = join(",", sort([for r in aws_security_group.adot_tasks.ingress : tostring(r.from_port)]))
}

# ---------------------------------------------------------------------------
# Telemetry CloudWatch Logs ingest hop outputs.
# ---------------------------------------------------------------------------

output "telemetry_log_group_name" {
  description = "Name of the dedicated telemetry CloudWatch Logs ingest group that the awscloudwatchlogs ADOT exporter writes to and the subscription filter forwards to the data-lake Firehose."
  value       = aws_cloudwatch_log_group.telemetry_ingest.name
}

output "telemetry_log_group_arn" {
  description = "ARN of the dedicated telemetry CloudWatch Logs ingest group. Scoped (with a :* suffix) in the ADOT task role inline policy logs:CreateLogStream/PutLogEvents/DescribeLogStreams statement (DescribeLogStreams is required by the awsemf structured-OTLP metrics exporter)."
  value       = aws_cloudwatch_log_group.telemetry_ingest.arn
}

output "cwl_to_firehose_role_arn" {
  description = "ARN of the IAM role CloudWatch Logs assumes to deliver the telemetry ingest group's subscription-filter records to the data-lake Firehose."
  value       = aws_iam_role.cwl_to_firehose.arn
}

output "cwl_to_firehose_trust_source_arns_echo" {
  description = "JSON-encoded list of the aws:SourceArn values in the CWL-to-Firehose role trust policy ArnLike condition. Must contain BOTH the bare telemetry ingest log-group ARN (the form CloudWatch Logs presents during PutSubscriptionFilter's creation-time test-message delivery) AND that ARN with the trailing ':*' segment (the form presented during runtime delivery), so the subscription filter both CREATES and DELIVERS to Firehose. Exposed for Terratest assertions (BUG-2)."
  value       = jsonencode(local.cwl_to_firehose_trust_source_arns)
}

output "telemetry_subscription_filter_destination_arn" {
  description = "Echo of the subscription filter destination (the data-lake Firehose ARN). Exposed for Terratest assertions confirming the match-all filter targets the existing Firehose."
  value       = aws_cloudwatch_log_subscription_filter.telemetry_ingest_to_firehose.destination_arn
}

# ---------------------------------------------------------------------------
# Accepted interface inputs -- surfaced for caller introspection and to assert
# interface stability. These inputs are accepted at the module interface for
# call-site symmetry / toggles that this module does not consume internally.
# ---------------------------------------------------------------------------

output "accepted_inputs" {
  description = "Inputs accepted at the module interface for call-site symmetry / toggles that this module does not consume internally; surfaced for caller introspection and to assert interface stability."
  value = {
    adot_image                        = var.adot_image
    log_group_name                    = var.log_group_name
    execution_role_assume_policy_json = var.execution_role_assume_policy_json
    task_role_assume_policy_json      = var.task_role_assume_policy_json
    enable_custom_domain              = var.enable_custom_domain
    adot_config_ssm_param_name        = var.adot_config_ssm_param_name
    waf_allow_cidrs_ssm_param_name    = var.waf_allow_cidrs_ssm_param_name
    max_request_body_size             = var.max_request_body_size
  }
}
