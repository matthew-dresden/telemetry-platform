# Architecture Decision Records

This directory records the architecture decisions for telemetry-platform as numbered
Architecture Decision Records (ADRs), using the format described by Michael Nygard.
Each record captures the context that forced a decision, the decision itself, and
the consequences it imposes. Records are immutable once accepted: when a decision is
replaced, its successor is added as a new ADR and the original is marked `Superseded`
rather than rewritten or deleted. The convention itself is recorded in
[0001](0001-record-architecture-decisions.md).

> **Historical context.** These ADRs record the *original* design context in which
> the platform was first built. They are preserved as the project's reasoning ledger,
> not as current deployment instructions. Records that describe environment-specific
> facts — notably the concrete multi-account AWS topology (ADR 0004) and its four
> illustrative account roles — are historical: a fork wires its own accounts, domains,
> and roles through `terragrunt/common/*.json` (see
> [../deploy-from-scratch.md](../deploy-from-scratch.md)). The original design also
> included a browser dashboard / portal "serve" tier fronted by a hosted sign-in and an
> embedded BI surface; that tier has since been removed and the ADRs that described only
> it have been retired, so the pipeline documented here terminates at the S3 Parquet
> data lake queried through Glue + Athena. Read these records for the *why* behind the
> pipeline's shape; consult the operational docs for how to deploy your own instance.

## How we got here

The platform began with an initial IaC design that fixed its core shape: a public,
no-auth, logs-only OTLP pipeline that lands partitioned Parquet in an S3 data lake
across a four-account AWS topology, built from a two-tier Terraform module taxonomy,
promoted by per-module semver, run from identical module code across environments,
and backed by a hardened Terragrunt state backend. An instance-set refactor then
made every resource name namespace-derived so that numbered, immutable instance sets
could coexist and traffic could cut over between them through DNS, and it adopted
destroy-and-recreate as the dev-rebuild model. A copyable-tree refactor moved state
locking to S3-native conditional writes and introduced a common mapping layer so the
layer tree could be copied without per-environment edits. End-to-end hardening
against real applies kept the security suppression list empty, relocated the
long-lived DNS zones, customer-managed key, and SSM parameters into a foundation
tier, replaced the public collector origin with an internal ALB behind a CloudFront
VPC origin, and split oversized CloudWatch Logs deliveries through a transform Lambda.
Go-live polish serialized the release and apply lanes behind a
FIFO merge queue with branch protection. A client-tool ingestion phase then added a
second, structured logs pipeline — routed by `service.name` and reshaped by the
`cwl_split` Lambda into the platform's canonical event envelope — so Claude Code,
Cowork, and Office telemetry lands in the same lake without disturbing the original
flat-body producers. That same phase later added a third, isolated pipeline that ingests
Claude tools' OTLP metrics signal via an `awsemf` exporter and reshapes the resulting
CloudWatch EMF log events into the same canonical envelope, so token, cost, session, and
active-time counters land in the lake alongside the structured logs, joinable by session and
user. Decisions that were later replaced are kept here and marked
`Superseded` so the reasoning behind each turn stays on the record.

## Decisions

| ADR | Title | Era | Status |
| --- | --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Initial IaC design | Accepted |
| [0002](0002-public-no-auth-telemetry-pipeline.md) | Public no-auth telemetry pipeline | Initial IaC design | Accepted |
| [0003](0003-logs-only-otlp-pipeline.md) | Logs-only telemetry pipeline | Initial IaC design | Accepted |
| [0004](0004-four-account-aws-topology.md) | Four-account AWS topology | Initial IaC design | Accepted |
| [0005](0005-two-tier-module-taxonomy.md) | Two-tier module taxonomy: primitives and references | Initial IaC design | Accepted |
| [0006](0006-identical-code-across-environments.md) | Identical module code across environments; only inputs differ | Initial IaC design | Accepted |
| [0007](0007-per-module-semver-promotion.md) | Per-module semver promotion (prove, release, pin) | Initial IaC design | Accepted |
| [0008](0008-account-id-from-layer-path-basename.md) | Account id from the layer-path basename, not env vars | Initial IaC design | Accepted |
| [0009](0009-terragrunt-state-backend-hardening.md) | Terragrunt state backend hardening | Initial IaC design | Accepted |
| [0010](0010-dynamodb-lock-table-pitr.md) | DynamoDB lock table with PITR for state locking | Initial IaC design | Superseded |
| [0011](0011-parquet-lake-athena-partition-projection.md) | Parquet lake with Athena partition projection over an injected tool key | Initial IaC design | Accepted |
| [0013](0013-long-lived-foundation-tier.md) | Long-lived foundation tier separated from the destroyable service tree | Initial IaC design | Accepted |
| [0014](0014-namespace-derived-resource-naming.md) | Namespace-derived, collision-free resource naming | Instance-set refactor (dev round 2) | Accepted |
| [0015](0015-immutable-numbered-instance-sets.md) | Immutable numbered instance sets with DNS blue/green cutover | Instance-set refactor (dev round 2) | Accepted |
| [0016](0016-dev-rebuild-destroy-recreate.md) | Dev-rebuild: destroy and recreate from final code, never state-migrate | Instance-set refactor (dev round 2) | Accepted |
| [0017](0017-s3-native-state-locking.md) | S3-native conditional-write state locking (use_lockfile) | E8 copyable-tree refactor | Accepted |
| [0018](0018-common-mapping-layer.md) | common/ mapping layer and bare-basename account.hcl for a copyable tree | E8 copyable-tree refactor | Accepted |
| [0019](0019-security-by-hardening-empty-suppression.md) | Security by hardening: keep the suppression list empty | E2E hardening (real-apply) | Accepted |
| [0020](0020-dns-cmk-ssm-to-foundation-layer.md) | Move per-env DNS zones, CMK, and SSM into the foundation layer | E2E hardening (real-apply) | Accepted |
| [0021](0021-collector-public-custom-origin.md) | CloudFront public custom origin pointed at the collector FQDN | E2E hardening | Superseded |
| [0022](0022-internal-alb-cloudfront-vpc-origin.md) | Internal ALB behind CloudFront over a VPC origin | E2E hardening | Accepted |
| [0023](0023-cwl-split-transform-lambda.md) | cwl-split transform Lambda for any-size CWL delivery | E2E hardening | Accepted |
| [0027](0027-defer-merge-queue-run-cancellation-hygiene.md) | Defer GitHub Merge Queue; rely on run-cancellation hygiene | Go-live polish | Superseded |
| [0028](0028-fifo-merge-queue-release-lanes.md) | FIFO-serialized release/apply lanes plus GitHub Merge Queue and branch protection | Go-live polish | Accepted |
| [0029](0029-dual-logs-pipeline-claude-reshape.md) | Dual logs pipeline with a Claude-tool reshape in cwl_split | Claude-tool ingestion | Accepted |
| [0030](0030-enum-projection-tool-partition.md) | Governed enum projection for the tool partition (amends 0011) | Prod queryability | Accepted |
| [0031](0031-claude-metrics-awsemf-pipeline.md) | Claude-tool metrics via a dedicated awsemf pipeline (amends 0003) | Claude-tool ingestion | Accepted |
