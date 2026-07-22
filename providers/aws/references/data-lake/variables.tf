# ---------------------------------------------------------------------------
# Child module source variables (const = true; default to in-repo relative paths).
# Override with a pinned git URL at deploy time via use_pinned_module_sources.
# ---------------------------------------------------------------------------

variable "lake_kms_key_source" {
  type        = string
  const       = true
  description = "Source path for the kms-key primitive used by the lake_kms_key child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use_pinned_module_sources."
  default     = "../../primitives/kms-key"
}

variable "lake_bucket_source" {
  type        = string
  const       = true
  description = "Source path for the s3-bucket primitive used by the lake_bucket child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use_pinned_module_sources."
  default     = "../../primitives/s3-bucket"
}

variable "glue_catalog_source" {
  type        = string
  const       = true
  description = "Source path for the glue-catalog-database primitive used by the glue_catalog child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use_pinned_module_sources."
  default     = "../../primitives/glue-catalog-database"
}

variable "firehose_role_source" {
  type        = string
  const       = true
  description = "Source path for the iam-role primitive used by the firehose_role child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use_pinned_module_sources."
  default     = "../../primitives/iam-role"
}

variable "firehose_source" {
  type        = string
  const       = true
  description = "Source path for the kinesis-firehose-delivery-stream primitive used by the firehose child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use_pinned_module_sources."
  default     = "../../primitives/kinesis-firehose-delivery-stream"
}

# ---------------------------------------------------------------------------
# S3 data lake bucket inputs
# ---------------------------------------------------------------------------

variable "bucket_name" {
  type        = string
  description = "(Required) The name of the S3 data lake bucket. Must be globally unique and follow S3 naming conventions."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be 3-63 characters, start and end with a lowercase letter or digit, and contain only lowercase letters, digits, hyphens, and dots."
  }
}

variable "force_destroy" {
  type        = bool
  description = "(Optional) Whether Terraform may destroy the data lake bucket even when it still contains objects/versions. Propagated to the composed s3-bucket primitive so a `terragrunt destroy` of this unit tears the bucket down without a manual empty step. Defaults to false; the live leaves set true so the env stack is fully destroy/recreate-able from terragrunt alone."
  default     = false
}

variable "transition_storage_class" {
  type        = string
  description = "(Optional) S3 lifecycle transition storage class for cold-tier objects. One of GLACIER, GLACIER_IR, or DEEP_ARCHIVE. Propagated to the composed s3-bucket primitive (AC-11)."
  default     = "GLACIER"

  validation {
    condition     = contains(["GLACIER", "GLACIER_IR", "DEEP_ARCHIVE"], var.transition_storage_class)
    error_message = "transition_storage_class must be one of GLACIER, GLACIER_IR, or DEEP_ARCHIVE."
  }
}

variable "transition_days" {
  type        = number
  description = "(Optional) Number of days after which objects transition to the cold storage class."
  default     = 90

  validation {
    condition     = var.transition_days > 0
    error_message = "transition_days must be a positive integer."
  }
}

variable "expiration_days" {
  type        = number
  description = "(Optional) Number of days after which objects are permanently deleted (AC-11 records-retention expiry leg). Set to null to disable expiry. When set, must be greater than transition_days so objects transition before they expire."
  default     = null

  validation {
    condition     = var.expiration_days == null || var.expiration_days > 0
    error_message = "expiration_days must be a positive integer or null (null disables expiry)."
  }
}

# ---------------------------------------------------------------------------
# Glue catalog database inputs
# ---------------------------------------------------------------------------

variable "glue_database_name" {
  type        = string
  description = "(Required) The name of the Glue catalog database. Must contain only lowercase letters, digits, and underscores."

  validation {
    condition     = can(regex("^[a-z0-9_]+$", var.glue_database_name))
    error_message = "glue_database_name must contain only lowercase letters, digits, and underscores."
  }
}

variable "glue_table_name" {
  type        = string
  description = "(Required) The name of the Glue catalog table used for Firehose format conversion (B4). Must contain only lowercase letters, digits, and underscores."

  validation {
    condition     = can(regex("^[a-z0-9_]+$", var.glue_table_name))
    error_message = "glue_table_name must contain only lowercase letters, digits, and underscores."
  }
}

