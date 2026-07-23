# ---------------------------------------------------------------------------
# Child module source variables -- const=true defaults resolve to in-repo local
# relative paths so terraform init -backend=false succeeds without network access.
# Each variable is declared with const=true to prevent callers from overriding
# the canonical in-repo source at plan time. (E9-F1-S1-T4 const-source convention.)
# ---------------------------------------------------------------------------

variable "is_active" {
  type        = bool
  description = "(Required) Whether this instance set is the ACTIVE set. The pretty-name alias is attached to the ACTIVE set's CloudFront distribution ONLY (CloudFront aliases are unique per distribution); inactive/candidate sets serve only their own set-scoped real name. Promote flips this and moves the pretty alias. The cert keeps the pretty SAN on every set so it is ready to take traffic."
}

variable "vpc_network_source" {
  type        = string
  const       = true
  description = "Source path for the vpc-network reference child module. Defaults to the in-repo relative path."
  default     = "../vpc-network"
}

variable "ecs_cluster_source" {
  type        = string
  const       = true
  description = "Source path for the ecs-app-cluster reference child module. Defaults to the in-repo relative path."
  default     = "../ecs-app-cluster"
}

variable "ecs_deploy_source" {
  type        = string
  const       = true
  description = "Source path for the ecs-app-deploy reference child module. Defaults to the in-repo relative path."
  default     = "../ecs-app-deploy"
}

variable "waf_webacl_source" {
  type        = string
  const       = true
  description = "Source path for the waf-webacl primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/waf-webacl"
}

variable "cloudfront_source" {
  type        = string
  const       = true
  description = "Source path for the cloudfront-distribution primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/cloudfront-distribution"
}

variable "route53_record_source" {
  type        = string
  const       = true
  description = "Source path for the route53-record primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/route53-record"
}

# ---------------------------------------------------------------------------
# D37 inputs -- all declared as inputs, never as computed outputs.
# These break the potential output-fed-as-input cycle (spec D37).
# ---------------------------------------------------------------------------

variable "firehose_delivery_stream_arn" {
  type        = string
  description = "(Required) ARN of the Firehose delivery stream sourced from the data-lake reference output. Declared as an INPUT per D37 -- never a computed output of this reference."

  validation {
    condition     = can(regex("^arn:aws:firehose:[a-z0-9-]+:[0-9]{12}:deliverystream/", var.firehose_delivery_stream_arn))
    error_message = "firehose_delivery_stream_arn must be a valid Firehose delivery stream ARN."
  }
}

variable "collector_service_fqdn" {
  type        = string
  description = "(Required) Per-set service FQDN of the collector (e.g. collector-000.<env>.platform...). Attached as a CloudFront viewer alias AND used as the CloudFront VPC-origin domain_name. With a VPC origin, CloudFront routes to the internal ALB privately by the VPC origin (the ALB ARN), NOT by public DNS resolution of this name, so the fact that this name is a Route53 A-alias to the same distribution does NOT create an origin self-loop; the name is used only for the Host header and TLS SNI/certificate validation, so the ALB's HTTPS-listener certificate must cover it. Declared as an INPUT per D37."

  validation {
    condition     = length(var.collector_service_fqdn) > 0
    error_message = "collector_service_fqdn must be a non-empty string."
  }
}

variable "collector_pretty_fqdn" {
  type        = string
  description = "(Required) Human-friendly FQDN for the collector endpoint (e.g. collector.example.com). Used as the CloudFront alias and route53-record value. Declared as an INPUT per D37."

  validation {
    condition     = length(var.collector_pretty_fqdn) > 0
    error_message = "collector_pretty_fqdn must be a non-empty string."
  }
}

variable "prod_hosted_zone_id" {
  type        = string
  description = "(Required) Route53 hosted zone ID of the production DNS zone. Declared as an INPUT per D37."

  validation {
    condition     = can(regex("^Z[A-Z0-9]+$", var.prod_hosted_zone_id))
    error_message = "prod_hosted_zone_id must be a valid Route53 hosted zone ID (starts with Z followed by uppercase alphanumerics)."
  }
}

variable "certificate_arn" {
  type        = string
  description = "(Required) ARN of the ACM certificate issued by the acm-collector module. This reference performs certificate VALIDATION only -- per docs/terragrunt-concepts.md, certificates are created exclusively by the acm-collector module. Declared as an INPUT per D37."

  validation {
    condition     = can(regex("^arn:aws:acm:[a-z0-9-]+:[0-9]{12}:certificate/", var.certificate_arn))
    error_message = "certificate_arn must be a valid ACM certificate ARN."
  }
}

variable "domain_validation_options" {
  type = list(object({
    domain_name           = string
    resource_record_name  = string
    resource_record_type  = string
    resource_record_value = string
  }))
  description = "(Required) List of domain validation options from the ACM certificate. Passed to aws_acm_certificate_validation. Declared as an INPUT per D37 -- this reference never creates certificates."

  validation {
    condition     = length(var.domain_validation_options) > 0
    error_message = "domain_validation_options must contain at least one validation record."
  }
}

