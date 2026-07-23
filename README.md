# telemetry-platform

`telemetry-platform` is an AWS-native usage-analytics platform delivered as infrastructure-as-code.
Client tools POST OTLP/HTTP log records (and a metrics signal) to a public collector edge; a
serverless pipeline lands them as partitioned Parquet in an S3 data lake, cataloged by AWS Glue and
queryable through Amazon Athena. It is the ingest-and-lake half of a telemetry system: it captures
usage events durably and cheaply and exposes them for query, and any downstream reporting or BI tool
attaches to the Athena/Glue endpoint out of band.

The records travel through the pipeline in two stages:

- **Ingest:** a public edge of CloudFront and WAF, an internal ALB, and an ADOT collector on ECS
  Fargate.
- **Process and land:** CloudWatch Logs, a transform Lambda, and Kinesis Firehose landing to an S3
  Parquet data lake, cataloged by Glue and queried through a cost-capped Athena workgroup.

This repository holds the Terraform modules and the Terragrunt live tree that deploys them across a
multi-account AWS topology. This README is the documentation hub: start here, then follow the links
into `docs/` for depth.

## Table of contents

- [What this is](#what-this-is)
- [Quickstart](#quickstart)
- [Contributing](#contributing)
- [Continuous integration and delivery](#continuous-integration-and-delivery)
- [Documentation](#documentation)
- [License](#license)

## What this is

The infrastructure is a two-tier Terraform module taxonomy under `providers/aws/`
(reusable `primitives/` composed by `references/`) deployed by a Terragrunt live tree under
`terragrunt/`. Sandbox and prod share identical module code and versions; only inputs differ per
environment, resolved from the `terragrunt/common/*.json` mapping layer.

The ingest pipeline accepts the OTLP **logs** and **metrics** signals (metrics via a dedicated
client-tool pipeline); there is no traces path at the edge. The pipeline terminates at the queryable
data lake — S3 Parquet described by a Glue catalog table and read through an Athena workgroup.

For depth, follow these into `docs/`:

- [docs/deploy-from-scratch.md](docs/deploy-from-scratch.md) — stand up your own instance end to end.
- [docs/architecture.md](docs/architecture.md) — the end-to-end system and pipeline design.
- [docs/terragrunt-concepts.md](docs/terragrunt-concepts.md) — deployment terminology and structure
  (module taxonomy, the scope hierarchy, namespacing, and instance sets).
- [docs/adr/README.md](docs/adr/README.md) — the architecture decision records behind the major
  design choices.

## Quickstart

- **Deploy your own instance:** [docs/deploy-from-scratch.md](docs/deploy-from-scratch.md) is the
  primary quickstart. It walks a fork from prerequisites and toolchain (`make configure`), through
  filling in `terragrunt/common/*.json`, the GitHub Environments and OIDC roles, the bootstrap tier,
  and the service tier, to sending a test event and querying Athena.
- **Send telemetry (producers):** point an OTLP/HTTP exporter at the public collector endpoint and
  emit log records in the expected shape. The endpoints, event shape, encodings, size limits, and
  worked examples are in [docs/sending-telemetry.md](docs/sending-telemetry.md).
- **Query the data (consumers):** read the lake directly with Amazon Athena. The schema, the `tool`
  partition, and ready-to-run queries are in [docs/data-model.md](docs/data-model.md).

To prove a freshly deployed or changed environment really ingests and lands synthetic telemetry, use
the standalone OTLP end-to-end harness documented in [docs/e2e-harness.md](docs/e2e-harness.md).

## Contributing

Changes follow a prove-then-promote workflow whose exact path depends on what you touch:

- Terraform modules are proven in the qa account with Terratest, released by per-module semver,
  then pinned and applied. See [docs/module-promotion-flow.md](docs/module-promotion-flow.md) and
  [docs/terraform-module-sourcing.md](docs/terraform-module-sourcing.md).
- Terragrunt live-tree changes are validated on the pull request, merged through a serialized merge
  queue, and applied to production through a gated, FIFO-serialized lane.
- Sandbox is applied locally and is never deployed by CI; production-affecting units run through
  one repo-wide serialized apply queue gated by a protected GitHub Environment.

The full per-scope workflow, including in-place versus immutable changes and zero-downtime
cutovers, is in [docs/contributing.md](docs/contributing.md). The canonical reference for every
workflow and make target that backs it is [docs/release-pipeline.md](docs/release-pipeline.md).

## Continuous integration and delivery

The repository runs distinct GitHub Actions workflows for validation, release, deployment, and
scanning. For the triggers, jobs, gates, make targets, and the flow diagrams, see the canonical
reference in [docs/release-pipeline.md](docs/release-pipeline.md).

- **Pull-request validation and merge queue** — every PR runs the quality gates and Terragrunt
  plans; `main` is protected and lands PRs one at a time through a FIFO merge queue.
- **Module-to-leaf release** — a merged module change derives a SemVer bump from the PR title,
  tags the module, and pins the leaf source to the new tag.
- **Terragrunt apply** — sandbox is applied locally, while production runs through one serialized,
  gated apply lane.
- **Terratest and the daily sweep** — modules are proven against the qa account, with a daily cron
  sweep that removes orphaned test resources.
- **CodeQL and the safety net** — CodeQL static analysis runs on every change and weekly, and a
  five-minute watchdog releases any stale release lock.

## Documentation

Everything lives under `docs/`. Start with deployment and architecture, then the user guides, the
operational runbooks, and the contributing material.

| Group | Document | What it covers |
| --- | --- | --- |
| Getting started | [deploy-from-scratch](docs/deploy-from-scratch.md) | Stand up your own instance end to end: prerequisites, config, GitHub Environments and OIDC, bootstrap and service tiers, first event, and teardown. |
| Architecture and concepts | [architecture](docs/architecture.md) | End-to-end system and pipeline; the logs-and-metrics signal scope. |
| Architecture and concepts | [terragrunt-concepts](docs/terragrunt-concepts.md) | The single source for deployment terminology and structure: module taxonomy, scope hierarchy, namespacing, singletons, glossary. |
| Architecture and concepts | [instance-sets-architecture](docs/instance-sets-architecture.md) | Immutable numbered instance sets at the environment-set and resource-set levels, with blue/green promotion. |
| Architecture and concepts | [terraform-module-sourcing](docs/terraform-module-sourcing.md) | Const-source variables and the `use_pinned_module_sources` toggle. |
| Architecture and concepts | [module-promotion-flow](docs/module-promotion-flow.md) | Module-to-leaf semver promotion. |
| Architecture and concepts | [data-model](docs/data-model.md) | The event schema, the `tool` projection, and Athena query examples. |
| Architecture and concepts | [security](docs/security.md) | Security and compliance posture for the public ingest edge, the pipeline, and the data lake. |
| Architecture and concepts | [adr/README.md](docs/adr/README.md) | The architecture decision record index (original design context; the account-topology and retired portal/serve-tier records are historical). |
| Architecture and concepts | [iac-coverage](docs/iac-coverage.md) | Which AWS resources are IaC-managed and proven, and the documented exceptions. |
| Using the platform | [sending-telemetry](docs/sending-telemetry.md) | Producer setup, endpoints, event shape, encodings, limits, and examples, including structured client-tool telemetry and usage metrics. |
| Using the platform | [onboarding-a-tool](docs/onboarding-a-tool.md) | How to onboard a new tool: the single-edit tool-registry entry that drives the Glue enum, the collector's structured-OTLP routing, and the reshape's service-name-to-tool map. |
| Using the platform | [e2e-harness](docs/e2e-harness.md) | The synthetic OTLP end-to-end test harness. |
| Operating the platform | [bootstrap-ordering](docs/bootstrap-ordering.md) | The order of bringup. |
| Operating the platform | [bootstrap-runbook](docs/bootstrap-runbook.md) | First-time state-backend bringup steps. |
| Operating the platform | [dns-cutover-runbook](docs/dns-cutover-runbook.md) | DNS cutover (CNAME flips). |
| Operating the platform | [instance-set-operations](docs/instance-set-operations.md) | Add a new environment or set by the copy method, and revert to an older set. |
| Operating the platform | [terragrunt-operational-runbook](docs/terragrunt-operational-runbook.md) | Day-2 plan, apply, and troubleshooting operations. |
| Operating the platform | [release-pipeline](docs/release-pipeline.md) | The canonical CI/CD reference. |
| Operating the platform | [troubleshooting](docs/troubleshooting.md) | A symptom-driven failure-to-fix reference. |
| Contributing | [contributing](docs/contributing.md) | The full dev-to-prod workflow per code scope. |

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full text.