# ---------------------------------------------------------------------------
# Athena partition projection inputs (BUG-3).
# The Glue table enables Athena partition projection so partitions resolve at
# query time from the S3 key layout (tool=<x>/dt=<yyyy-MM-dd>/) without a crawler
# or registered partitions. The dt projection format and the
# storage.location.template are DERIVED from firehose_prefix (the single source of
# truth for the on-disk layout, locals.tf); these inputs tune the dt
# date-projection range and interval. tool is an enum dimension whose governed set
# of values is input-driven (glue_partition_projection_tool_values), so the table is
# queryable with no tool filter (SELECT * works) while partition pruning stays scoped
# to those tools.
# ---------------------------------------------------------------------------

variable "glue_partition_projection_dt_range" {
  type        = string
  description = "(Optional) Athena date-projection range for the dt partition (projection.dt.range). Bounds the dates Athena projects for dt. Defaults to 'NOW-3YEARS,NOW' (the trailing three years through today). Must be a comma-separated start,end range."
  default     = "NOW-3YEARS,NOW"

  validation {
    condition     = can(regex("^[^,]+,[^,]+$", var.glue_partition_projection_dt_range))
    error_message = "glue_partition_projection_dt_range must be a comma-separated start,end date-projection range (e.g. 'NOW-3YEARS,NOW' or '2023-01-01,NOW')."
  }
}

variable "glue_partition_projection_dt_interval" {
  type        = number
  description = "(Optional) Athena date-projection interval for the dt partition (projection.dt.interval), in units of glue_partition_projection_dt_interval_unit. Defaults to 1 (one day per projected partition, matching the daily dt=yyyy-MM-dd Firehose layout)."
  default     = 1

  validation {
    condition     = var.glue_partition_projection_dt_interval > 0
    error_message = "glue_partition_projection_dt_interval must be a positive integer."
  }
}

variable "glue_partition_projection_dt_interval_unit" {
  type        = string
  description = "(Optional) Athena date-projection interval unit for the dt partition (projection.dt.interval.unit). One of YEARS, MONTHS, WEEKS, DAYS, HOURS, MINUTES, SECONDS. Defaults to DAYS, matching the daily dt=yyyy-MM-dd Firehose partition layout."
  default     = "DAYS"

  validation {
    condition     = contains(["YEARS", "MONTHS", "WEEKS", "DAYS", "HOURS", "MINUTES", "SECONDS"], var.glue_partition_projection_dt_interval_unit)
    error_message = "glue_partition_projection_dt_interval_unit must be one of YEARS, MONTHS, WEEKS, DAYS, HOURS, MINUTES, SECONDS."
  }
}

variable "glue_partition_projection_tool_values" {
  type        = list(string)
  description = "(Optional) The governed set of tool identifiers that report into the telemetry lake, used as the Athena enum partition-projection values for the tool partition (projection.tool.values). enum keeps partition pruning scoped to these tools while leaving the table queryable with no tool filter (SELECT * works), unlike an injected tool column which rejects any query lacking a static WHERE tool='...' equality (CONSTRAINT_VIOLATION). Onboarding a new tool is a reviewed change to this list (see the data-lake README). Defaults to a minimal set (the reserved 'e2e-smoke' value used by the post-deploy pipeline validation, plus 'example-cli'); production passes the full registry-derived tool list via terragrunt."
  default     = ["e2e-smoke", "example-cli"]

  validation {
    condition     = length(var.glue_partition_projection_tool_values) > 0
    error_message = "glue_partition_projection_tool_values must contain at least one tool value."
  }

  validation {
    condition     = alltrue([for t in var.glue_partition_projection_tool_values : can(regex("^[a-zA-Z0-9._-]+$", t))])
    error_message = "each glue_partition_projection_tool_values entry must be non-empty, comma-free, and contain only alphanumeric characters, dots, underscores, or hyphens (values are joined with commas into projection.tool.values)."
  }
}

# ---------------------------------------------------------------------------
# Kinesis Firehose delivery stream inputs
# ---------------------------------------------------------------------------

variable "firehose_stream_name" {
  type        = string
  description = "(Required) The name of the Firehose delivery stream. Must contain only alphanumeric characters, underscores, hyphens, and dots."

  validation {
    condition     = can(regex("^[a-zA-Z0-9_.-]+$", var.firehose_stream_name))
    error_message = "firehose_stream_name must contain only alphanumeric characters, underscores, hyphens, and dots."
  }
}

