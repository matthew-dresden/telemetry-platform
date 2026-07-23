variable "stream_name" {
  type        = string
  description = "Name of the Firehose delivery stream and prefix for all prerequisite resources."
  # A concrete default lets the static security scan resolve the derived bucket
  # name (and the self-logging access-log target reference) so it can confirm S3
  # access logging is enabled. Terratest overrides this via TF_VAR_stream_name.
  default = "telemetry-parquet-firehose"
}

variable "dynamic_partitioning_jq" {
  type        = map(string)
  description = "JQ expressions for dynamic-partitioning metadata extraction (T27: input-driven, not hardcoded)."
  default = {
    tool = ".tool"
    date = "%Y/%m/%d"
  }
}

variable "transform_lambda_arn" {
  type        = string
  description = "When set, the stream prepends a Lambda transform processor (the record-splitting front end for a CloudWatch Logs subscription source) before MetadataExtraction. Defaults to null to preserve the single-MetadataExtraction fixture behavior."
  default     = null
}

variable "project_tag" {
  type        = string
  description = "Project tag value applied to all resources via the provider default_tags block."
}

variable "terratest_run_id" {
  type        = string
  description = "Terratest run identifier applied to all resources via the provider default_tags block."
}