# ---------------------------------------------------------------------------
# docs/terragrunt-concepts.md ADOT runtime inputs (D44 concrete defaults).
# ---------------------------------------------------------------------------

variable "adot_image" {
  type        = string
  description = "(Required) Docker image URI for the ADOT collector (e.g. public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1). Must be pinned to a specific digest or tag."

  validation {
    condition     = length(var.adot_image) > 0
    error_message = "adot_image must be a non-empty Docker image URI."
  }
}

variable "adot_task_cpu" {
  type        = number
  description = "(Optional) CPU units for the ADOT ECS task. Must be a valid Fargate CPU value. Defaults to 1024 -- right-sized (from the prior 512 D44 default) for the ~2000-concurrent-session high-concurrency ingestion target; terragrunt sets the real per-env value."
  default     = 1024

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096], var.adot_task_cpu)
    error_message = "adot_task_cpu must be a valid Fargate CPU value: 256, 512, 1024, 2048, or 4096."
  }
}

variable "adot_task_memory" {
  type        = number
  description = "(Optional) Memory (MiB) for the ADOT ECS task. Must be a valid Fargate combination. Defaults to 2048 -- right-sized (from the prior 1024 D44 default) for the ~2000-concurrent-session high-concurrency ingestion target; terragrunt sets the real per-env value. Must exceed memory_limiter_limit_mib (the memory_limiter processor's hard cap must leave headroom below the task's hard OOM limit)."
  default     = 2048

  validation {
    condition     = var.adot_task_memory >= 512
    error_message = "adot_task_memory must be at least 512 MiB."
  }
}

variable "adot_receiver_max_request_body_size" {
  type        = number
  description = "(Optional) Maximum OTLP HTTP request body size in bytes enforced by the ADOT receiver (D5). Shipped into the AOT_CONFIG_CONTENT SSM SecureString. Concrete default 4194304 bytes (4 MiB) per D44."
  default     = 4194304

  validation {
    condition     = var.adot_receiver_max_request_body_size > 0
    error_message = "adot_receiver_max_request_body_size must be a positive integer in bytes."
  }
}

variable "memory_limiter_limit_mib" {
  type        = number
  description = "(Optional) Hard memory limit (MiB) for the ADOT memory_limiter processor (D5). Traffic is dropped when memory exceeds this threshold. Must be less than adot_task_memory. Defaults to 1800 -- raised proportionally (from the prior 900 D44 default) for the adot_task_memory 2048 MiB right-sized default, leaving headroom below the task's hard Fargate OOM limit."
  default     = 1800

  validation {
    condition     = var.memory_limiter_limit_mib > 0
    error_message = "memory_limiter_limit_mib must be a positive integer in MiB."
  }

  validation {
    condition     = var.memory_limiter_limit_mib < var.adot_task_memory
    error_message = "memory_limiter_limit_mib must be less than adot_task_memory so the collector process throttles before Fargate OOM-kills the task."
  }
}

variable "memory_limiter_spike_limit_mib" {
  type        = number
  description = "(Optional) Soft spike limit (MiB) for the ADOT memory_limiter processor (D5). The processor starts throttling when memory exceeds (limit_mib - spike_limit_mib). Must be less than memory_limiter_limit_mib. Defaults to 400 -- raised proportionally (from the prior 200 D44 default) alongside memory_limiter_limit_mib for the larger 2048 MiB right-sized task."
  default     = 400

  validation {
    condition     = var.memory_limiter_spike_limit_mib > 0
    error_message = "memory_limiter_spike_limit_mib must be a positive integer in MiB."
  }

  validation {
    condition     = var.memory_limiter_spike_limit_mib < var.memory_limiter_limit_mib
    error_message = "memory_limiter_spike_limit_mib must be less than memory_limiter_limit_mib."
  }
}

# ---------------------------------------------------------------------------
# ADOT ECS scaling inputs (high-concurrency ingestion sizing).
#
# ECS Application Auto Scaling reacts on a multi-minute cadence (a CloudWatch
# alarm must breach, then the scaling policy acts, then new tasks must pull the
# image and pass the ALB health check) -- far slower than an OTLP traffic burst.
# adot_desired_count is therefore the PRIMARY lever for a known concurrency
# target (e.g. ~2000 concurrent Claude Code sessions): its default is set to
# carry the full expected baseline load so the service does not depend on
# scale-out latency to keep up under normal conditions. Autoscaling
# (adot_enable_autoscaling / adot_autoscaling) is the elastic cushion above
# that baseline (traffic spikes beyond the baseline) and the cost lever below
# it (scale-in during off-hours) -- not the primary capacity mechanism.
# ---------------------------------------------------------------------------