# ---------------------------------------------------------------------------
# CloudWatch-Logs-split transform Lambda inputs (BUG-6).
# The telemetry Firehose stream's source is a CloudWatch Logs subscription filter (the
# collector-ingestion awscloudwatchlogs exporter -> telemetry log group -> subscription
# filter -> this stream). A single subscription record batches MANY logEvents, and the
# AWS-native unwrap path (Decompression -> CloudWatchLogProcessing -> RecordDeAggregation)
# fails for deliveries larger than 500 events because RecordDeAggregation is hard-capped at
# 500 sub-records per record. This Lambda is wired as the Firehose transform processor: it
# decompresses + envelope-strips each record and re-ingests each logEvents[].message as its
# own single-JSON record via firehose:PutRecordBatch (no 500-record cap), so a delivery of
# any size is split into individual JSON records before MetadataExtraction.
# ---------------------------------------------------------------------------

variable "cwl_transform_lambda_name" {
  type        = string
  description = "(Required) Name of the CloudWatch-Logs-split Firehose transform Lambda. Must be 64 characters or fewer (AWS Lambda function name limit)."

  validation {
    condition     = length(var.cwl_transform_lambda_name) >= 1 && length(var.cwl_transform_lambda_name) <= 64
    error_message = "cwl_transform_lambda_name must be between 1 and 64 characters (AWS Lambda function name limit)."
  }
}

variable "cwl_transform_lambda_role_name" {
  type        = string
  description = "(Required) Name of the IAM execution role for the CloudWatch-Logs-split transform Lambda. Must be 64 characters or fewer (IAM role name limit)."

  validation {
    condition     = length(var.cwl_transform_lambda_role_name) >= 1 && length(var.cwl_transform_lambda_role_name) <= 64
    error_message = "cwl_transform_lambda_role_name must be between 1 and 64 characters (IAM role name limit)."
  }
}

variable "cwl_transform_lambda_runtime" {
  type        = string
  description = "(Optional) Lambda runtime for the CloudWatch-Logs-split transform function. Defaults to python3.12 (the handler uses only the standard library + the runtime-provided boto3)."
  default     = "python3.12"

  validation {
    condition     = can(regex("^python3\\.[0-9]+$", var.cwl_transform_lambda_runtime))
    error_message = "cwl_transform_lambda_runtime must be a python3.x runtime identifier (e.g. python3.12); the handler is implemented in Python."
  }
}

variable "cwl_transform_lambda_timeout" {
  type        = number
  description = "(Optional) Timeout in seconds for the CloudWatch-Logs-split transform Lambda. Sized generously for high-volume re-ingestion (decompress + PutRecordBatch in chunks of 500). Must be between 1 and 900 (AWS allowed range)."
  default     = 120

  validation {
    condition     = var.cwl_transform_lambda_timeout >= 1 && var.cwl_transform_lambda_timeout <= 900
    error_message = "cwl_transform_lambda_timeout must be between 1 and 900 seconds (AWS allowed range)."
  }
}

variable "cwl_transform_lambda_memory_size" {
  type        = number
  description = "(Optional) Memory (MB) for the CloudWatch-Logs-split transform Lambda. Defaults to 512: the Lambda re-ingests each logEvents[].message as its own PutRecordBatch record, so a single invocation holds the fully decompressed + envelope-stripped batch (up to thousands of split records) in memory at once, and Lambda's proportionally-allocated CPU scales with memory_size, so a higher baseline also speeds decompression/PutRecordBatch throughput under high-volume re-ingestion (e.g. concurrent-session bursts). Must be between 128 and 10240 (AWS allowed range); terragrunt sets the real per-env value."
  default     = 512

  validation {
    condition     = var.cwl_transform_lambda_memory_size >= 128 && var.cwl_transform_lambda_memory_size <= 10240
    error_message = "cwl_transform_lambda_memory_size must be between 128 and 10240 MB (AWS allowed range)."
  }
}

variable "cwl_transform_lambda_reserved_concurrent_executions" {
  type        = number
  description = "(Optional) Reserved concurrent executions for the CloudWatch-Logs-split transform Lambda. Reserving concurrency carves out a guaranteed slice of the account's regional concurrency pool for this function so the Firehose transform is never starved by other functions competing for the shared unreserved pool under high-volume re-ingestion (e.g. ~2000 concurrent Claude Code sessions). Defaults to null, which leaves the function drawing from the account's unreserved concurrency pool (no reservation, existing behavior preserved); terragrunt sets the real per-env value. Must be null or a non-negative integer (AWS allows 0, which throttles the function entirely, up to the account's unreserved concurrency)."
  default     = null

  validation {
    condition     = var.cwl_transform_lambda_reserved_concurrent_executions == null || var.cwl_transform_lambda_reserved_concurrent_executions >= 0
    error_message = "cwl_transform_lambda_reserved_concurrent_executions must be null (no reservation) or a non-negative integer."
  }
}

