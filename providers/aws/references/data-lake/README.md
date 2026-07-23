# data-lake

Composes the telemetry data-lake primitives into a single NEW-LOCAL reference so consumers deploy
storage, ingestion, catalog, encryption, and the Firehose IAM role as one unit instead of wiring
them by hand.

The reference root declares no `resource` blocks. All infrastructure is delegated to the composed
primitives: `kms-key`, `s3-bucket`, `glue-catalog-database`, `iam-role` (the Firehose delivery role),
and `kinesis-firehose-delivery-stream`.

## Composed modules

- `kms-key` (telemetry-platform v1.0.0) -- the `telemetry-data` CMK (`alias/telemetry-data`) with
  `enable_key_rotation=true` and an input-driven principal-scoped key policy (no wildcard principals,
  `kms:ViaService` conditions) per docs/terragrunt-concepts.md. This reference owns ONLY this CMK;
  the `telemetry-config` (S4) and `telemetry-spice` (S5) CMKs are owned by other modules.
- `s3-bucket` (telemetry-platform v1.0.0) -- the S3 data lake bucket (B1) encrypted with the
  `telemetry-data` CMK, with all public-access blocked and an input-driven cold-tier lifecycle rule
  whose `transition_storage_class` is propagated from the reference input (AC-11). Its S3 server
  access logs are delivered to the dedicated access-log bucket below.
- `s3-bucket` (telemetry-platform v1.0.0) -- a dedicated `<bucket_name>-logs` bucket that receives
  the data lake bucket's S3 server access logs. It is a self-logging terminal bucket (its own access
  logs are written back under its `<bucket_name>/` prefix, the same pattern the state-bootstrap
  access-log bucket uses), encrypted with the `telemetry-data` CMK, with all public-access blocked.
  A dedicated log bucket -- rather than self-logging the data lake bucket -- keeps access-log objects
  out of the data lake so Firehose/Glue/Athena never read them, and enables S3 access logging on the
  data lake bucket without a trivy AWS-0089 suppression.
