variable "name" {
  type        = string
  description = "(Required) The name of the Firehose delivery stream. Must contain only alphanumeric characters, underscores, hyphens, and dots."

  validation {
    condition     = can(regex("^[a-zA-Z0-9_.-]+$", var.name))
    error_message = "name must contain only alphanumeric characters, underscores, hyphens, and dots."
  }
}

variable "destination" {
  type        = string
  description = "(Optional) Destination type. Only extended_s3 is supported for the telemetry data lake."
  default     = "extended_s3"

  validation {
    condition     = contains(["extended_s3"], var.destination)
    error_message = "destination must be extended_s3. Only the S3 lake destination is supported for this platform."
  }
}

variable "role_arn" {
  type        = string
  description = "(Required) ARN of the IAM role that grants Firehose permission to write to S3. Must match ^arn:aws:iam::."

  validation {
    condition     = can(regex("^arn:aws:iam::", var.role_arn))
    error_message = "role_arn must be a valid IAM role ARN matching ^arn:aws:iam::."
  }
}

variable "bucket_arn" {
  type        = string
  description = "(Required) ARN of the target S3 data lake bucket. Must match ^arn:aws:s3:::."

  validation {
    condition     = can(regex("^arn:aws:s3:::", var.bucket_arn))
    error_message = "bucket_arn must be a valid S3 bucket ARN matching ^arn:aws:s3:::."
  }
}

variable "prefix" {
  type        = string
  description = "(Optional) S3 object prefix for successful records (supports dynamic partitioning expressions)."
  default     = "raw/"
}

variable "error_output_prefix" {
  type        = string
  description = "(Optional) S3 prefix for failed records."
  default     = "errors/"
}

variable "buffering_size" {
  type        = number
  description = "(Optional) Buffer size in MB before delivery. Must be between 1 and 128 (AWS allowed range)."
  default     = 64

  validation {
    condition     = var.buffering_size >= 1 && var.buffering_size <= 128
    error_message = "buffering_size must be between 1 and 128 MB (AWS allowed range)."
  }
}

variable "buffering_interval" {
  type        = number
  description = "(Optional) Buffer time in seconds before delivery. Must be between 60 and 900 (AWS allowed range)."
  default     = 300

  validation {
    condition     = var.buffering_interval >= 60 && var.buffering_interval <= 900
    error_message = "buffering_interval must be between 60 and 900 seconds (AWS allowed range)."
  }
}

variable "compression_format" {
  type        = string
  description = "(Optional) Compression format for S3 objects. Use UNCOMPRESSED when format conversion to Parquet is enabled."
  default     = "UNCOMPRESSED"

  validation {
    condition     = contains(["UNCOMPRESSED", "GZIP", "Snappy", "ZIP", "HADOOP_SNAPPY"], var.compression_format)
    error_message = "compression_format must be one of UNCOMPRESSED, GZIP, Snappy, ZIP, or HADOOP_SNAPPY."
  }
}

variable "kms_key_arn" {
  type        = string
  description = "(Required) ARN of the KMS key used for server-side encryption of Firehose delivery. Must match ^arn:aws:kms:."

  validation {
    condition     = can(regex("^arn:aws:kms:", var.kms_key_arn))
    error_message = "kms_key_arn must be a valid KMS key ARN matching ^arn:aws:kms:."
  }
}

variable "enable_format_conversion" {
  type        = bool
  description = "(Optional) When true, enables JSON-to-Parquet format conversion using the Glue table referenced in data_format_conversion."
  default     = true
}

variable "data_format_conversion" {
  type = object({
    glue_database_name = string
    glue_table_name    = string
    glue_role_arn      = string
  })
  description = "(Optional) Glue schema source for Parquet conversion (the Glue catalog table B4). Required when enable_format_conversion is true."
  default     = null

  validation {
    condition     = !var.enable_format_conversion || var.data_format_conversion != null
    error_message = "data_format_conversion must be non-null when enable_format_conversion is true."
  }
}

variable "dynamic_partitioning_enabled" {
  type        = bool
  description = "(Optional) When true, enables dynamic partitioning using JQ expressions from dynamic_partitioning_jq."
  default     = true
}