variable "adot_desired_count" {
  type        = number
  description = "(Optional) Initial/baseline desired task count for the ADOT ECS service, passed straight through to the composed ecs-app-deploy (module.adot_service) desired_count input. This is the PRIMARY capacity lever: ECS Application Auto Scaling reacts on a multi-minute cadence (alarm breach -> scaling action -> task startup -> ALB health check), so the baseline count -- not scale-out -- must already carry the expected steady-state concurrent load (e.g. ~2000 concurrent Claude Code sessions). Defaults to 4, a generous baseline sized for that target load; terragrunt sets the real per-env value. When adot_enable_autoscaling is true, ECS Application Auto Scaling manages the running count between adot_autoscaling.min_capacity and adot_autoscaling.max_capacity thereafter, and this value must fall within that range so the initial deployment is coherent with the scaling policy from the first apply."
  default     = 4

  validation {
    condition     = var.adot_desired_count >= 1
    error_message = "adot_desired_count must be at least 1."
  }

  validation {
    condition = !var.adot_enable_autoscaling || (
      var.adot_desired_count >= var.adot_autoscaling.min_capacity &&
      var.adot_desired_count <= var.adot_autoscaling.max_capacity
    )
    error_message = "adot_desired_count must fall within [adot_autoscaling.min_capacity, adot_autoscaling.max_capacity] when adot_enable_autoscaling is true, so the initial ECS desired count never conflicts with the Application Auto Scaling target range."
  }
}

variable "adot_enable_autoscaling" {
  type        = bool
  description = "(Optional) Whether to enable ECS Application Auto Scaling (CPU target-tracking) for the ADOT service, passed straight through to the composed ecs-app-deploy (module.adot_service) enable_autoscaling input -- which the ecs-service primitive already implements via aws_appautoscaling_target/aws_appautoscaling_policy with predefined_metric_type = ECSServiceAverageCPUUtilization. Defaults to true: the collector is a public high-concurrency ingestion edge, so elastic headroom above the adot_desired_count baseline (and scale-in during off-hours) is the secure/cost-aware default. When true, adot_autoscaling must be non-null (enforced by the composed ecs-service primitive's fail-fast precondition)."
  default     = true
}

variable "adot_autoscaling" {
  type = object({
    min_capacity       = number
    max_capacity       = number
    cpu_target_percent = optional(number, 60)
    scale_in_cooldown  = optional(number, 300)
    scale_out_cooldown = optional(number, 60)
  })
  description = "(Optional) ECS Application Auto Scaling configuration for the ADOT service, passed straight through to the composed ecs-app-deploy (module.adot_service) autoscaling input (CPU target-tracking via ECSServiceAverageCPUUtilization). Required (non-null) when adot_enable_autoscaling is true. min_capacity should carry the FULL expected concurrent load on its own (e.g. ~2000 concurrent Claude Code sessions): ECS scale-out latency is on the order of minutes (alarm breach, scaling action, task startup, ALB health check), so the running floor -- not a future scale-out event -- is the primary capacity guarantee; autoscaling above min_capacity is the elastic cushion for bursts and max_capacity is the ceiling, not the sizing target. Defaults to a generous baseline: min_capacity 4, max_capacity 12, cpu_target_percent 50 (scales out earlier than the ecs-app-deploy default of 60 so headroom is reserved before CPU saturates), scale_in_cooldown 300s, scale_out_cooldown 60s."
  default = {
    min_capacity       = 4
    max_capacity       = 12
    cpu_target_percent = 50
  }

  validation {
    condition     = var.adot_autoscaling.max_capacity >= var.adot_autoscaling.min_capacity
    error_message = "adot_autoscaling.max_capacity must be >= adot_autoscaling.min_capacity."
  }

  validation {
    condition     = var.adot_autoscaling.min_capacity >= 1
    error_message = "adot_autoscaling.min_capacity must be at least 1."
  }
}

# ---------------------------------------------------------------------------
# ADOT exporter throughput inputs (high-concurrency ingestion sizing).
#
# The awscloudwatchlogs and awscloudwatchlogs/structured exporters in
# local.adot_config_content (locals.tf) each declare a sending_queue -- an
# in-memory buffer of batches awaiting delivery to CloudWatch Logs -- and the
# shared "batch" processor groups individual OTLP records into batches before
# they reach the exporters. Both were previously unsized (sending_queue =
# { enabled = true }, batch = {}), which is fine at low volume but under
# ~2000-concurrent-session ingestion an undersized queue/batch causes the
# exporter to apply backpressure (and, if the queue fills, drop data) well
# before the task's CPU or memory limits are reached. These inputs make that
# throughput budget explicit and input-driven rather than relying on the
# ADOT/OTel Collector Contrib built-in defaults.
# ---------------------------------------------------------------------------

variable "adot_exporter_sending_queue_size" {
  type        = number
  description = "(Optional) sending_queue.queue_size (buffered batches awaiting delivery) for the awscloudwatchlogs and awscloudwatchlogs/structured exporters in the rendered AOT_CONFIG_CONTENT. Sized for high-concurrency ingestion so a delivery slowdown does not immediately apply backpressure to the OTLP receiver. Defaults to 10000."
  default     = 10000

  validation {
    condition     = var.adot_exporter_sending_queue_size > 0
    error_message = "adot_exporter_sending_queue_size must be a positive integer."
  }
}