variable "cwl_transform_lambda_log_retention_in_days" {
  type        = number
  description = "(Optional) Retention in days for the CloudWatch-Logs-split transform Lambda's own log group. Must be one of the AWS allowed values."
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.cwl_transform_lambda_log_retention_in_days)
    error_message = "cwl_transform_lambda_log_retention_in_days must be one of the AWS allowed values: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, or 3653."
  }
}

variable "service_tool_map" {
  type        = map(string)
  description = "(Optional) Maps an OTLP resource.service.name (structured-OTLP tools, e.g. Claude Code / Cowork / Office agents, exported by the collector's raw_log=false pipeline) to the lake 'tool' partition value the cwl_split transform assigns. Passed to the Lambda as the SERVICE_TOOL_MAP env var (JSON). Empty (the default) is inert: records that follow the body contract (example-cli and any tool with a top-level 'tool') always pass through byte-identical, so this map only affects structured OTLP records. Every mapped value MUST be a member of glue_partition_projection_tool_values, or its rows land in S3 but are unqueryable in Athena. NO FALLBACK: a structured record whose resource.service.name is not a key in this map fails fast per-record -- the transform Lambda re-ingests its ORIGINAL bytes instead of assigning a catch-all tool, so Firehose routes it to the monitored errors/ prefix (no top-level 'tool') rather than silently dropping it or making it look like a registered tool."
  default     = {}
  validation {
    condition     = alltrue([for v in values(var.service_tool_map) : can(regex("^[a-zA-Z0-9._-]+$", v))])
    error_message = "each service_tool_map value must be non-empty and contain only alphanumeric characters, dots, underscores, or hyphens (it becomes a 'tool' partition value)."
  }

  # Enforce the documented registry contract: every value the cwl_split transform can assign as a
  # 'tool' MUST be an enumerated Glue partition-projection value, or the reshaped
  # raw/tool=<value>/ partitions are written to S3 but never projected -- unqueryable in Athena
  # (silent, accumulating data loss). Fail fast at plan time instead of discovering it in Athena.
  validation {
    condition     = alltrue([for v in values(var.service_tool_map) : contains(var.glue_partition_projection_tool_values, v)])
    error_message = "every service_tool_map value must be a member of glue_partition_projection_tool_values (the Glue tool enum); an unenumerated tool value delivers to S3 but is never projected, so its rows are unqueryable in Athena. Add the value to glue_partition_projection_tool_values (or, in the terragrunt tree, register the tool in common/tool-registry.json so both are derived together)."
  }
}

variable "firehose_dynamic_partitioning_jq" {
  type        = map(string)
  description = "(Optional) JQ expressions for Firehose dynamic-partitioning metadata extraction. Every key/value pair is merged into the single Firehose MetadataExtractionQuery as {key:value,...}, so each value MUST be a valid JQ expression. strftime/timestamp patterns (e.g. \"%Y/%m/%d\") are NOT valid JQ; a single one makes the whole MetadataExtractionQuery fail to compile, so dynamic partitioning fails and every delivered record lands under errors/metadata-extraction-failed/ instead of raw/ (BUG-4). The dt partition is supplied by the Firehose-native !{timestamp:<fmt>} namespace in firehose_prefix, NOT by JQ, so only the tool partition key is extracted here. Defaults to {tool=\".tool\"}, which renders MetadataExtractionQuery {tool:.tool}. Passed through to the composed Firehose primitive (T27, AC-11)."
  default = {
    tool = ".tool"
  }

  validation {
    condition     = alltrue([for v in values(var.firehose_dynamic_partitioning_jq) : length(v) > 0])
    error_message = "Each firehose_dynamic_partitioning_jq value must be a non-empty JQ expression."
  }

  # Every value is concatenated into one MetadataExtractionQuery ({key:value,...}) that
  # Firehose compiles as JQ. strftime/timestamp tokens such as %Y/%m/%d are NOT valid JQ
  # -- a single one makes the whole query fail to compile and routes every delivered
  # record to errors/metadata-extraction-failed/ instead of raw/ (BUG-4). The dt partition
  # comes from the Firehose-native !{timestamp:<fmt>} namespace in firehose_prefix, never
  # from JQ, so reject strftime date tokens (% immediately followed by a letter) and fail
  # fast at plan time rather than silently sending the data lake dark.
  validation {
    condition     = alltrue([for v in values(var.firehose_dynamic_partitioning_jq) : !can(regex("%[A-Za-z]", v))])
    error_message = "firehose_dynamic_partitioning_jq values must be valid JQ expressions, not strftime/timestamp patterns (e.g. \"%Y/%m/%d\"). The dt partition is supplied by the Firehose-native !{timestamp:<fmt>} namespace in firehose_prefix, not by JQ."
  }
}

