# Deploy from scratch

This is the primary quickstart for standing up your own instance of `telemetry-platform`. It walks a
fork from an empty set of AWS accounts to a live ingest-and-lake pipeline that you can send a test
event to and query in Athena. The platform is vendor-neutral: every organization-, account-,
domain-, and role-specific value is an input you supply through `terragrunt/common/*.json` and your
own CI configuration — nothing is baked into the modules.

What you are deploying (the terminus is Athena over the Glue-cataloged Parquet lake):

```text
client tools --> CloudFront + WAF --> internal ALB --> ADOT collector (ECS Fargate)
             --> CloudWatch Logs --> transform Lambda --> Kinesis Firehose
             --> S3 Parquet data lake --> Glue catalog --> Athena
```

The steps below reference the deeper runbooks rather than duplicating them. Read this end to end
before you start; the ordering matters.

## 1. Prerequisites

**AWS multi-account topology.** The design separates roles across AWS accounts (see
[adr/0004-four-account-aws-topology.md](adr/0004-four-account-aws-topology.md) for the original
rationale). A fork supplies its own account IDs; the reference layout uses four roles:

| Account role | Purpose |
| --- | --- |
| `prod-infra` | Production service account (the live pipeline). |
| `sandbox` | Disposable environment for dev/verification (applied locally, never by CI). |
| `qa-infra` | Runs Terratest module proofs. |
| `dns-owner` | Holds the parent DNS hosted zone and the delegated per-env zones. |

You do not need all four to begin — a single sandbox account is enough to prove the pipeline — but the
config files expect each account role you intend to use to be filled in.