variable "adot_exporter_sending_queue_num_consumers" {
  type        = number
  description = "(Optional) sending_queue.num_consumers (parallel goroutines draining the sending queue to CloudWatch Logs) for the awscloudwatchlogs and awscloudwatchlogs/structured exporters in the rendered AOT_CONFIG_CONTENT. Higher concurrency drains the queue faster under high-concurrency ingestion. Defaults to 8."
  default     = 8

  validation {
    condition     = var.adot_exporter_sending_queue_num_consumers > 0
    error_message = "adot_exporter_sending_queue_num_consumers must be a positive integer."
  }
}

variable "adot_batch_send_batch_size" {
  type        = number
  description = "(Optional) send_batch_size (number of OTLP records grouped per batch before export) for the shared \"batch\" processor in the rendered AOT_CONFIG_CONTENT (the logs and logs/structured pipelines). Larger batches reduce the number of PutLogEvents calls under high-concurrency ingestion. Defaults to 8192."
  default     = 8192

  validation {
    condition     = var.adot_batch_send_batch_size > 0
    error_message = "adot_batch_send_batch_size must be a positive integer."
  }
}

variable "adot_batch_timeout_seconds" {
  type        = number
  description = "(Optional) timeout, in seconds, that bounds how long the shared \"batch\" processor in the rendered AOT_CONFIG_CONTENT waits to fill a batch to adot_batch_send_batch_size before flushing anyway. Rendered as \"<n>s\" in the ADOT config. Defaults to 5."
  default     = 5

  validation {
    condition     = var.adot_batch_timeout_seconds > 0
    error_message = "adot_batch_timeout_seconds must be a positive integer number of seconds."
  }
}

variable "enable_flow_logs" {
  type        = bool
  description = "(Optional) Whether to enable VPC Flow Logs for the composed vpc-network module. Defaults to true (secure-by-default) so VPC traffic is auditable; requires flow_logs_iam_role_arn and flow_logs_destination_arn to be supplied."
  default     = true
}

variable "flow_logs_iam_role_arn" {
  type        = string
  description = "(Optional) ARN of the IAM role VPC Flow Logs uses to publish to the destination. Required when enable_flow_logs is true. Must match ^arn:aws:iam: when provided."
  default     = null

  validation {
    condition     = var.flow_logs_iam_role_arn == null || can(regex("^arn:aws:iam:", var.flow_logs_iam_role_arn))
    error_message = "flow_logs_iam_role_arn must be a valid IAM role ARN matching ^arn:aws:iam: when provided."
  }
}

variable "flow_logs_destination_arn" {
  type        = string
  description = "(Optional) ARN of the CloudWatch Logs group or S3 bucket that receives VPC Flow Logs. Required when enable_flow_logs is true. Must match ^arn:aws: when provided."
  default     = null

  validation {
    condition     = var.flow_logs_destination_arn == null || can(regex("^arn:aws:", var.flow_logs_destination_arn))
    error_message = "flow_logs_destination_arn must be a valid ARN matching ^arn:aws: when provided."
  }
}

variable "access_log_bucket_name" {
  type        = string
  description = "(Required) Name of the centralized access-log destination S3 bucket that receives the CloudFront standard access logs for the collector distribution. CloudFront standard logging requires an SSE-S3 (AES256), ACL-enabled destination bucket, so this destination is provisioned and encrypted outside this module and referenced here only by name. Must follow S3 naming conventions."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.access_log_bucket_name))
    error_message = "access_log_bucket_name must be 3-63 characters, start and end with a lowercase letter or digit, and contain only lowercase letters, digits, hyphens, and dots."
  }
}

variable "adot_egress_cidr_blocks" {
  type        = list(string)
  description = "(Optional) Egress destination CIDR blocks for the ADOT collector task security group. When null (the default), egress is restricted to the VPC CIDR (var.vpc_cidr_block) because the collector reaches Firehose, CloudWatch Logs, SSM, ECR, and S3 through in-VPC endpoints; this is the secure default. Supply an explicit list (for example [\"0.0.0.0/0\"]) only when the collector must reach an endpoint without a VPC endpoint, such as pulling a public ECR image over NAT."
  default     = null

  validation {
    condition     = var.adot_egress_cidr_blocks == null || length(var.adot_egress_cidr_blocks) > 0
    error_message = "adot_egress_cidr_blocks must be null (VPC-scoped default) or a non-empty list of CIDR blocks."
  }
}

variable "rate_limit_per_ip" {
  type        = number
  description = "(Optional) WAF rate limit per source IP per 5-minute window (D44). Applied to the REUSED waf-webacl module. Concrete default 2000 per D44."
  default     = 2000

  validation {
    condition     = var.rate_limit_per_ip >= 100
    error_message = "rate_limit_per_ip must be at least 100 requests per 5-minute window."
  }
}

variable "vpc_cidr_block" {
  type        = string
  description = "(Required) CIDR block for the VPC created by the composed vpc-network module."

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr_block))
    error_message = "vpc_cidr_block must be a valid CIDR block (e.g. 10.0.0.0/16)."
  }
}

