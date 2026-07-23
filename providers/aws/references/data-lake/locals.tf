locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # Name of the dedicated, self-logging access-log bucket that receives the data
  # lake bucket's S3 server access logs. Derived from the data lake bucket name so
  # it is namespace-unique by construction (no separate input). var.bucket_name is
  # already the namespace-derived data-lake bucket name (e.g.
  # telemetry-useast1-sandbox-shared-data-lake-000-data-lake = 56 chars); the longer
  # "-access-logs" suffix (12 chars) overflowed the 63-char S3 bound for the live
  # envs (sandbox 68, prod 65). The compact "-logs" suffix keeps every env's
  # namespace-derived name within the 63-char S3 bound (sandbox 61, prod 58, qa 56).
  access_log_bucket_name = "${var.bucket_name}-logs"

  # S3 lifecycle rules constructed from the input-driven transition_storage_class,
  # transition_days, and expiration_days. The rule is always enabled and scoped to
  # all objects (no prefix filter). expiration_days = null disables expiry (default);
  # set to a positive integer to enable the records-retention expiry leg (AC-11).
  lifecycle_rules = [
    {
      id                       = "cold-tier-transition"
      enabled                  = true
      prefix                   = ""
      transition_days          = var.transition_days
      transition_storage_class = var.transition_storage_class
      expiration_days          = var.expiration_days
    }
  ]

  # ---------------------------------------------------------------------------
  # Athena partition projection for the Glue format-conversion table (BUG-3).
  #
  # Firehose writes objects to s3://<bucket>/<firehose_prefix>, where
  # firehose_prefix embeds the Firehose placeholder namespaces
  # !{partitionKeyFromQuery:tool} (the tool partition, extracted by the
  # dynamic-partitioning MetadataExtraction JQ) and !{timestamp:<fmt>} (the dt
  # partition, the record's arrival date). The Glue table declares tool + dt as
  # partition keys ONLY -- never also as data columns. Glue stores partition keys
  # as additional descriptor columns, so a name that is both a data column and a
  # partition key yields a descriptor with duplicate columns that Athena and
  # QuickSight reject with "HIVE_INVALID_METADATA: duplicate columns" on every
  # query. The tool value is read from the dynamic-partition path tool=<x>/dt=<y>,
  # not from a data column.
  #
  # The table has no crawler and registers no partitions, so Athena needs
  # partition projection to resolve partitions at query time from the S3 key
  # layout. Both the dt projection format and the storage.location.template are
  # DERIVED from var.firehose_prefix (the single source of truth for the on-disk
  # layout) so the projection can never drift from the path Firehose actually
  # writes:
  #   * tool -> enum projection (the governed, input-driven set of tools that report
  #             into the lake). enum keeps partition pruning to the enumerated tools
  #             while leaving the table queryable with no tool filter, so SELECT * and
  #             BI tools work out of the box. An injected tool column would instead
  #             reject any query lacking a static WHERE tool='...' equality
  #             (CONSTRAINT_VIOLATION). Onboarding a new tool is a reviewed change to
  #             var.glue_partition_projection_tool_values (see the data-lake README).
  #   * dt   -> date projection whose format equals the Firehose timestamp format
  #             and whose range/interval are input-driven
  #   * storage.location.template -> the Firehose prefix with the Firehose
  #     namespaces swapped for Athena's ${tool} / ${dt} placeholders, rooted at the
  #     lake bucket
  # ---------------------------------------------------------------------------
  firehose_tool_query_token = "!{partitionKeyFromQuery:tool}"

  # The timestamp format embedded in firehose_prefix (e.g. "yyyy-MM-dd" from
  # "!{timestamp:yyyy-MM-dd}"). regex() fails the plan with a clear message when the
  # prefix omits a !{timestamp:<fmt>} namespace, so the dt projection can never be
  # built from a prefix that lacks the dt partition Firehose writes (the presence of
  # both namespaces is validated on var.firehose_prefix in variables.tf).
  firehose_dt_timestamp_format = regex("!\\{timestamp:([^}]+)\\}", var.firehose_prefix)[0]
  firehose_dt_timestamp_token  = "!{timestamp:${local.firehose_dt_timestamp_format}}"

  # storage.location.template: the Firehose prefix with the Firehose namespaces
  # replaced by Athena projection placeholders, rooted at the lake bucket. The
  # $${...} escapes emit the literal Athena ${tool}/${dt} placeholders so projected
  # partition locations match the tool=<x>/dt=<y> objects Firehose delivers.
  glue_projection_location_template = "s3://${var.bucket_name}/${replace(replace(var.firehose_prefix, local.firehose_tool_query_token, "$${tool}"), local.firehose_dt_timestamp_token, "$${dt}")}"

  # Athena partition-projection table parameters. Rendered onto the Glue table via
  # the glue-catalog-database primitive's table.parameters passthrough so partitions
  # resolve at query time without a crawler or registered partitions.
  glue_table_parameters = {
    "projection.enabled"          = "true"
    "projection.tool.type"        = "enum"
    "projection.tool.values"      = join(",", sort(var.glue_partition_projection_tool_values))
    "projection.dt.type"          = "date"
    "projection.dt.format"        = local.firehose_dt_timestamp_format
    "projection.dt.range"         = var.glue_partition_projection_dt_range
    "projection.dt.interval"      = tostring(var.glue_partition_projection_dt_interval)
    "projection.dt.interval.unit" = var.glue_partition_projection_dt_interval_unit
    "storage.location.template"   = local.glue_projection_location_template
  }

  # Glue table configuration for Parquet format conversion (B4). tool + dt are
  # partition keys ONLY (disjoint from the data columns); the data columns are the
  # event fields. Athena partition projection (glue_table_parameters) resolves
  # partitions from the S3 key layout without a crawler.
  #
  # The SerDe + input/output formats MUST match the columnar Parquet that Firehose's
  # DataFormatConversion writes (ParquetSerDe/SNAPPY). The table previously declared the
  # OpenX JSON SerDe with text input/output formats, so Athena read the binary Parquet
  # through a JSON text reader and returned every data column as NULL (the tool/dt partition
  # keys still resolved from the S3 path, masking the failure). ParquetHiveSerDe with the
  # MapredParquet input/output formats reads the Firehose Parquet directly, so Athena returns
  # the actual row values. This matches the parquet-partitioned example of the
  # kinesis-firehose-delivery-stream primitive (BUG-7, proven in qa: an Athena SELECT over the
  # JSON-SerDe table returned NULL data columns; over the Parquet-SerDe table it returns the
  # written values and the row count matches the delivered records).
  glue_table = {
    name                        = var.glue_table_name
    location                    = "s3://${var.bucket_name}/"
    input_format                = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format               = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    serde_serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    columns = [
      { name = "timestamp", type = "string", comment = "ISO-8601 event timestamp" },
      { name = "event_type", type = "string", comment = "Telemetry event type" },
      { name = "payload", type = "string", comment = "Raw JSON payload" },
    ]
    partition_keys = [
      { name = "tool", type = "string" },
      { name = "dt", type = "string" },
    ]
    parameters = local.glue_table_parameters
  }

  # The Firehose delivery stream ARN is deterministic from name + region + account, so
  # it is constructed here rather than read from module.firehose.delivery_stream_arn. The
  # CloudWatch-Logs-split Lambda's role (below) needs firehose:PutRecordBatch on this ARN to
  # re-ingest split records, and the Firehose stream's Lambda processor needs the Lambda's
  # ARN -- reading both from the live resources would form a dependency cycle. The account
  # id comes from the aws_caller_identity data source (the current account, not a literal, D31).
  firehose_stream_arn = "arn:aws:firehose:${var.region}:${data.aws_caller_identity.current.account_id}:deliverystream/${var.firehose_stream_name}"

  # CloudWatch-Logs-split transform Lambda log group (pre-created with the telemetry-data CMK
  # + retention so the function never auto-creates an unencrypted, never-expiring log group).
  cwl_transform_lambda_log_group_name = "/aws/lambda/${var.cwl_transform_lambda_name}"

  # Inline policy for the CloudWatch-Logs-split transform Lambda's execution role. Every
  # statement Resource is a scoped ARN -- no wildcard resources (X-Ray write, which AWS only
  # grants on "*", is attached via the AWS-managed AWSXRayDaemonWriteAccess policy instead, so
  # this inline policy stays wildcard-free). The Lambda re-ingests split records to the SSE-CMK
  # Firehose stream, so it needs firehose:PutRecordBatch on the stream plus
  # kms:GenerateDataKey/Decrypt on the telemetry-data CMK (an SSE-CMK Firehose producer cannot
  # deliver without the KMS grant). It writes its own structured logs to its dedicated log group.
  cwl_transform_lambda_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "FirehoseReingestSplitRecords"
        Effect = "Allow"
        Action = [
          "firehose:PutRecord",
          "firehose:PutRecordBatch",
        ]
        Resource = [local.firehose_stream_arn]
      },
      {
        Sid    = "KmsDataLakeKeyAccess"
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
        ]
        Resource = [module.lake_kms_key.key_arn]
      },
      {
        Sid    = "LambdaSelfLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = ["${aws_cloudwatch_log_group.cwl_transform.arn}:*"]
      },
    ]
  })

  # Firehose delivery role inline policy scoped per docs/terragrunt-concepts.md.
  # All statement Resources are input-driven ARNs -- no wildcard resources.
  # The KMS ARN is wired from module.lake_kms_key.key_arn so the composition
  # is self-contained and no external KMS ARN input is needed.
  firehose_delivery_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3DataLakeBucketAccess"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetBucketLocation",
          "s3:ListBucket",
          "s3:AbortMultipartUpload",
          "s3:GetObject",
        ]
        Resource = [
          var.firehose_role_s3_bucket_arn,
          "${var.firehose_role_s3_bucket_arn}/*",
        ]
      },
      {
        Sid    = "GlueFormatConversionTableAccess"
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetTableVersion",
          "glue:GetTableVersions",
        ]
        # Glue authorizes GetTable*/GetTableVersions against the catalog, database, AND
        # table ARNs together; scoping only the table ARN yields AccessDenied on the
        # catalog resource when Firehose reads the schema for Parquet conversion. The
        # catalog and database ARNs come from the glue-catalog-database module outputs
        # (catalog_id is the account id; database_arn is the real database ARN) so no
        # account id literal appears here (D31). region is input-driven (var.region).
        Resource = [
          "arn:aws:glue:${var.region}:${module.glue_catalog.catalog_id}:catalog",
          module.glue_catalog.database_arn,
          var.firehose_role_glue_table_arn,
        ]
      },
      {
        Sid    = "KmsDataLakeKeyAccess"
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
        ]
        Resource = [module.lake_kms_key.key_arn]
        Condition = {
          StringEquals = {
            "kms:ViaService" = "s3.${var.region}.amazonaws.com"
          }
        }
      },
      {
        Sid      = "FirehoseErrorLogGroupAccess"
        Effect   = "Allow"
        Action   = ["logs:PutLogEvents"]
        Resource = [var.firehose_role_log_group_arn]
      },
      {
        # Firehose assumes this delivery role to invoke the CloudWatch-Logs-split transform
        # Lambda (the processing_configuration's first processor); without InvokeFunction the
        # stream's Lambda processor fails. Scoped to the transform Lambda ARN only.
        Sid    = "LambdaTransformInvoke"
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction",
          "lambda:GetFunctionConfiguration",
        ]
        Resource = [aws_lambda_function.cwl_transform.arn]
      },
    ]
  })

  # ---------------------------------------------------------------------------
  # Cross-account read grant (input-driven; empty by default = no statements).
  #
  # A central BI/analytics role in another AWS account reads the telemetry data
  # lake via Athena/Glue. Because the lake bucket is SSE-KMS with the
  # telemetry-data CMK, S3 read alone returns AccessDenied on GetObject -- the
  # external principal needs BOTH S3 read on the bucket AND KMS Decrypt on the
  # CMK. The grant is expressed against the account ROOT principal
  # (arn:aws:iam::<account_id>:root): the external role assumes within its own
  # account, and a root principal delegates the grant to that account's IAM (the
  # correct, stable cross-account grant shape -- the specific role ARN need not be
  # known here). Lake Formation is not in use, so table access is governed by IAM
  # plus these resource policies, not LF permissions.
  #
  # SPLIT OWNERSHIP: the S3 statements are attached to the lake bucket by the
  # aws_s3_bucket_policy.lake_cross_account resource (main.tf) -- this reference
  # owns the lake bucket, so it owns its bucket policy. The KMS statements are
  # EXPOSED as the cross_account_kms_statements output for the terragrunt leaf to
  # MERGE into the lake_kms_policy_json it already owns and passes in; this
  # reference does NOT own the whole telemetry-data key policy, so it must not
  # attach a second key policy.
  # ---------------------------------------------------------------------------
  cross_account_principal_arns = [
    for p in values(var.cross_account_read_principals) : "arn:aws:iam::${p.account_id}:root"
  ]

  cross_account_s3_statements = length(var.cross_account_read_principals) > 0 ? [
    {
      Sid    = "CrossAccountDataLakeRead"
      Effect = "Allow"
      Principal = {
        AWS = local.cross_account_principal_arns
      }
      # GetObject matches the object ARN (bucket/*); ListBucket + GetBucketLocation
      # match the bucket ARN. Both ARNs are supplied so each action resolves against
      # the resource it applies to (mirrors the Firehose S3DataLakeBucketAccess shape).
      Action = [
        "s3:GetObject",
        "s3:ListBucket",
        "s3:GetBucketLocation",
      ]
      Resource = [
        module.lake_bucket.bucket_arn,
        "${module.lake_bucket.bucket_arn}/*",
      ]
    }
  ] : []

  # Full bucket-policy document attached to the lake bucket when the grant is on.
  cross_account_s3_bucket_policy_json = jsonencode({
    Version   = "2012-10-17"
    Statement = local.cross_account_s3_statements
  })

  # KMS key-policy statements for the telemetry-data CMK. Resource = "*" is the KMS
  # key-policy idiom meaning "this key" (the key the policy is attached to). Exposed
  # via the cross_account_kms_statements output for the leaf to merge into
  # lake_kms_policy_json (the leaf owns the final key policy JSON).
  cross_account_kms_statements = length(var.cross_account_read_principals) > 0 ? [
    {
      Sid    = "CrossAccountDataLakeKmsRead"
      Effect = "Allow"
      Principal = {
        AWS = local.cross_account_principal_arns
      }
      Action = [
        "kms:Decrypt",
        "kms:DescribeKey",
      ]
      Resource = "*"
      Condition = {
        StringEquals = {
          "kms:ViaService" = "s3.${var.region}.amazonaws.com"
        }
      }
    }
  ] : []
}