variable "firehose_prefix" {
  type        = string
  description = "(Optional) S3 object prefix for successful Firehose delivery records. When dynamic_partitioning_enabled is true (always for this module), the prefix MUST contain !{partitionKeyFromQuery:<key>} or !{timestamp:<fmt>} namespaces for every key in firehose_dynamic_partitioning_jq; AWS rejects a stream whose prefix omits those namespaces. The Glue table's Athena partition-projection storage.location.template and dt date-format are DERIVED from this prefix (locals.tf), so it must carry both the !{partitionKeyFromQuery:tool} namespace (the tool partition) and a !{timestamp:<fmt>} namespace (the dt partition) -- otherwise the projected partitions diverge from the delivered objects and queries return nothing. Default matches the built-in JQ key (tool, via !{partitionKeyFromQuery:tool}) plus the Firehose-native dt namespace (!{timestamp:yyyy-MM-dd})."
  default     = "raw/tool=!{partitionKeyFromQuery:tool}/dt=!{timestamp:yyyy-MM-dd}/"

  validation {
    condition     = length(var.firehose_prefix) > 0
    error_message = "firehose_prefix must be a non-empty S3 prefix string."
  }

  # The Athena partition-projection storage.location.template and dt format are
  # derived from firehose_prefix (locals.tf), so the prefix MUST carry both the
  # tool partition (!{partitionKeyFromQuery:tool}) and the dt partition
  # (!{timestamp:<fmt>}) namespaces. A prefix missing either namespace would make
  # the projected partition locations diverge from the objects Firehose delivers,
  # so Athena queries would return nothing. Failing fast at plan time keeps the
  # projection and the on-disk layout in lockstep.
  validation {
    condition     = can(regex("!\\{partitionKeyFromQuery:tool\\}", var.firehose_prefix)) && can(regex("!\\{timestamp:[^}]+\\}", var.firehose_prefix))
    error_message = "firehose_prefix must contain both the !{partitionKeyFromQuery:tool} namespace (the tool partition) and a !{timestamp:<fmt>} namespace (the dt partition) so the Athena partition projection derived from it matches the path Firehose writes."
  }
}

variable "firehose_error_output_prefix" {
  type        = string
  description = "(Optional) S3 prefix for failed Firehose delivery records."
  default     = "errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/"

  validation {
    condition     = length(var.firehose_error_output_prefix) > 0
    error_message = "firehose_error_output_prefix must be a non-empty S3 prefix string."
  }
}

# ---------------------------------------------------------------------------
# IAM role inputs -- Firehose delivery role
# ---------------------------------------------------------------------------

variable "firehose_role_name" {
  type        = string
  description = "(Required) Name of the IAM role for Firehose delivery. Must be 64 characters or fewer."

  validation {
    condition     = length(var.firehose_role_name) <= 64
    error_message = "firehose_role_name must be 64 characters or fewer to satisfy the IAM role name limit."
  }
}

variable "firehose_assume_role_policy_json" {
  type        = string
  description = "(Required) A valid JSON trust policy document granting the Firehose service principal permission to assume the delivery role."

  validation {
    condition     = can(jsondecode(var.firehose_assume_role_policy_json))
    error_message = "firehose_assume_role_policy_json must be a valid JSON string."
  }
}

# ---------------------------------------------------------------------------
# ARN inputs for Firehose role policy statement scoping (B1, B4, S7).
# The reference module builds the inline policy JSON internally in locals.tf
# from these input-driven ARNs so the policy is reproducible and ARN-scoped
# per docs/terragrunt-concepts.md without requiring the caller to construct raw JSON.
# ---------------------------------------------------------------------------