variable "subnet_layout" {
  type = list(object({
    name              = string
    cidr_block        = string
    availability_zone = string
    public            = bool
  }))
  description = "(Required) List of subnet definitions for the composed vpc-network module. Each entry must specify name, cidr_block, availability_zone, and whether it is public."

  validation {
    condition     = length(var.subnet_layout) >= 2
    error_message = "subnet_layout must declare at least 2 subnets across availability zones."
  }
}

variable "waf_log_kms_key_arn" {
  type        = string
  description = "(Required) ARN of the telemetry-data KMS CMK used to encrypt WAF logs (docs/terragrunt-concepts.md). Must be set to the telemetry-data CMK ARN sourced from the data-lake reference."

  validation {
    condition     = can(regex("^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/", var.waf_log_kms_key_arn))
    error_message = "waf_log_kms_key_arn must be a valid KMS key ARN."
  }
}

variable "waf_log_retention_in_days" {
  type        = number
  description = "(Optional) Retention in days for the collector WAF's module-owned CloudWatch log group (docs/terragrunt-concepts.md). The reused waf-webacl creates its own aws-waf-logs-* log group (create_log_group = true), so no external WAF log destination ARN is required. Must be a valid CloudWatch retention value. Defaults to 365 (docs/terragrunt-concepts.md)."
  default     = 365

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.waf_log_retention_in_days)
    error_message = "waf_log_retention_in_days must be one of the valid CloudWatch retention values: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653."
  }
}

# ---------------------------------------------------------------------------
# Telemetry CloudWatch Logs ingest hop inputs.
#
# The awscloudwatchlogs ADOT exporter writes to a dedicated telemetry log group
# (/telemetry/<env>/ingest/otlp-logs) created by this reference. A match-all
# subscription filter on that group forwards records to the existing data-lake
# Firehose (firehose_delivery_stream_arn) via a dedicated CWL-to-Firehose role.
# ---------------------------------------------------------------------------

variable "telemetry_log_kms_key_arn" {
  type        = string
  description = "(Required) ARN of the telemetry-data KMS CMK used to encrypt the dedicated telemetry CloudWatch Logs ingest group (/telemetry/<env>/ingest/otlp-logs). Set to the telemetry-data CMK ARN sourced from the data-lake reference (the same CMK as waf_log_kms_key_arn). The data-lake CMK policy already grants logs.<region>.amazonaws.com via the ArnLike kms:EncryptionContext:aws:logs:arn condition covering arn:aws:logs:<region>:<account>:log-group:*, so no KMS policy change is required for this group."

  validation {
    condition     = can(regex("^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/", var.telemetry_log_kms_key_arn))
    error_message = "telemetry_log_kms_key_arn must be a valid KMS key ARN."
  }
}

variable "telemetry_log_retention_in_days" {
  type        = number
  description = "(Optional) Retention in days for the dedicated telemetry CloudWatch Logs ingest group. The durable copy lives in the Parquet data lake, so this is a transient hop; defaults to 1 for cost mitigation. Also passed to the awscloudwatchlogs exporter log_retention so the exporter's PutRetentionPolicy matches the Terraform-managed group. Must be a valid CloudWatch retention value."
  default     = 1

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.telemetry_log_retention_in_days)
    error_message = "telemetry_log_retention_in_days must be one of the valid CloudWatch retention values: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653."
  }
}

variable "structured_otlp_service_names" {
  type        = list(string)
  description = "(Optional) OTLP resource.service.name values (e.g. claude-code, claude-cowork, claude-office) whose STRUCTURED log records are routed to a second logs pipeline exported with raw_log=false, so the log-record attributes (marketplaces/plugins/skills/MCPs usage) survive for the data-lake cwl_split transform to reshape. Every other service (example-cli and any flat-body top-level-'tool' contract tool) stays on the existing raw_log=true path, byte-unchanged. Empty (the default) is inert: the collector behaves exactly as today until an env supplies these names. The data-lake module's service_tool_map must map each of these to a 'tool' partition value."
  default     = []

  validation {
    condition     = alltrue([for s in var.structured_otlp_service_names : can(regex("^[a-zA-Z0-9._-]+$", s))])
    error_message = "each structured_otlp_service_names entry must be non-empty and contain only alphanumeric characters, dots, underscores, or hyphens (it is an OTLP service.name matched in an OTTL filter condition)."
  }
}

variable "claude_metrics_emf_namespace" {
  type        = string
  description = "(Optional) CloudWatch namespace the awsemf exporter publishes structured-OTLP tool metrics under (the metrics/structured pipeline). The pipeline is gated by the SAME structured_otlp_service_names input as the logs/structured pipeline (empty is inert -- the keep-condition drops every datapoint), so this namespace value is inert too until structured_otlp_service_names is non-empty. Defaults to \"Telemetry/ClaudeCode\" -- the variable name and default are kept as-is to avoid a CloudWatch-namespace migration; the namespace now covers every structured-OTLP tool, not only Claude."
  default     = "Telemetry/ClaudeCode"

  validation {
    condition     = length(var.claude_metrics_emf_namespace) > 0
    error_message = "claude_metrics_emf_namespace must be a non-empty string."
  }
}

