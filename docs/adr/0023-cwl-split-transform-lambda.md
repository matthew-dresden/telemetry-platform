# 0023. cwl-split transform Lambda for any-size CWL delivery

- Status: Accepted
- Era: E2E hardening

## Context

CloudWatch Logs subscription-filter records arrive GZIP-compressed and wrapped in a
`{messageType, owner, logGroup, logStream, subscriptionFilters, logEvents[]}` envelope, and a
single subscription record batches many `logEvents`. The downstream Firehose delivery stream
uses dynamic partitioning, whose `MetadataExtraction` jq engine expects one JSON object per
record.

The AWS-native Firehose `RecordDeAggregation` processor that would unbundle the envelope is
hard-capped at 500 sub-records per record. A delivery whose record carries more than 500 events
is passed whole to `MetadataExtraction`, which rejects the multi-object blob with a
`DynamicPartitioning.MetadataExtractionFailed` error and routes every event to the
`errors/metadata-extraction-failed/` prefix. A Firehose transform Lambda is strictly 1:1 — each
returned record must reuse its input `recordId`, and the function cannot emit more records than
it received — so a transform cannot split one record into many by returning them directly.

## Decision

Add a Firehose transform Lambda, `cwl_split`, that follows the AWS re-ingestion pattern instead
of returning split records inline.

For each input record the function decompresses the GZIP payload, parses the CloudWatch Logs
envelope, and extracts every `logEvents[].message` as its own single-JSON record. It re-ingests
those records into the same delivery stream via `firehose:PutRecordBatch`, chunked to the
500-record API limit, and marks the original aggregated record `Dropped`. Re-ingestion of failed
records is retried up to a bounded, environment-driven budget; once the budget is exhausted the
original record is reported `ProcessingFailed` so Firehose retries it rather than losing data
silently. Subscription `CONTROL_MESSAGE` envelopes carry no log data and are dropped.

```text
input record   result
GZIP envelope  Dropped         (events re-ingested individually)
CONTROL_MESSAGE Dropped        (no log data)
single JSON    Ok              (re-ingested record passes through 1:1)
re-ingest fail ProcessingFailed (Firehose retries; no silent loss)
```

Re-ingested records are plain single-event JSON, not GZIP, so on re-invocation they take the
pass-through branch and flow 1:1 to `MetadataExtraction` and Parquet conversion. Re-ingestion
has no 500-record cap. The delivery stream name is a required Lambda input and the function fails
fast if it is unset.

## Consequences

A CloudWatch Logs delivery of any size is split into individual JSON records before
`MetadataExtraction`, removing the 500-sub-record ceiling that the native de-aggregation
processor imposed and keeping oversized deliveries out of the error prefix. The data-lake module
gains a new required input for the delivery stream name, which is a breaking change for module
consumers that must now supply it. Re-ingestion routes each oversized delivery through the
delivery stream twice — once for the aggregated record and once for the re-ingested single-event
records — and adds a Lambda invocation to the ingestion path.

See the [ADR index](README.md).
