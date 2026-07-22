# IaC coverage and documented exceptions

What this is: the coverage contract for AWS resources in this repository — what must be
Infrastructure-as-Code, how that is proven, and the small set of resources that are
deliberately *not* managed here (with the reason for each). Read this when you add a
resource or wonder why a given resource is or is not in Terraform.

## The coverage contract

Every AWS resource that *can* be safely managed by Terraform is managed by this repo's
IaC, and its coverage is proven at two levels:

- **Module level** — the owning primitive or reference module has a Terratest and an
  example that stand the resource up in the qa account, assert, and tear it down.
- **Terragrunt level** — a live leaf under `terragrunt/live/` deploys it, and prod pins the
  released module tag (see [module-promotion-flow.md](module-promotion-flow.md)).

Environments are identical (sandbox and prod share module code and versions; only inputs
differ), so coverage holds in both.

## Documented exceptions

A few resources are intentionally outside the module-plus-Terratest contract. Each is
listed here with its reason and how it is instead assured.

### Cannot be managed by this repository

| Resource | Where it lives | Why it is exempt | Assurance |
| --- | --- | --- | --- |
| Prod "pretty" Route53 records and their state | DNS-owner account | Separate account this repo's deploy identities do not administer. | Managed in the DNS-owner account; the service tree consumes delegation only. |
| S3 telemetry data objects (Parquet) | Data lake bucket | Data, not infrastructure. | Lifecycle-managed by the bucket's IaC-defined rules. |

## Related documentation

- [module-promotion-flow.md](module-promotion-flow.md) — how modules are proven, released, and pinned.
- [terragrunt-concepts.md](terragrunt-concepts.md) — module taxonomy and the deployment structure.
- [../README.md](../README.md) — documentation hub.