variable "cwl_to_firehose_role_name" {
  type        = string
  description = "(Required) Name of the IAM role CloudWatch Logs assumes to deliver subscription-filter records to the data-lake Firehose. Trusts logs.<region>.amazonaws.com and grants firehose:PutRecord/PutRecordBatch on firehose_delivery_stream_arn only. SEPARATE from the ADOT task role and the Firehose delivery role. Must be 1-64 characters (IAM role name limit)."

  validation {
    condition     = length(var.cwl_to_firehose_role_name) >= 1 && length(var.cwl_to_firehose_role_name) <= 64
    error_message = "cwl_to_firehose_role_name must be 1-64 characters to satisfy the IAM role name limit."
  }
}

# ---------------------------------------------------------------------------
# vpc-network interface inputs -- required by the vpc-network reference module.
# ---------------------------------------------------------------------------

variable "nat_gateways" {
  type = list(object({
    name               = string
    public_subnet_name = string
    connectivity_type  = optional(string, "public")
  }))
  description = "(Required) List of NAT gateway definitions for the composed vpc-network module. One entry per AZ for HA (e.g. one per public subnet)."

  validation {
    condition     = length(var.nat_gateways) > 0
    error_message = "nat_gateways must contain at least one entry."
  }
}

variable "interface_endpoint_service_names" {
  type        = list(string)
  description = "(Required) List of AWS service names for Interface-type VPC endpoints (e.g. com.amazonaws.us-east-1.ssm). Passed to the composed vpc-network module."

  validation {
    condition     = length(var.interface_endpoint_service_names) > 0
    error_message = "interface_endpoint_service_names must contain at least one entry."
  }
}

variable "interface_endpoint_subnet_names" {
  type        = list(string)
  description = "(Required) List of private subnet names to attach to Interface endpoints. Passed to the composed vpc-network module."

  validation {
    condition     = length(var.interface_endpoint_subnet_names) > 0
    error_message = "interface_endpoint_subnet_names must contain at least one entry."
  }
}

variable "s3_gateway_endpoint_service_name" {
  type        = string
  description = "(Required) AWS service name for the S3 Gateway endpoint (e.g. com.amazonaws.us-east-1.s3). Passed to the composed vpc-network module."

  validation {
    condition     = length(var.s3_gateway_endpoint_service_name) > 0
    error_message = "s3_gateway_endpoint_service_name must be a non-empty string."
  }
}

# ---------------------------------------------------------------------------
# ADOT service network inputs
# ---------------------------------------------------------------------------

variable "adot_container_port" {
  type        = number
  description = "(Optional) Container port the ADOT collector OTLP/HTTP receiver listens on. Used for the ALB target group port and the in-module ADOT task security group ingress. Defaults to 4318 (OTLP/HTTP)."
  default     = 4318

  validation {
    condition     = var.adot_container_port > 0 && var.adot_container_port <= 65535
    error_message = "adot_container_port must be a valid TCP port between 1 and 65535."
  }
}

variable "adot_health_check_port" {
  type        = number
  description = "(Optional) TCP port the ADOT collector health_check extension binds (0.0.0.0:<port>) and the ALB target-group health check probes on path \"/\". The OTLP/HTTP receiver (adot_container_port) does not serve a health path, so the ALB must probe the health_check extension's port instead -- otherwise the target reports Target.ResponseCodeMismatch [404] and the ECS task cycles. The basic health_check extension serves HTTP 200 on \"/\" when the pipeline is healthy. Added to the ADOT container portMappings and the in-module ADOT task security group ingress so the internal ALB can reach it. Defaults to 13133 (the ADOT health_check extension default port)."
  default     = 13133

  validation {
    condition     = var.adot_health_check_port > 0 && var.adot_health_check_port <= 65535
    error_message = "adot_health_check_port must be a valid TCP port between 1 and 65535."
  }
}

variable "alb_ssl_policy" {
  type        = string
  description = "(Optional) TLS security policy for the ALB HTTPS listener. Required by the alb-listener primitive for HTTPS listeners. Defaults to a current TLS 1.3 ELB security policy."
  default     = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  validation {
    condition     = length(var.alb_ssl_policy) > 0
    error_message = "alb_ssl_policy must be a non-empty ELB security policy name."
  }
}

variable "alb_https_listener_port" {
  type        = number
  description = "(Optional) TCP port of the internal ALB HTTPS listener that CloudFront reaches via the VPC origin. Single source of truth wired into BOTH the ALB HTTPS listener and the CloudFront VPC origin endpoint https_port AND the internal ALB security-group ingress rule (so the ALB opens exactly the listener port to the CloudFront origin-facing prefix list). Defaults to 443."
  default     = 443

  validation {
    condition     = var.alb_https_listener_port > 0 && var.alb_https_listener_port <= 65535
    error_message = "alb_https_listener_port must be a valid TCP port between 1 and 65535."
  }
}