**Toolchain.** The pinned toolchain is declared in `.tool-versions` and provisioned reproducibly.
Install the bootstrap prerequisites (`git`, GNU `make`, and [`uv`](https://github.com/astral-sh/uv)),
then run:

```bash
make configure
```

`make configure` runs `tools-ensure` (downloads the pinned Terraform, Terragrunt, terraform-docs,
and other binaries named in `.tool-versions`), `py-sync` (`uv sync --frozen` for the Python
environment), and `hooks-install` (the pre-commit/pre-push git hooks). Re-running it is a no-op once
versions match.

**AWS credentials.** Configure a named AWS profile for each account you will deploy to (the profile
names are inputs — see step 2). The named profiles must be able to assume the deploy roles you
create in step 3.

## 2. Fill in `terragrunt/common/*.json`

All environment-specific inputs live in the `terragrunt/common/` mapping layer, so the live tree
under `terragrunt/live/` never contains a literal account ID, domain, or email. Fill these in for
your fork:

| File | What you set |
| --- | --- |
| `platform.json` | The one file that carries your identity: `org`, `repo`, `product`, `primary_region`, and `module_source_host` (`github.com`). Every pinned module source (`git::https://<host>/<org>/<repo>.git//...`), the Go module path, the IAM deploy-role prefix, and the OIDC subject claims derive from these. `product` must match the `terragrunt/live/<product>/` folder name. |
| `accounts.json` | Keyed by 12-digit account ID: `account_role`, `aws_profile`, `ci_deploy` (and `ci_deploy_on_demand`), `deploy_role_name`, `plan_role_name`, `is_dns_owner`, and `dns_owner_zone_id` on the DNS-owner account. |
| `env_accounts.json` | Env-keyed resolution: `envs.<sandbox\|prod\|qa>.{account_id, aws_profile, use_pinned_module_sources}` plus `dns_owner.{account_id, aws_profile}`. Copying an env folder to a new env-class works with zero path edits because the account follows from this file. |
| `domains.json` | Per-env `dns_service_apex`, `dns_pretty_apex`, and `enable_custom_domain`. Use your own domain (the reference uses `telemetry.example.com` with `prod.`/`sandbox.`/`qa.`/`bootstrap.` subdomains). |
| `contacts.json` | Per-env `alert_emails` and `budget_emails` (CloudWatch alarms and budget notifications). |
| `networks.json` | Namespace-keyed `vpc_cidr_block` for each collector-ingestion VPC. |
| `oidc-roles.json` | Keyed by account ID → `roles.<role-name>.{sub, managed_policy_arns, inline_policies, description}`. The `sub` is the GitHub OIDC subject claim (`repo:<org>/<repo>:...`) that scopes each CI role. This is the declarative source for the roles you create in step 3. |
| `cross-account-read.json` | Optional external principals granted cross-account READ on the data lake, keyed by a stable label → `{account_id, description}`. Empty means no external grant. This is how a downstream BI/analytics account attaches to the Athena/Glue terminus. |

There is **no fallback**: an unregistered value fails fast rather than defaulting silently. Placeholder
values in the committed files (`example-org`, `111111111111`, `<REAL_Z_ID>`, `<FILL:KMS_KEY_UUID>`)
are deliberate fail-fast markers — replace every one for the accounts you deploy.

Also register the tools whose telemetry you will ingest in
[`terragrunt/common/tool-registry.json`](../terragrunt/common/tool-registry.json) — one entry per
tool drives the Glue `tool` enum, the collector's structured-OTLP routing, and the reshape map. See
[onboarding-a-tool.md](onboarding-a-tool.md).

## 3. Consumer CI setup (GitHub Environments, OIDC, and Actions variables)

These are configured once in your GitHub repository, not committed to it.

**GitHub OIDC provider and IAM roles.** In each AWS account you deploy to, create the GitHub OIDC
identity provider and the apply/plan IAM roles whose names and trust (`sub`) claims you declared in
`oidc-roles.json` — for example `telemetry-platform-gha-tg-plan` (PR plans), `telemetry-platform-gha-tg-apply`
(production apply, trusted for the `prod-apply` environment), and `telemetry-platform-gha-terratest`
(qa proofs). The `oidc-bootstrap` reference module provisions these declaratively; you apply it as
part of the bootstrap tier (step 4). The role ARNs are then referenced by CI.

**GitHub Environments.** Create these four protected environments and attach approvers/rules as your
governance requires:

| Environment | Gates |
| --- | --- |
| `prod-apply` | Production Terragrunt apply lane (required reviewers recommended). |
| `terratest-approval` | Approval gate before Terratest runs against the qa account. |
| `release` | The module tag-and-release lane. |
| `sandbox-apply` | On-demand sandbox apply lane. |

**Repository Actions variables.** Set these as repo (or environment) variables — they are documented
inputs, never baked into the code:

| Variable | Meaning |
| --- | --- |
| `AWS_DEFAULT_REGION` | The primary region (matches `platform.json`'s `primary_region`). |
| `AWS_QA_TERRATEST_ROLE_ARN` | ARN of the qa Terratest role. |
| `AWS_QA_SANDBOX_DOMAIN` | Domain used by Terratest/sandbox fixtures. |
| `AWS_QA_SANDBOX_PARENT_ZONE_ID` | Parent hosted-zone ID for those fixtures. |
| `OBSERVABILITY_BUDGET_AMOUNT` | Monthly budget threshold. |
| `OBSERVABILITY_BUDGET_EMAIL` | Budget/SNS notification address. |
| `LOCK_MAX_AGE_MINUTES` | Release-lock watchdog max age. |
| `RELEASE_WORKFLOW_FILE_NAME` | The release workflow file the safety-net watchdog targets. |
| `PERF_READINESS_POLL_INTERVAL` / `PERF_READINESS_TIMEOUT` | Readiness polling for the optional load-test lane. |

## 4. Bootstrap-tier bringup

The bootstrap (foundation) tier is long-lived and stands up the state backend, the CI OIDC roles,
and the per-env DNS zones **before** any service infrastructure. It lives under
`terragrunt/live/<product>/<region>/bootstrap/<role>_role/{state-bootstrap,oidc-bootstrap,dns-prod-zone}`.

Bring it up in dependency order — state backend first, then OIDC roles, then DNS zones. The exact
order and the one-time chicken-and-egg handling (local state for the very first `state-bootstrap`
apply, then migrating to the remote backend) are in:

- [bootstrap-ordering.md](bootstrap-ordering.md) — the tier ordering.
- [bootstrap-runbook.md](bootstrap-runbook.md) — the step-by-step first-time bringup.

Applies are driven by the Terragrunt make targets, scoped to a unit (or subtree) with
`INCLUDE_DIR_FLAGS`:

```bash
make tg-plan  INCLUDE_DIR_FLAGS='--queue-include-dir terragrunt/live/telemetry/us-east-1/bootstrap/sandbox_role/state-bootstrap'
make tg-apply INCLUDE_DIR_FLAGS='--queue-include-dir terragrunt/live/telemetry/us-east-1/bootstrap/sandbox_role/state-bootstrap'
```

(Use the include-dir flags exactly as the runbook shows for your Terragrunt version.)

## 5. Service-tier apply

With the foundation in place, apply the service tier — the ingest-and-lake pipeline — under
`terragrunt/live/<product>/<region>/<env>/`. This is where `collector-ingestion` (the CloudFront +
WAF edge, internal ALB, and ADOT collector), the ACM/DNS collector units, and the shared singletons
(the `data-lake` and `observability` references) come up. Sandbox is applied locally; production
runs through the gated CI apply lane.

Day-2 plan/apply mechanics, unit scoping, and dependency handling are in
[terragrunt-operational-runbook.md](terragrunt-operational-runbook.md). The immutable numbered
instance-set model (how a change stands up a new set and cuts traffic over) is in
[instance-sets-architecture.md](instance-sets-architecture.md).

## 6. Send a test event

Point an OTLP/HTTP exporter at the collector endpoint for your environment (the `collector.<env>`
FQDN derived from `domains.json`) and emit a log record in the expected shape. The endpoints, event
body contract, encodings, size limits, and worked `curl` examples are in
[sending-telemetry.md](sending-telemetry.md).

To prove the whole path — ingest, land, and query — with synthetic traffic rather than a real tool,
run the standalone OTLP end-to-end harness in [e2e-harness.md](e2e-harness.md).

## 7. Query Athena

Once records land, query them in the cost-capped Athena workgroup. The Glue `telemetry_events`
table, the `tool`/`dt` partition projection, and ready-to-run queries are in
[data-model.md](data-model.md). This Athena/Glue endpoint is the platform's terminus: any downstream
reporting or BI tool attaches here with its own credentials, and a consumer in another AWS account is
granted access through the optional `cross-account-read.json` grant.

## 8. Teardown

Development environments are disposable — the model is to get the code right, then destroy and
recreate rather than migrate state (see
[adr/0016-dev-rebuild-destroy-recreate.md](adr/0016-dev-rebuild-destroy-recreate.md)). Destroy the
service tier with the scoped teardown lane, leaving the long-lived foundation intact:

```bash
make tg-destroy INCLUDE_DIR_FLAGS='--queue-include-dir terragrunt/live/telemetry/us-east-1/sandbox/000/collector-ingestion'
```

Tear down in reverse dependency order (service tier before foundation). The foundation tier (state
backend, OIDC roles, DNS zones) is intentionally durable; destroy it only when retiring the instance
entirely.

## Where to go next

- [architecture.md](architecture.md) — the end-to-end system and pipeline design.
- [terragrunt-concepts.md](terragrunt-concepts.md) — terminology, module taxonomy, and namespacing.
- [security.md](security.md) — the security and compliance posture.
- [release-pipeline.md](release-pipeline.md) — the canonical CI/CD reference.
- [adr/README.md](adr/README.md) — the architecture decision records (original design context).