variable "dynamic_partitioning_jq" {
  type        = map(string)
  description = "(Optional) JQ expressions for dynamic-partitioning metadata extraction. Each value must be non-empty. Keys are used as partition metadata extraction names in the Firehose processor (T27: these are input-driven, never hardcoded in main.tf)."
  default = {
    tool = ".tool"
    date = "%Y/%m/%d"
  }

  validation {
    condition     = alltrue([for v in values(var.dynamic_partitioning_jq) : length(v) > 0])
    error_message = "Each dynamic_partitioning_jq value must be a non-empty JQ expression or timestamp pattern."
  }
}

variable "cloudwatch_logging_enabled" {
  type        = bool
  description = "(Optional) When true, enables CloudWatch error logging for failed delivery records."
  default     = true
}

variable "transform_lambda_arn" {
  type        = string
  description = "(Optional) ARN of a Firehose transform Lambda prepended to the processing_configuration as the record-splitting front end for a CloudWatch Logs subscription source. CloudWatch Logs subscription records arrive GZIP-compressed and wrapped in the {messageType, owner, logGroup, logStream, subscriptionFilters, logEvents[]} envelope, batching MANY logEvents per record. The AWS-native unwrap path (Decompression -> CloudWatchLogProcessing -> RecordDeAggregation) cannot be used at scale because RecordDeAggregation is hard-capped at 500 sub-records per record: a delivery whose record carries more than 500 events is passed WHOLE to the dynamic-partitioning MetadataExtraction JQ engine, which rejects it with 'Non JSON record provided' and routes every event to errors/metadata-extraction-failed/. Because a Firehose transform Lambda is strictly 1:1 (it cannot emit more records than it received), the Lambda decompresses + envelope-strips each record and re-ingests each logEvents[].message as its own single-JSON record via firehose:PutRecordBatch (marking the original Dropped), which has no 500-record cap, so a CloudWatch Logs delivery of any size is split into individual JSON records before MetadataExtraction. The Firehose delivery role (role_arn) must hold lambda:InvokeFunction + lambda:GetFunctionConfiguration on this Lambda. Defaults to null so standalone fixtures keep their single-MetadataExtraction processing_configuration."
  default     = null

  validation {
    condition     = var.transform_lambda_arn == null || can(regex("^arn:aws:lambda:", var.transform_lambda_arn))
    error_message = "transform_lambda_arn must be a valid Lambda function ARN matching ^arn:aws:lambda: when provided."
  }
}

variable "log_retention_in_days" {
  type        = number
  description = "(Optional) CloudWatch log group retention in days. Must be one of the AWS allowed values."
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.log_retention_in_days)
    error_message = "log_retention_in_days must be one of the AWS allowed values: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, or 3653."
  }
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources created by this module."
  default     = {}
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Value for the ManagedBy tag."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Value for the Module tag identifying the source module."
  default     = "kinesis-firehose-delivery-stream"
}

variable "create_timeout" {
  type        = string
  description = "(Optional) Bounded timeout the AWS provider waits for the delivery stream to reach ACTIVE before failing fast. Overrides the provider's 30m default so a stream stuck in CREATING surfaces a clear timeout error instead of silently blocking. Format: a Go duration ending in s, m, or h (e.g. 10m)."
  default     = "10m"

  validation {
    condition     = can(regex("^[0-9]+[smh]$", var.create_timeout))
    error_message = "create_timeout must be a Go duration ending in s, m, or h (e.g. 10m)."
  }
}

variable "update_timeout" {
  type        = string
  description = "(Optional) Bounded timeout the AWS provider waits for an in-place delivery stream update to converge before failing fast. Overrides the provider's 10m default. Format: a Go duration ending in s, m, or h (e.g. 10m)."
  default     = "10m"

  validation {
    condition     = can(regex("^[0-9]+[smh]$", var.update_timeout))
    error_message = "update_timeout must be a Go duration ending in s, m, or h (e.g. 10m)."
  }
}

variable "delete_timeout" {
  type        = string
  description = "(Optional) Bounded timeout the AWS provider waits for the delivery stream to be fully deleted before failing fast. Overrides the provider's 30m default so a stream stuck in DELETING surfaces a clear timeout error instead of silently blocking. Format: a Go duration ending in s, m, or h (e.g. 10m)."
  default     = "10m"

  validation {
    condition     = can(regex("^[0-9]+[smh]$", var.delete_timeout))
    error_message = "delete_timeout must be a Go duration ending in s, m, or h (e.g. 10m)."
  }
}