# ---------------------------------------------------------------------------
# CloudFront VPC origin edge input.
#
# The collector CloudFront distribution reaches the INTERNAL ALB through a
# CloudFront VPC origin (primitives/cloudfront-distribution origin_type = "vpc"),
# so the ALB never has to be internet-facing and there is no CloudFront->CloudFront
# origin self-loop. CloudFront origin-facing traffic to the ALB is sourced from the
# AWS-managed prefix list com.amazonaws.global.cloudfront.origin-facing, so the
# internal ALB security group allows inbound from that prefix list on the HTTPS
# listener port. The prefix list id is REGION-specific and AWS-managed; it is supplied
# as an INPUT (not discovered via a data "aws_ec2_managed_prefix_list" lookup) so this
# module performs NO plan-time AWS read (the dns-owner cross-account terragrunt plan
# must succeed without ec2:DescribeManagedPrefixLists access). Source it in the
# terragrunt leaf from a region-keyed config value (e.g. common/cloudfront.json).
# ---------------------------------------------------------------------------
variable "cloudfront_origin_facing_prefix_list_id" {
  type        = string
  description = "(Required) ID of the AWS-managed CloudFront origin-facing prefix list (com.amazonaws.global.cloudfront.origin-facing) for this region. Allowed as inbound on the internal ALB security group (HTTPS listener port) so CloudFront can reach the ALB via the VPC origin. Region-specific AWS-managed value (e.g. pl-3b927c52 in us-east-1), supplied as an input so the module performs no plan-time AWS read."

  validation {
    condition     = can(regex("^pl-[0-9a-f]+$", var.cloudfront_origin_facing_prefix_list_id))
    error_message = "cloudfront_origin_facing_prefix_list_id must be a managed prefix list ID matching ^pl-[0-9a-f]+ (the CloudFront origin-facing prefix list for this region)."
  }
}

# ---------------------------------------------------------------------------
# Route53 record inputs
# ---------------------------------------------------------------------------

variable "route53_record_ttl" {
  type        = number
  description = "(Optional) TTL in seconds for the Route53 CNAME record pointing the collector pretty FQDN to the CloudFront distribution domain. Defaults to 300 seconds."
  default     = 300

  validation {
    condition     = var.route53_record_ttl > 0
    error_message = "route53_record_ttl must be a positive integer in seconds."
  }
}

# ---------------------------------------------------------------------------
# Record-ownership toggle (single-owner DNS, AC-8 / D39)
# ---------------------------------------------------------------------------

variable "create_public_dns_record" {
  type        = bool
  description = "(Optional) Whether this reference creates the public DNS record pointing the collector pretty FQDN at the CloudFront distribution (a CNAME). Defaults to true so the self-contained standalone example/terratest fixture (which creates its own Route53 zone) owns and provisions the record. Set to false in the LIVE sandbox/prod collector-ingestion leaves, where the dedicated dns-collector unit is the single owner of the public collector hostname record (R4), which it writes as an A ALIAS to the same CloudFront distribution; creating a CNAME for the same name here too would conflict with the A-alias and fail apply (D39, AC-8). The aws_acm_certificate_validation waiter is unaffected by this toggle -- it creates no DNS record and is created in both modes so the collector always consumes an ISSUED certificate."
  default     = true
}

# ---------------------------------------------------------------------------
# Deployment topology inputs
# ---------------------------------------------------------------------------

variable "namespace" {
  type        = string
  description = "(Required) Fully-qualified namespace (product-region-env-envinstance-service-serviceinstance). Used to derive SET-SCOPED resource names (the telemetry ingest CloudWatch log group and the ADOT SSM parameter path) so coexisting instance sets never collide (design law: every resource name is namespace-derived)."

  validation {
    condition     = can(regex("^[a-z0-9_-]+$", var.namespace))
    error_message = "namespace must contain only lowercase letters, digits, hyphens, and underscores."
  }
}

variable "env" {
  type        = string
  description = "(Required) Environment name (e.g. sandbox, qa, prod). Used to construct the ADOT SSM parameter path /telemetry/<env>/ingest/adot-config."

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.env))
    error_message = "env must contain only lowercase letters, digits, and hyphens."
  }
}

variable "vpc_name" {
  type        = string
  description = "(Required) Name for the VPC resource."

  validation {
    condition     = length(var.vpc_name) > 0
    error_message = "vpc_name must be a non-empty string."
  }
}

variable "cluster_name" {
  type        = string
  description = "(Required) Name of the ECS cluster to create for the ADOT service."

  validation {
    condition     = length(var.cluster_name) > 0
    error_message = "cluster_name must be a non-empty string."
  }
}

variable "service_name" {
  type        = string
  description = "(Required) Name of the ADOT ECS service."

  validation {
    condition     = can(regex("^[a-zA-Z0-9_-]+$", var.service_name))
    error_message = "service_name must contain only alphanumeric characters, underscores, and hyphens."
  }
}