- `glue-catalog-database` (telemetry-platform v1.0.0) -- the Glue catalog database and format-conversion
  table (B4) for Firehose Parquet conversion. `tool` and `dt` are declared as partition keys ONLY
  (disjoint from the data columns) and the table carries Athena partition-projection parameters so
  partitions resolve at query time from the S3 key layout without a crawler (see "Glue table schema
  and Athena partition projection" below).
- `iam-role` (telemetry-platform v1.0.0) -- the Firehose delivery role. This single role serves BOTH the
  Firehose delivery `role_arn` AND the `data_format_conversion` `schema_configuration.role_arn`: it
  trusts `firehose.amazonaws.com` and its inline policy (built internally in `locals.tf` from
  input-driven ARN-scoped inputs, least-privilege per docs/terragrunt-concepts.md) grants
  `glue:GetTable`/`GetTableVersion`/`GetTableVersions` on the format-conversion table. Firehose assumes
  it both to deliver to S3 and to read the Glue Data Catalog schema during Parquet format conversion.
  A separate `glue.amazonaws.com`-only conversion role cannot be assumed by Firehose, so none is created.
  The role's `role_arn` output is wired directly into the Firehose primitive so no `role_arn` input is
  left dangling.
- `kinesis-firehose-delivery-stream` (telemetry-platform v1.0.0) -- the Firehose delivery stream with both
  `role_arn` and `data_format_conversion.glue_role_arn` (the schema_configuration role) wired from the
  composed Firehose delivery IAM role, and `dynamic_partitioning_jq` passed through from the reference
  input (T27, AC-11).

## CloudWatch Logs subscription source (telemetry ingest hop)

The telemetry stream's source is a CloudWatch Logs subscription filter owned by the
collector-ingestion reference: the `awscloudwatchlogs` ADOT exporter writes each OTLP log
record body string (`raw_log = true`) into a dedicated telemetry log group, and a match-all
subscription filter forwards every record here. Those records arrive GZIP-compressed and
wrapped in the CloudWatch Logs `{owner, logGroup, logStream, ..., logEvents[]}` envelope.

This reference creates a **CloudWatch-Logs-split transform Lambda** (`aws_lambda_function.cwl_transform`,
source in `lambda/cwl_split/index.py`) and wires its ARN into the composed
`kinesis-firehose-delivery-stream` primitive as `transform_lambda_arn`, so the stream's
`processing_configuration` is `Lambda -> MetadataExtraction`.

The AWS-native unwrap path (`Decompression` -> `CloudWatchLogProcessing` -> `RecordDeAggregation`)
cannot be used at scale: `RecordDeAggregation` is **hard-capped at 500 sub-records per record**, so a
single subscription record batching more than 500 `logEvents` is passed WHOLE to the
`MetadataExtraction` JQ engine, which rejects the multi-object blob with
`DynamicPartitioning.MetadataExtractionFailed: Non JSON record provided` and routes **every** event to
`errors/metadata-extraction-failed/` (BUG-6, proven in QA: a 2000-event delivery landed wholly under
`errors/`, a 50-event delivery under `raw/`). A Firehose transform Lambda is strictly 1:1 (it cannot
emit more records than it received), so the Lambda instead decompresses + envelope-strips each record
and **re-ingests each `logEvents[].message` as its own single-JSON record via `firehose:PutRecordBatch`**
(marking the original `Dropped`). Re-ingested records are plain single-event JSON (not GZIP), so on
re-invocation they pass through 1:1 to `MetadataExtraction` + Parquet conversion. Re-ingestion has no
500-record cap, so a CloudWatch Logs delivery of **any size** is split into individual JSON records
before partitioning. The composed Firehose delivery role holds `lambda:InvokeFunction` +
`lambda:GetFunctionConfiguration` on this Lambda; the Lambda's execution role holds
`firehose:PutRecordBatch` on the stream plus `kms:GenerateDataKey`/`Decrypt` on the telemetry-data CMK
(an SSE-CMK Firehose producer cannot deliver without the KMS grant).

The merged `MetadataExtractionQuery` must be a **valid JQ expression** -- it renders as
`{tool:.tool}` and extracts only the `tool` partition key. The `dt` partition is supplied by the
Firehose-native `!{timestamp:yyyy-MM-dd}` namespace in `firehose_prefix`, **not** by JQ, so
`firehose_dynamic_partitioning_jq` must never carry a strftime/timestamp pattern (e.g. `%Y/%m/%d`):
a single such token makes the whole query fail to compile, dynamic partitioning fails, and every
delivered record lands under `errors/metadata-extraction-failed/` instead of `raw/tool=.../dt=.../`
(BUG-4). The variable is validated to reject strftime tokens so this fails fast at plan time.

Because the exporter writes the SDK event JSON raw, the Glue table data columns
(`timestamp`/`event_type`/`payload`) match the unwrapped record, provided the emitting SDK sets
the OTLP body to a flat JSON object with those top-level keys plus a `tool` field (an unverified
contract to lock before live ingest, since this repo is IaC-only and ships no SDK emitter). The
`tool` field feeds the `MetadataExtractionQuery` (`tool = .tool`) that drives the
`tool=<x>/dt=<y>` dynamic-partition path; `tool` is a **partition key only, not a data column**
(see the next section).

## Glue table schema and Athena partition projection (BUG-3)

The Glue format-conversion table separates **data columns** from **partition keys**:

| Kind | Names |
|------|-------|
| Data columns | `timestamp`, `event_type`, `payload` |
| Partition keys | `tool`, `dt` |

`tool` and `dt` are partition keys **only** -- never also data columns. Glue stores partition keys
as additional descriptor columns, so a name that is both a data column and a partition key yields a
descriptor with duplicate columns that Athena and QuickSight reject on every query with
`HIVE_INVALID_METADATA: Table descriptor contains duplicate columns`. The `tool` value is read from
the Firehose dynamic-partition path (`tool=<x>/dt=<y>/`), not from a stored column.

The table has no crawler and registers no partitions, so it enables **Athena partition projection**
(rendered via the `glue-catalog-database` primitive's `table.parameters` passthrough) so partitions
resolve at query time directly from the S3 key layout:

| Parameter | Value | Notes |
|-----------|-------|-------|
| `projection.enabled` | `true` | partitions resolve without a crawler |
| `projection.tool.type` | `enum` | the governed set of tools that report into the lake; keeps partition pruning scoped to those tools while leaving the table queryable with no tool filter (`SELECT *` works) |
| `projection.tool.values` | `glue_partition_projection_tool_values` (default `["e2e-smoke", "example-cli"]`, sorted + comma-joined) | input-driven; production passes the full registry-derived tool list via terragrunt; onboarding a new tool adds it here (see "Onboarding a new tool") |
| `projection.dt.type` | `date` | |
| `projection.dt.format` | `yyyy-MM-dd` | DERIVED from the `!{timestamp:<fmt>}` namespace in `firehose_prefix` |
| `projection.dt.range` | `glue_partition_projection_dt_range` (default `NOW-3YEARS,NOW`) | input-driven |
| `projection.dt.interval` / `.unit` | `glue_partition_projection_dt_interval` / `_unit` (default `1` / `DAYS`) | input-driven |
| `storage.location.template` | `s3://<bucket>/raw/tool=${tool}/dt=${dt}/` | DERIVED from `firehose_prefix` + the lake bucket |

Both the `dt` date format and the `storage.location.template` are derived from `firehose_prefix`
(the single source of truth for the on-disk layout), so the projection can never drift from the path
Firehose actually writes. `firehose_prefix` is validated to carry both the
`!{partitionKeyFromQuery:tool}` and a `!{timestamp:<fmt>}` namespace for this reason. `tool` is an
`enum` dimension whose values are the governed set in `glue_partition_projection_tool_values`, so the
table is queryable with no tool filter (`SELECT *` works) while partition pruning stays scoped to the
enumerated tools. (An `injected` `tool` would instead reject any query lacking a static
`WHERE tool = '...'` equality with `CONSTRAINT_VIOLATION` -- the reason this was changed to `enum`.)

### Onboarding a new tool

The set of tools that may report into the lake is the governed
`glue_partition_projection_tool_values` list. To make a new tool's telemetry queryable:

1. Add the tool identifier to `glue_partition_projection_tool_values` (the data-lake reference input;
   the same value the tool sends as its top-level `tool` field).
2. Open a PR; on merge, CICD applies the change to both environments as an **in-place** Glue table
   update -- no resource replacement, and no change to the table/database/bucket/collector names or
   endpoints that consumers integrate against.
3. The tool's partitions (`raw/tool=<new>/dt=.../`) then become projected and queryable.

The reserved value `e2e-smoke` is used by the post-deploy pipeline validation (`make e2e-all`) and
must remain in the list.

### Ingesting structured-OTLP telemetry

Some tools (e.g. Claude Code, Claude Cowork, and Claude Office agents) emit **structured OTLP
logs** -- their event data lives in the OTLP log record's `attributes`/`resource` fields, not a
flat JSON `body` -- so they do not follow the body contract described above. The collector's
`raw_log = false` pipeline routes these structured records by `resource.service.name`. The
`cwl_split` transform Lambda (`aws_lambda_function.cwl_transform`) reshapes each one into the
same canonical `{timestamp, tool, event_type, payload}` envelope: `tool` is looked up from
`resource.service.name` in `service_tool_map`.

**No fallback tool.** A structured (or EMF metrics) record whose `resource.service.name` is not a
key in `service_tool_map` is NOT assigned a catch-all `tool` value. The transform Lambda isolates
the failure to that single record and re-ingests its ORIGINAL bytes unchanged; because a
structured record carries no top-level `tool` field, Firehose's `{tool:.tool}` dynamic
partitioning routes it to the monitored `errors/` prefix instead of `raw/` -- fail-fast and
visible, never a silently-dropped record and never a record disguised as a registered tool. Every
OTHER record batched in the same CloudWatch Logs delivery (example-cli, other tools, other structured
records) is unaffected and still delivers. In normal operation this path is never exercised: the
collector only routes the tool registry's registered service names, which are exactly
`service_tool_map`'s keys (both derived from the same registry) -- the per-record isolation
defends only a config-drift window (e.g. the collector redeployed before this Lambda's env).

Onboarding a new structured-OTLP tool requires both:

1. Add its `resource.service.name` -> `tool` mapping to `service_tool_map`.
2. Add that `tool` value to `glue_partition_projection_tool_values` (see "Onboarding a new tool"
   above) -- otherwise its rows land in S3 but are unqueryable in Athena.

### Sizing the cwl_split transform Lambda for high-volume ingestion

The transform Lambda amplifies Firehose records: it re-ingests every `logEvents[].message` as its
own `PutRecordBatch` record, so a single invocation holds an entire decompressed CloudWatch Logs
delivery (up to thousands of split records) in memory while it re-ingests them. Two inputs tune
the Lambda for high-volume ingestion (e.g. large concurrent-session bursts) without any code
change:

- `cwl_transform_lambda_memory_size` (default `512` MB) -- Lambda allocates CPU proportionally to
  memory, so raising this both gives the re-ingestion loop more headroom and speeds up
  decompression/`PutRecordBatch` throughput.
- `cwl_transform_lambda_reserved_concurrent_executions` (default `null`, no reservation) --
  reserves a guaranteed slice of the account's regional Lambda concurrency pool for this function
  so it is never starved by other functions competing for the shared unreserved pool. `null`
  leaves the function drawing from the unreserved pool (existing behavior); terragrunt sets the
  real per-env value.

The table's SerDe + input/output formats are `ParquetHiveSerDe` /
`MapredParquetInputFormat` / `MapredParquetOutputFormat`, matching the columnar Parquet that
Firehose's `DataFormatConversion` writes (`ParquetSerDe`/SNAPPY). The table previously declared the
OpenX JSON SerDe with text input/output formats, so Athena read the binary Parquet through a JSON text
reader and returned every data column as `NULL` (the `tool`/`dt` partition keys still resolved from the
S3 path, masking the failure). `ParquetHiveSerDe` reads the Firehose Parquet directly, so Athena
returns the actual row values and the row count matches the delivered records (BUG-7, proven in QA: an
Athena `SELECT` over the JSON-SerDe table returned `NULL` data columns; over the Parquet-SerDe table it
returns the written values and a count that equals the events sent).

## Firehose delivery role inline policy (docs/terragrunt-concepts.md)

The Firehose delivery role inline policy is constructed internally in `locals.tf` from the following
input-driven ARN-scoped inputs. No external caller-supplied policy JSON is required; the module
builds the policy automatically from the ARN inputs and wires the KMS ARN internally from
`module.lake_kms_key.key_arn`.

| Statement | Actions | Resource |
|-----------|---------|----------|
| S3 data lake access | `s3:PutObject`, `s3:GetBucketLocation`, `s3:ListBucket`, `s3:AbortMultipartUpload`, `s3:GetObject` | Lake bucket B1 ARN and `<bucket-arn>/*` only (never `*`) |
| Glue format-conversion table | `glue:GetTable`, `glue:GetTableVersion`, `glue:GetTableVersions` | Glue table B4 ARN only (never `*`) |
| KMS data key operations | `kms:GenerateDataKey`, `kms:Decrypt` | `telemetry-data` CMK ARN (wired internally from `module.lake_kms_key.key_arn`), with `Condition StringEquals kms:ViaService = s3.<region>.amazonaws.com` |
| CloudWatch error logs | `logs:PutLogEvents` | Firehose error log group ARN (S7) only (never `*`) |

The three caller-supplied ARN inputs are `firehose_role_s3_bucket_arn` (B1),
`firehose_role_glue_table_arn` (B4), and `firehose_role_log_group_arn` (S7). The KMS ARN is wired
internally from the composed `kms-key` module. No statement uses a wildcard resource.

## telemetry-data CMK (docs/terragrunt-concepts.md)

The composed `kms-key` is the `telemetry-data` CMK with alias `alias/telemetry-data`. Key properties:

- `enable_key_rotation = true` (annual automatic rotation)
- Key policy supplied via `lake_kms_policy_json`: must have no wildcard `Principal: "*"` entry and
  must use `kms:ViaService` conditions on the Firehose and ECS task role statements. The policy must
  grant the Firehose role, the ECS task role, Athena, and the QuickSight service principal
  `kms:GenerateDataKey` / `kms:Decrypt` with `kms:ViaService` conditions where supported.

## Cross-account read grant (input-driven, default off)

A central BI/analytics team in another AWS account can be granted read access to the telemetry
data lake via `cross_account_read_principals` (a map of `{ account_id, description }` objects,
default `{}` = no grant, so existing behavior is preserved). When non-empty:

- An `aws_s3_bucket_policy` is attached to the data lake bucket granting each configured account
  root (`arn:aws:iam::<account_id>:root`) `s3:GetObject` / `s3:ListBucket` / `s3:GetBucketLocation`,
  scoped to the lake bucket and its objects (never wildcard). The bucket keeps
  `block_public_policy = true`: an account-root grant is not public, so `PutBucketPolicy` accepts it.
- The matching KMS `kms:Decrypt` / `kms:DescribeKey` statements (conditioned on
  `kms:ViaService = s3.<region>.amazonaws.com`) are exposed via the `cross_account_kms_statements`
  output. Because the lake bucket is SSE-KMS with the telemetry-data CMK, S3 read alone returns
  `AccessDenied` on `GetObject` -- the external principal also needs decrypt on the CMK.

The account **root** principal is used deliberately: the external role assumes within its own
account, and a root principal delegates the grant to that account's IAM (the correct, stable
cross-account grant shape -- the specific role ARN need not be known here). Lake Formation is not
in use, so lake access is governed by IAM plus these resource policies.

**Split ownership / leaf wiring.** This reference owns the lake bucket, so it owns and attaches the
S3 bucket policy directly. It does **not** own the whole telemetry-data key policy -- the terragrunt
leaf owns `lake_kms_policy_json`. The leaf must therefore **merge** the `cross_account_kms_statements`
output into the `Statement` array of the JSON it passes to `lake_kms_policy_json` (e.g. `jsondecode`
the base policy, `concat` these statements, `jsonencode`). Only the KMS half needs merging; the S3
half is fully managed by this module.

## D37 input contract

Per decision D37 (`docs/adr/` D37), the `firehose_delivery_stream_arn` output
is consumed as an **INPUT** by the downstream `collector-ingestion` reference. The dependency edge is
one-directional: `data-lake` produces the ARN, `collector-ingestion` receives it as an input. This
avoids feeding a computed output back as an input cycle.

## Usage

When `use_pinned_module_sources = true` (prod environments), the terragrunt leaf sources this reference
via a pinned `git::...?ref=providers/aws/references/data-lake/v<semver>` URL. When false (dev/sandbox),
the leaf uses `${get_repo_root()}//providers/aws/references/data-lake` so local relative defaults apply.

```hcl
# Pinned usage (use_pinned_module_sources = true, prod):
module "data_lake" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/references/data-lake?ref=providers/aws/references/data-lake/v1.0.0"

  bucket_name          = "my-telemetry-data-lake"
  glue_database_name   = "telemetry_data_lake"
  glue_table_name      = "telemetry_events"
  firehose_stream_name = "telemetry-delivery"
  firehose_role_name   = "telemetry-firehose-role"

  region = "us-east-1"

  transition_storage_class = "GLACIER"
  transition_days          = 90

  firehose_dynamic_partitioning_jq = {
    tool = ".tool"
  }

  firehose_assume_role_policy_json = jsonencode({ ... })
  lake_kms_policy_json             = jsonencode({ ... })

  firehose_role_s3_bucket_arn  = "arn:aws:s3:::my-telemetry-data-lake"
  firehose_role_glue_table_arn = "arn:aws:glue:us-east-1:123456789012:table/telemetry_data_lake/telemetry_events"
  firehose_role_log_group_arn  = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/firehose/telemetry-delivery:*"

  lake_kms_alias = "telemetry-data"

  tags = {
    Environment = "prod"
  }
}

output "firehose_delivery_stream_arn" {
  value = module.data_lake.firehose_delivery_stream_arn
}
```

## Examples

- `examples/default` -- five composed primitives, GLACIER lifecycle, JQ dynamic partitioning

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.15.5 |
| aws | >= 6.49.0 |

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

No providers.

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_firehose"></a> [firehose](#module\_firehose) | var.firehose\_source | n/a |
| <a name="module_firehose_role"></a> [firehose\_role](#module\_firehose\_role) | var.firehose\_role\_source | n/a |
| <a name="module_glue_catalog"></a> [glue\_catalog](#module\_glue\_catalog) | var.glue\_catalog\_source | n/a |
| <a name="module_lake_bucket"></a> [lake\_bucket](#module\_lake\_bucket) | var.lake\_bucket\_source | n/a |
| <a name="module_lake_kms_key"></a> [lake\_kms\_key](#module\_lake\_kms\_key) | var.lake\_kms\_key\_source | n/a |

## Resources

| Name | Type |
| ---- | ---- |
| [aws_cloudwatch_log_group.cwl_transform](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_lambda_function.cwl_transform](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function) | resource |
| [aws_s3_bucket_policy.lake_cross_account](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_policy) | resource |
| [archive_file.cwl_transform](https://registry.terraform.io/providers/hashicorp/archive/latest/docs/data-sources/file) | data source |
| [aws_caller_identity.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/caller_identity) | data source |

The CloudWatch-Logs-split transform Lambda's IAM execution role is composed via the iam-role
primitive (reusing `firehose_role_source`); the Firehose delivery stream, delivery role, KMS CMK,
S3 buckets, and Glue catalog are composed via their respective primitive modules.

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_bucket_name"></a> [bucket\_name](#input\_bucket\_name) | (Required) The name of the S3 data lake bucket. Must be globally unique and follow S3 naming conventions. | `string` | n/a | yes |
| <a name="input_cross_account_read_principals"></a> [cross\_account\_read\_principals](#input\_cross\_account\_read\_principals) | (Optional) Cross-account principals granted read (S3 GetObject/ListBucket + KMS Decrypt/DescribeKey) to the data lake. Keyed by a caller-chosen label; each value's account\_id is a 12-digit AWS account id whose root principal is granted read on the data lake bucket (an aws\_s3\_bucket\_policy created by this module) and on the telemetry-data CMK (via the cross\_account\_kms\_statements output the terragrunt leaf merges into lake\_kms\_policy\_json). The account root is used because the external role assumes within that account -- a root principal is the correct, stable cross-account grant shape (the specific role ARN need not be known here). Lake Formation is not in use, so lake read access is governed by IAM plus these resource policies. Defaults to {} (no grant, existing behavior preserved); the actual account id is wired in the terragrunt leaf. | <pre>map(object({<br/>    account_id  = string<br/>    description = optional(string, "")<br/>  }))</pre> | `{}` | no |
| <a name="input_cwl_transform_lambda_log_retention_in_days"></a> [cwl\_transform\_lambda\_log\_retention\_in\_days](#input\_cwl\_transform\_lambda\_log\_retention\_in\_days) | (Optional) Retention in days for the CloudWatch-Logs-split transform Lambda's own log group. Must be one of the AWS allowed values. | `number` | `30` | no |
| <a name="input_cwl_transform_lambda_memory_size"></a> [cwl\_transform\_lambda\_memory\_size](#input\_cwl\_transform\_lambda\_memory\_size) | (Optional) Memory (MB) for the CloudWatch-Logs-split transform Lambda. Defaults to 512: the Lambda re-ingests each logEvents[].message as its own PutRecordBatch record, so a single invocation holds the fully decompressed + envelope-stripped batch (up to thousands of split records) in memory at once, and Lambda's proportionally-allocated CPU scales with memory\_size, so a higher baseline also speeds decompression/PutRecordBatch throughput under high-volume re-ingestion (e.g. concurrent-session bursts). Must be between 128 and 10240 (AWS allowed range); terragrunt sets the real per-env value. | `number` | `512` | no |
| <a name="input_cwl_transform_lambda_name"></a> [cwl\_transform\_lambda\_name](#input\_cwl\_transform\_lambda\_name) | (Required) Name of the CloudWatch-Logs-split Firehose transform Lambda. Must be 64 characters or fewer. | `string` | n/a | yes |
| <a name="input_cwl_transform_lambda_reserved_concurrent_executions"></a> [cwl\_transform\_lambda\_reserved\_concurrent\_executions](#input\_cwl\_transform\_lambda\_reserved\_concurrent\_executions) | (Optional) Reserved concurrent executions for the CloudWatch-Logs-split transform Lambda. Reserving concurrency carves out a guaranteed slice of the account's regional concurrency pool for this function so the Firehose transform is never starved by other functions competing for the shared unreserved pool under high-volume re-ingestion (e.g. ~2000 concurrent Claude Code sessions). Defaults to null, which leaves the function drawing from the account's unreserved concurrency pool (no reservation, existing behavior preserved); terragrunt sets the real per-env value. Must be null or a non-negative integer (AWS allows 0, which throttles the function entirely, up to the account's unreserved concurrency). | `number` | `null` | no |
| <a name="input_cwl_transform_lambda_role_name"></a> [cwl\_transform\_lambda\_role\_name](#input\_cwl\_transform\_lambda\_role\_name) | (Required) Name of the IAM execution role for the CloudWatch-Logs-split transform Lambda. Must be 64 characters or fewer. | `string` | n/a | yes |
| <a name="input_cwl_transform_lambda_runtime"></a> [cwl\_transform\_lambda\_runtime](#input\_cwl\_transform\_lambda\_runtime) | (Optional) Lambda runtime for the CloudWatch-Logs-split transform function. Defaults to python3.12. | `string` | `"python3.12"` | no |
| <a name="input_cwl_transform_lambda_timeout"></a> [cwl\_transform\_lambda\_timeout](#input\_cwl\_transform\_lambda\_timeout) | (Optional) Timeout in seconds for the CloudWatch-Logs-split transform Lambda. Sized for high-volume re-ingestion. Must be between 1 and 900. | `number` | `120` | no |
| <a name="input_expiration_days"></a> [expiration\_days](#input\_expiration\_days) | (Optional) Number of days after which objects are permanently deleted (AC-11 records-retention expiry leg). Set to null to disable expiry. When set, must be greater than transition\_days so objects transition before they expire. | `number` | `null` | no |
| <a name="input_firehose_assume_role_policy_json"></a> [firehose\_assume\_role\_policy\_json](#input\_firehose\_assume\_role\_policy\_json) | (Required) A valid JSON trust policy document granting the Firehose service principal permission to assume the delivery role. | `string` | n/a | yes |
| <a name="input_firehose_dynamic_partitioning_jq"></a> [firehose\_dynamic\_partitioning\_jq](#input\_firehose\_dynamic\_partitioning\_jq) | (Optional) JQ expressions for Firehose dynamic-partitioning metadata extraction. Every key/value pair is merged into the single Firehose MetadataExtractionQuery as {key:value,...}, so each value MUST be a valid JQ expression. strftime/timestamp patterns (e.g. "%Y/%m/%d") are NOT valid JQ; a single one makes the whole MetadataExtractionQuery fail to compile, so dynamic partitioning fails and every delivered record lands under errors/metadata-extraction-failed/ instead of raw/ (BUG-4). The dt partition is supplied by the Firehose-native !{timestamp:<fmt>} namespace in firehose\_prefix, NOT by JQ, so only the tool partition key is extracted here. Defaults to {tool=".tool"}, which renders MetadataExtractionQuery {tool:.tool}. Passed through to the composed Firehose primitive (T27, AC-11). | `map(string)` | <pre>{<br/>  "tool": ".tool"<br/>}</pre> | no |
| <a name="input_firehose_error_output_prefix"></a> [firehose\_error\_output\_prefix](#input\_firehose\_error\_output\_prefix) | (Optional) S3 prefix for failed Firehose delivery records. | `string` | `"errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/"` | no |
| <a name="input_firehose_prefix"></a> [firehose\_prefix](#input\_firehose\_prefix) | (Optional) S3 object prefix for successful Firehose delivery records. When dynamic\_partitioning\_enabled is true (always for this module), the prefix MUST contain !{partitionKeyFromQuery:<key>} or !{timestamp:<fmt>} namespaces for every key in firehose\_dynamic\_partitioning\_jq; AWS rejects a stream whose prefix omits those namespaces. The Glue table's Athena partition-projection storage.location.template and dt date-format are DERIVED from this prefix (locals.tf), so it must carry both the !{partitionKeyFromQuery:tool} namespace (the tool partition) and a !{timestamp:<fmt>} namespace (the dt partition) -- otherwise the projected partitions diverge from the delivered objects and queries return nothing. Default matches the built-in JQ key (tool, via !{partitionKeyFromQuery:tool}) plus the Firehose-native dt namespace (!{timestamp:yyyy-MM-dd}). | `string` | `"raw/tool=!{partitionKeyFromQuery:tool}/dt=!{timestamp:yyyy-MM-dd}/"` | no |
| <a name="input_firehose_role_glue_table_arn"></a> [firehose\_role\_glue\_table\_arn](#input\_firehose\_role\_glue\_table\_arn) | (Required) ARN of the Glue format-conversion table (B4) scoped in the Firehose delivery role inline policy per docs/terragrunt-concepts.md. Must match ^arn:aws:glue:. | `string` | n/a | yes |
| <a name="input_firehose_role_log_group_arn"></a> [firehose\_role\_log\_group\_arn](#input\_firehose\_role\_log\_group\_arn) | (Required) ARN of the Firehose error log group (S7) scoped in the Firehose delivery role inline policy per docs/terragrunt-concepts.md. Must match ^arn:aws:logs:. | `string` | n/a | yes |
| <a name="input_firehose_role_name"></a> [firehose\_role\_name](#input\_firehose\_role\_name) | (Required) Name of the IAM role for Firehose delivery. Must be 64 characters or fewer. | `string` | n/a | yes |
| <a name="input_firehose_role_s3_bucket_arn"></a> [firehose\_role\_s3\_bucket\_arn](#input\_firehose\_role\_s3\_bucket\_arn) | (Required) ARN of the S3 data lake bucket (B1) scoped in the Firehose delivery role inline policy per docs/terragrunt-concepts.md. Must match ^arn:aws:s3:::. | `string` | n/a | yes |
| <a name="input_firehose_role_source"></a> [firehose\_role\_source](#input\_firehose\_role\_source) | Source path for the iam-role primitive used by the firehose\_role child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use\_pinned\_module\_sources. | `string` | `"../../primitives/iam-role"` | no |
| <a name="input_firehose_source"></a> [firehose\_source](#input\_firehose\_source) | Source path for the kinesis-firehose-delivery-stream primitive used by the firehose child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use\_pinned\_module\_sources. | `string` | `"../../primitives/kinesis-firehose-delivery-stream"` | no |
| <a name="input_firehose_stream_name"></a> [firehose\_stream\_name](#input\_firehose\_stream\_name) | (Required) The name of the Firehose delivery stream. Must contain only alphanumeric characters, underscores, hyphens, and dots. | `string` | n/a | yes |
| <a name="input_glue_catalog_source"></a> [glue\_catalog\_source](#input\_glue\_catalog\_source) | Source path for the glue-catalog-database primitive used by the glue\_catalog child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use\_pinned\_module\_sources. | `string` | `"../../primitives/glue-catalog-database"` | no |
| <a name="input_glue_database_name"></a> [glue\_database\_name](#input\_glue\_database\_name) | (Required) The name of the Glue catalog database. Must contain only lowercase letters, digits, and underscores. | `string` | n/a | yes |
| <a name="input_glue_partition_projection_dt_interval"></a> [glue\_partition\_projection\_dt\_interval](#input\_glue\_partition\_projection\_dt\_interval) | (Optional) Athena date-projection interval for the dt partition (projection.dt.interval), in units of glue\_partition\_projection\_dt\_interval\_unit. Defaults to 1 (one day per projected partition, matching the daily dt=yyyy-MM-dd Firehose layout). | `number` | `1` | no |
| <a name="input_glue_partition_projection_dt_interval_unit"></a> [glue\_partition\_projection\_dt\_interval\_unit](#input\_glue\_partition\_projection\_dt\_interval\_unit) | (Optional) Athena date-projection interval unit for the dt partition (projection.dt.interval.unit). One of YEARS, MONTHS, WEEKS, DAYS, HOURS, MINUTES, SECONDS. Defaults to DAYS, matching the daily dt=yyyy-MM-dd Firehose partition layout. | `string` | `"DAYS"` | no |
| <a name="input_glue_partition_projection_dt_range"></a> [glue\_partition\_projection\_dt\_range](#input\_glue\_partition\_projection\_dt\_range) | (Optional) Athena date-projection range for the dt partition (projection.dt.range). Bounds the dates Athena projects for dt. Defaults to 'NOW-3YEARS,NOW' (the trailing three years through today). Must be a comma-separated start,end range. | `string` | `"NOW-3YEARS,NOW"` | no |
| <a name="input_glue_partition_projection_tool_values"></a> [glue\_partition\_projection\_tool\_values](#input\_glue\_partition\_projection\_tool\_values) | (Optional) The governed set of tool identifiers that report into the telemetry lake, used as the Athena enum partition-projection values for the tool partition (projection.tool.values). enum keeps partition pruning scoped to these tools while leaving the table queryable with no tool filter (SELECT \* works), unlike an injected tool column which rejects any query lacking a static WHERE tool='...' equality (CONSTRAINT\_VIOLATION). Onboarding a new tool is a reviewed change to this list (see the data-lake README). Defaults to a minimal set (the reserved 'e2e-smoke' value used by the post-deploy pipeline validation, plus 'example-cli'); production passes the full registry-derived tool list via terragrunt. | `list(string)` | <pre>[<br>  "e2e-smoke",<br>  "example-cli"<br>]</pre> | no |
| <a name="input_glue_table_name"></a> [glue\_table\_name](#input\_glue\_table\_name) | (Required) The name of the Glue catalog table used for Firehose format conversion (B4). Must contain only lowercase letters, digits, and underscores. | `string` | n/a | yes |
| <a name="input_lake_bucket_source"></a> [lake\_bucket\_source](#input\_lake\_bucket\_source) | Source path for the s3-bucket primitive used by the lake\_bucket child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use\_pinned\_module\_sources. | `string` | `"../../primitives/s3-bucket"` | no |
| <a name="input_lake_kms_alias"></a> [lake\_kms\_alias](#input\_lake\_kms\_alias) | (Optional) Alias suffix for the telemetry-data KMS CMK. The kms-key primitive prefixes 'alias/' automatically. Defaults to 'telemetry-data' per docs/terragrunt-concepts.md. | `string` | `"telemetry-data"` | no |
| <a name="input_lake_kms_key_source"></a> [lake\_kms\_key\_source](#input\_lake\_kms\_key\_source) | Source path for the kms-key primitive used by the lake\_kms\_key child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use\_pinned\_module\_sources. | `string` | `"../../primitives/kms-key"` | no |
| <a name="input_lake_kms_policy_json"></a> [lake\_kms\_policy\_json](#input\_lake\_kms\_policy\_json) | (Required) A valid JSON key policy for the telemetry-data CMK. Must contain no wildcard Principal: "*" entries and must include kms:ViaService conditions per docs/terragrunt-concepts.md. Principals are all input-driven ARNs (Firehose role, ECS task role, Athena, QuickSight). | `string` | n/a | yes |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source reference module. | `string` | `"data-lake"` | no |
| <a name="input_region"></a> [region](#input\_region) | (Required) AWS region in which the data-lake resources are deployed. Used to construct the kms:ViaService condition value (e.g. 's3.<region>.amazonaws.com') per docs/terragrunt-concepts.md. Must be a valid AWS region string. | `string` | n/a | yes |
| <a name="input_service_tool_map"></a> [service\_tool\_map](#input\_service\_tool\_map) | (Optional) Maps an OTLP resource.service.name (structured-OTLP tools, e.g. Claude Code / Cowork / Office agents, exported by the collector's raw\_log=false pipeline) to the lake 'tool' partition value the cwl\_split transform assigns. Passed to the Lambda as the SERVICE\_TOOL\_MAP env var (JSON). Empty (the default) is inert: records that follow the body contract (example-cli and any tool with a top-level 'tool') always pass through byte-identical, so this map only affects structured OTLP records. Every mapped value MUST be a member of glue\_partition\_projection\_tool\_values, or its rows land in S3 but are unqueryable in Athena. NO FALLBACK: a structured record whose resource.service.name is not a key in this map fails fast per-record -- the transform Lambda re-ingests its ORIGINAL bytes instead of assigning a catch-all tool, so Firehose routes it to the monitored errors/ prefix (no top-level 'tool') rather than silently dropping it or making it look like a registered tool. | `map(string)` | `{}` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this reference module. | `map(string)` | `{}` | no |
| <a name="input_transition_days"></a> [transition\_days](#input\_transition\_days) | (Optional) Number of days after which objects transition to the cold storage class. | `number` | `90` | no |
| <a name="input_transition_storage_class"></a> [transition\_storage\_class](#input\_transition\_storage\_class) | (Optional) S3 lifecycle transition storage class for cold-tier objects. One of GLACIER, GLACIER\_IR, or DEEP\_ARCHIVE. Propagated to the composed s3-bucket primitive (AC-11). | `string` | `"GLACIER"` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_cross_account_kms_statements"></a> [cross\_account\_kms\_statements](#output\_cross\_account\_kms\_statements) | KMS key-policy statement objects (kms:Decrypt + kms:DescribeKey for each cross\_account\_read\_principals account root, conditioned on kms:ViaService=s3.<region>.amazonaws.com) granting the configured cross-account BI principals decrypt access to the telemetry-data CMK. Empty list when cross\_account\_read\_principals is {} (the default no-op). This reference does NOT own the whole telemetry-data key policy -- the terragrunt leaf owns the lake\_kms\_policy\_json it passes to var.lake\_kms\_policy\_json -- so the leaf MUST MERGE these statements into that policy's Statement array (jsondecode the base policy, concat these statements, jsonencode) so the external role can decrypt the SSE-KMS lake objects it reads. The matching S3 read grant is attached to the lake bucket by this module (aws\_s3\_bucket\_policy.lake\_cross\_account), so only the KMS half needs merging by the leaf. |
| <a name="output_data_lake_bucket_arn"></a> [data\_lake\_bucket\_arn](#output\_data\_lake\_bucket\_arn) | ARN of the S3 data lake bucket (B1). |
| <a name="output_firehose_assume_role_policy_json"></a> [firehose\_assume\_role\_policy\_json](#output\_firehose\_assume\_role\_policy\_json) | The Firehose delivery role trust policy JSON. Exposed for Terratest assertions confirming the role (which is also the schema\_configuration role) trusts firehose.amazonaws.com so Firehose can assume it for format conversion. |
| <a name="output_cwl_transform_lambda_arn"></a> [cwl\_transform\_lambda\_arn](#output\_cwl\_transform\_lambda\_arn) | ARN of the CloudWatch-Logs-split Firehose transform Lambda (the Firehose processing\_configuration's first processor, which re-ingests each logEvents[].message as its own single-JSON record so any-size CWL delivery is split before MetadataExtraction). Exposed for Terratest assertions verifying the split path. |
| <a name="output_cwl_transform_lambda_name"></a> [cwl\_transform\_lambda\_name](#output\_cwl\_transform\_lambda\_name) | Name of the CloudWatch-Logs-split Firehose transform Lambda. The FunctionName dimension for CloudWatch AWS/Lambda alarms (consumed by the observability unit, D37). Wired from the composed Lambda resource's function\_name attribute, not the input var, so it reflects the actually-created function -- mirrors the firehose\_stream\_name output pattern above. |
| <a name="output_firehose_delivery_stream_arn"></a> [firehose\_delivery\_stream\_arn](#output\_firehose\_delivery\_stream\_arn) | ARN of the Firehose delivery stream. Per decision D37, this is consumed as an INPUT by the collector-ingestion reference to keep the dependency edge one-directional. |
| <a name="output_firehose_dynamic_partitioning_jq_output"></a> [firehose\_dynamic\_partitioning\_jq\_output](#output\_firehose\_dynamic\_partitioning\_jq\_output) | The firehose\_dynamic\_partitioning\_jq map passed through to the composed Firehose primitive (T27, AC-11). Exposed for Terratest assertions verifying passthrough. |
| <a name="output_firehose_role_arn"></a> [firehose\_role\_arn](#output\_firehose\_role\_arn) | ARN of the Firehose delivery role. This single role is wired as BOTH the Firehose delivery role\_arn AND the data\_format\_conversion schema\_configuration.role\_arn, so it is exposed for Terratest assertions confirming the schema-configuration role equals the delivery role. |
| <a name="output_firehose_role_inline_policy_json"></a> [firehose\_role\_inline\_policy\_json](#output\_firehose\_role\_inline\_policy\_json) | The Firehose delivery role inline policy JSON built from input-driven ARNs. Exposed for Terratest assertions verifying per-statement ARN scoping per docs/terragrunt-concepts.md. |
| <a name="output_firehose_stream_name"></a> [firehose\_stream\_name](#output\_firehose\_stream\_name) | Name of the Firehose delivery stream. Consumed by the observability unit as the DeliveryStreamName CloudWatch alarm dimension (one-directional dependency edge, D37). Wired from the composed Firehose primitive's resource name, not the input var, so it reflects the actually-created stream. |
| <a name="output_glue_database_name"></a> [glue\_database\_name](#output\_glue\_database\_name) | Name of the Glue catalog database. |
| <a name="output_glue_table_parameters"></a> [glue\_table\_parameters](#output\_glue\_table\_parameters) | The table-level Glue parameters rendered onto the Glue format-conversion table -- the Athena partition-projection configuration (projection.enabled, projection.tool.type=injected, projection.dt.type=date with projection.dt.format/range/interval, and storage.location.template). Exposed so consumers and Terratest can confirm partitions resolve via projection without a crawler and that the projected partition layout matches the Firehose delivery path (BUG-3). |
| <a name="output_lake_kms_key_arn"></a> [lake\_kms\_key\_arn](#output\_lake\_kms\_key\_arn) | ARN of the telemetry-data KMS CMK (alias/telemetry-data). This reference owns only this CMK -- telemetry-config (S4) and telemetry-spice (S5) CMKs are owned by other modules. |
| <a name="output_lake_kms_key_policy_json"></a> [lake\_kms\_key\_policy\_json](#output\_lake\_kms\_key\_policy\_json) | The telemetry-data KMS key policy JSON. Exposed for Terratest assertions verifying absence of wildcard principals per docs/terragrunt-concepts.md. |
| <a name="output_transition_storage_class"></a> [transition\_storage\_class](#output\_transition\_storage\_class) | The S3 lifecycle transition storage class propagated to the composed s3-bucket (AC-11). Exposed for Terratest assertions verifying cold-tier propagation. |
<!-- END_TF_DOCS -->

## Input validation

- `bucket_name` must be 3-63 characters matching S3 naming rules.
- `transition_storage_class` must be one of `GLACIER`, `GLACIER_IR`, or `DEEP_ARCHIVE`.
- `transition_days` must be a positive integer.
- `glue_database_name` and `glue_table_name` must contain only lowercase letters, digits, and underscores.
- `firehose_prefix` must contain both the `!{partitionKeyFromQuery:tool}` namespace and a `!{timestamp:<fmt>}` namespace (the Athena partition projection is derived from it).
- `glue_partition_projection_dt_range` must be a comma-separated `start,end` date-projection range.
- `glue_partition_projection_dt_interval` must be a positive integer.
- `glue_partition_projection_dt_interval_unit` must be one of `YEARS`, `MONTHS`, `WEEKS`, `DAYS`, `HOURS`, `MINUTES`, `SECONDS`.
- `firehose_stream_name` must contain only alphanumeric characters, underscores, hyphens, and dots.
- `firehose_role_name` must be 64 characters or fewer.
- `firehose_role_s3_bucket_arn` must match `^arn:aws:s3:::`.
- `firehose_role_glue_table_arn` must match `^arn:aws:glue:`.
- `firehose_role_log_group_arn` must match `^arn:aws:logs:`.
- `region` must be a valid AWS region string (e.g. `us-east-1`).
- `lake_kms_alias` must not include the `alias/` prefix.
- `cross_account_read_principals` -- each entry's `account_id` must be a 12-digit AWS account id.
- `firehose_assume_role_policy_json` and `lake_kms_policy_json` must both be valid JSON.
- `cwl_transform_lambda_memory_size` must be between 128 and 10240 MB (AWS allowed range).
- `cwl_transform_lambda_reserved_concurrent_executions` must be `null` (no reservation) or a non-negative integer.

## Child module source variables (const=true convention)

Each in-repo child module source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo local relative path without network access. Callers do not override these.

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `lake_kms_key_source` | `../../primitives/kms-key` | Source for the kms-key primitive module |
| `lake_bucket_source` | `../../primitives/s3-bucket` | Source for the s3-bucket primitive module |
| `glue_catalog_source` | `../../primitives/glue-catalog-database` | Source for the glue-catalog-database primitive module |
| `firehose_role_source` | `../../primitives/iam-role` | Source for the Firehose iam-role primitive module |
| `firehose_source` | `../../primitives/kinesis-firehose-delivery-stream` | Source for the kinesis-firehose-delivery-stream primitive module |

By default (when `use_pinned_module_sources = false` in `account.hcl`), each source resolves to the
relative in-repo path above. When `use_pinned_module_sources = true` (prod), the terragrunt leaf
sets every `*_source` input to the pinned `git::https://github.com/example-org/telemetry-platform.git//<path>?ref=<path>/v<semver>`
URL. The toggle flows from `account.hcl` through `_envcommon/data-lake.hcl` to the leaf.
See `docs/terraform-module-sourcing.md` for the end-to-end workflow.

## Policies

- `source_policy.rego` -- in-repo child sources are `source = var.<name>_source` (var-driven, const=true); external `terraform-modules` sources remain pinned literals; prod-context pin gate enforces that in-repo sources resolve to pinned git URLs when `use_pinned_module_sources = true`
- `no_resources_policy` -- the reference root declares no `resource` blocks
- `composition_policy` -- the reference root uses only `module` blocks for infrastructure
