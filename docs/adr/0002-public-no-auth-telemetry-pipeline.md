# 0002. Public no-auth telemetry pipeline

- Status: Accepted
- Era: Initial IaC design

## Context

telemetry-platform is an AWS-native usage-analytics platform whose producers are client tools and CLIs distributed across many independent environments. Those producers must be able to report usage events without first obtaining or rotating credentials, which rules out an authenticated ingest API: there is no practical way to distribute and manage client API keys across every tool that might emit telemetry.

Usage events travel as OTLP/HTTP log records (the platform is logs-only by design). The platform therefore needs a public, unauthenticated ingest endpoint that accepts untrusted OTLP traffic at the edge and lands it durably and cheaply for later analysis, while keeping the security boundary somewhere other than producer authentication.

## Decision

Accept OTLP log records on a public, unauthenticated ingest path and move them through an AWS-native serverless pipeline:

- CloudFront is the single public entry point, fronted by a WAF WebACL that filters requests before they are forwarded.
- CloudFront forwards over a VPC origin to an internal Application Load Balancer; the ALB never exposes a public listener.
- The ALB routes to the ADOT collector running on ECS Fargate, which terminates OTLP/HTTP, applies a memory-limiter and batch processor, and exports each record onward.
- Records flow through CloudWatch Logs and a match-all subscription filter into Kinesis Data Firehose, which extracts the `tool` partition key and converts records to Parquet.
- Firehose writes partitioned Parquet to the S3 data lake; a Glue catalog table describes the layout and Athena resolves partitions through projection.
- Athena queries the lake through a cost-capped workgroup for downstream analysis and reporting.

Because ingest is intentionally unauthenticated, the security boundary for untrusted traffic sits at the edge rather than at a producer credential.

## Consequences

This sets the overall system topology: a public ingest half (edge to collector to data lake) joined to an Athena consume surface over the S3 data lake.

- Producer onboarding requires no credentials, since there are no client API keys to distribute or rotate.
- The platform must defend against abuse at the edge instead of through authentication. The compensating controls are the single CloudFront entry point, the WAF WebACL (AWS managed rule groups plus a per-IP rate-based rule), the internal-only ALB reachable solely through the CloudFront VPC origin, and request shaping (maximum body size and the memory limiter) applied at the ADOT receiver rather than at the WAF edge so large legitimate batches are not dropped.
- The logs-only OTLP record shape keeps a uniform record from the client through Parquet to Athena, so partition keys and data columns populate consistently across the pipeline.

See the [ADR index](README.md).