variable "task_role_name" {
  type        = string
  description = "(Required) Name of the ECS task IAM role for the ADOT service. Must be 64 characters or fewer."

  validation {
    condition     = length(var.task_role_name) >= 1 && length(var.task_role_name) <= 64
    error_message = "task_role_name must be 1-64 characters to satisfy the IAM role name limit."
  }
}

variable "execution_role_name" {
  type        = string
  description = "(Required) Name of the ECS task execution IAM role. Must be 64 characters or fewer."

  validation {
    condition     = length(var.execution_role_name) >= 1 && length(var.execution_role_name) <= 64
    error_message = "execution_role_name must be 1-64 characters to satisfy the IAM role name limit."
  }
}

variable "alb_name" {
  type        = string
  description = "(Required) Name for the Application Load Balancer."

  validation {
    condition     = can(regex("^[a-zA-Z0-9-]+$", var.alb_name)) && length(var.alb_name) <= 32
    error_message = "alb_name must contain only alphanumeric characters and hyphens, and be 32 characters or fewer."
  }
}

variable "log_group_name" {
  type        = string
  description = "(Required) CloudWatch log group name for ADOT container logs."

  validation {
    condition     = can(regex("^/", var.log_group_name))
    error_message = "log_group_name must start with a forward slash."
  }
}

variable "execution_role_assume_policy_json" {
  type        = string
  description = "(Required) Trust policy JSON for the ECS task execution role."

  validation {
    condition     = can(jsondecode(var.execution_role_assume_policy_json))
    error_message = "execution_role_assume_policy_json must be valid JSON."
  }
}

variable "task_role_assume_policy_json" {
  type        = string
  description = "(Required) Trust policy JSON for the ECS task role."

  validation {
    condition     = can(jsondecode(var.task_role_assume_policy_json))
    error_message = "task_role_assume_policy_json must be valid JSON."
  }
}

variable "task_role_inline_policies" {
  type        = map(string)
  description = "(Optional) Map of inline policy name to JSON document for the ADOT task role."
  default     = {}

  validation {
    condition     = alltrue([for v in values(var.task_role_inline_policies) : can(jsondecode(v))])
    error_message = "Every value in task_role_inline_policies must be a valid JSON string."
  }
}

variable "container_definitions" {
  type        = string
  description = "(Required) JSON-encoded list of container definitions for the ADOT ECS task."

  validation {
    condition     = can(jsondecode(var.container_definitions))
    error_message = "container_definitions must be valid JSON."
  }
}

# ---------------------------------------------------------------------------
# Deployment feature flags (spec section 4.7, AC-FUNC-001)
# ---------------------------------------------------------------------------

variable "enable_custom_domain" {
  type        = bool
  description = "(Optional) Declared to accept the leaf-supplied value from _envcommon/collector-ingestion.hcl so Terraform raises no undeclared-variable warning (D31: sandbox vs prod parity via account.hcl enable_custom_domain). Supplied by _envcommon/collector-ingestion.hcl."
  default     = false
}

# ---------------------------------------------------------------------------
# ADOT SSM configuration inputs (spec section 4.7, AC-FUNC-001)
# ---------------------------------------------------------------------------

variable "adot_config_ssm_param_name" {
  type        = string
  description = "(Optional) SSM SecureString parameter path declared to accept the leaf-supplied value from _envcommon/collector-ingestion.hcl so Terraform raises no undeclared-variable warning."
  default     = ""

  validation {
    condition     = var.adot_config_ssm_param_name == "" || can(regex("^/[a-zA-Z0-9/_-]+[^/]$", var.adot_config_ssm_param_name))
    error_message = "adot_config_ssm_param_name must be empty or a valid SSM parameter path starting with / and not ending with /."
  }
}

variable "waf_allow_cidrs_ssm_param_name" {
  type        = string
  description = "(Optional) SSM parameter name declared to accept the leaf-supplied value from _envcommon/collector-ingestion.hcl so Terraform raises no undeclared-variable warning."
  default     = ""

  validation {
    condition     = var.waf_allow_cidrs_ssm_param_name == "" || can(regex("^/[a-zA-Z0-9/_-]+[^/]$", var.waf_allow_cidrs_ssm_param_name))
    error_message = "waf_allow_cidrs_ssm_param_name must be empty or a valid SSM parameter path starting with / and not ending with /."
  }
}

variable "max_request_body_size" {
  type        = number
  description = "(Optional) Maximum OTLP HTTP request body size in bytes declared to accept the leaf-supplied _envcommon canonical key (D44) so Terraform raises no undeclared-variable warning."
  default     = 0

  validation {
    condition     = var.max_request_body_size >= 0
    error_message = "max_request_body_size must be a non-negative integer in bytes."
  }
}

# ---------------------------------------------------------------------------
# Shared tagging inputs
# ---------------------------------------------------------------------------

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources created by this reference module."
  default     = {}
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Value for the ManagedBy tag."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Value for the Module tag identifying this reference module."
  default     = "collector-ingestion"
}