variable "firehose_role_s3_bucket_arn" {
  type        = string
  description = "(Required) ARN of the S3 data lake bucket (B1) scoped in the Firehose delivery role inline policy per docs/terragrunt-concepts.md. Must match ^arn:aws:s3:::."

  validation {
    condition     = can(regex("^arn:aws:s3:::", var.firehose_role_s3_bucket_arn))
    error_message = "firehose_role_s3_bucket_arn must be a valid S3 bucket ARN matching ^arn:aws:s3:::."
  }
}

variable "firehose_role_glue_table_arn" {
  type        = string
  description = "(Required) ARN of the Glue format-conversion table (B4) scoped in the Firehose delivery role inline policy per docs/terragrunt-concepts.md. Must match ^arn:aws:glue:."

  validation {
    condition     = can(regex("^arn:aws:glue:", var.firehose_role_glue_table_arn))
    error_message = "firehose_role_glue_table_arn must be a valid Glue table ARN matching ^arn:aws:glue:."
  }
}


variable "firehose_role_log_group_arn" {
  type        = string
  description = "(Required) ARN of the Firehose error log group (S7) scoped in the Firehose delivery role inline policy per docs/terragrunt-concepts.md. Must match ^arn:aws:logs:."

  validation {
    condition     = can(regex("^arn:aws:logs:", var.firehose_role_log_group_arn))
    error_message = "firehose_role_log_group_arn must be a valid CloudWatch Logs ARN matching ^arn:aws:logs:."
  }
}

# ---------------------------------------------------------------------------
# telemetry-data KMS CMK inputs (docs/terragrunt-concepts.md)
# ---------------------------------------------------------------------------

variable "lake_kms_alias" {
  type        = string
  description = "(Optional) Alias suffix for the telemetry-data KMS CMK. The kms-key primitive prefixes 'alias/' automatically. Defaults to 'telemetry-data' per docs/terragrunt-concepts.md."
  default     = "telemetry-data"

  validation {
    condition     = can(regex("^[A-Za-z0-9/_-]+$", var.lake_kms_alias))
    error_message = "lake_kms_alias must contain only alphanumeric characters, slashes, underscores, or hyphens and must not include the 'alias/' prefix."
  }
}

variable "lake_kms_policy_json" {
  type        = string
  description = "(Required) A valid JSON key policy for the telemetry-data CMK. Must contain no wildcard Principal: \"*\" entries and must include kms:ViaService conditions per docs/terragrunt-concepts.md. Principals are all input-driven ARNs (Firehose role, ECS task role, Athena, QuickSight)."

  validation {
    condition     = can(jsondecode(var.lake_kms_policy_json))
    error_message = "lake_kms_policy_json must be a valid JSON string."
  }
}

# ---------------------------------------------------------------------------
# Cross-account read grant inputs (input-driven; default = {} no-op).
# ---------------------------------------------------------------------------

variable "cross_account_read_principals" {
  type = map(object({
    account_id  = string
    description = optional(string, "")
  }))
  description = "(Optional) Cross-account principals granted read (S3 GetObject/ListBucket + KMS Decrypt/DescribeKey) to the data lake. Keyed by a caller-chosen label; each value's account_id is a 12-digit AWS account id whose root principal is granted read on the data lake bucket (an aws_s3_bucket_policy created by this module) and on the telemetry-data CMK (via the cross_account_kms_statements output the terragrunt leaf merges into lake_kms_policy_json). The account root is used because the external role assumes within that account -- a root principal is the correct, stable cross-account grant shape (the specific role ARN need not be known here). Lake Formation is not in use, so lake read access is governed by IAM plus these resource policies. Defaults to {} (no grant, existing behavior preserved); the actual account id is wired in the terragrunt leaf."
  default     = {}

  validation {
    condition     = alltrue([for p in values(var.cross_account_read_principals) : can(regex("^[0-9]{12}$", p.account_id))])
    error_message = "Each cross_account_read_principals account_id must be a 12-digit AWS account id."
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
  description = "(Optional) Value for the Module tag identifying the source reference module."
  default     = "data-lake"
}

# ---------------------------------------------------------------------------
# AWS region input for region-scoped policy conditions (e.g. kms:ViaService).
# ---------------------------------------------------------------------------

variable "region" {
  type        = string
  description = "(Required) AWS region in which the data-lake resources are deployed. Used to construct the kms:ViaService condition value (e.g. 's3.<region>.amazonaws.com') per docs/terragrunt-concepts.md. Must be a valid AWS region string."

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.region))
    error_message = "region must be a valid AWS region string such as 'us-east-1' or 'eu-west-2'."
  }
}
