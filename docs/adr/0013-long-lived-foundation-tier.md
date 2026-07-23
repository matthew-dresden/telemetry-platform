# 0013. Long-lived foundation tier separated from the destroyable service tree

- Status: Accepted
- Era: Initial IaC design

## Context

A destroy of the telemetry platform's service tree must not churn DNS, remote state, or
encryption keys. The hosted zone's name servers, the Terraform state backend, and the
customer-managed KMS keys are slow or disruptive to recreate: changing the hosted zone's name
servers forces an apex re-delegation and a DNS propagation wait, and the state backend holds
the record of every other unit in the account. If these resources lived in the same tier as
the per-environment service-instance sets, tearing down and rebuilding a set would disturb
them.

## Decision

Separate a long-lived foundation tier from a destroyable service tree.

The foundation tier holds the resources that must survive any service teardown:

- The remote-state backend: the state S3 bucket, its customer-managed KMS key, the access-log
  bucket, and the shared artifact bucket.
- The CICD identity roles that GitHub Actions assumes to plan and apply.
- The DNS foundation: the long-lived hosted zone, the configuration KMS key, and the SSM seed
  parameters.
- The account-wide singletons that the service-instance sets build on.

The destroyable service tree holds the per-environment service-instance sets, which are
immutable and numbered. A change is rolled out by standing up a new set and flipping traffic
to it rather than mutating a live set, and an old set is destroyed once traffic has moved.

## Consequences

A service-instance set can be destroyed and recreated without disturbing DNS, remote state, or
encryption keys. The hosted zone's name servers stay fixed, so no apex re-delegation or DNS
propagation wait is incurred on each rebuild, and the state backend and KMS keys persist across
teardowns. The cost is an explicit tier boundary that operators and CICD must respect: the
foundation tier is applied once per account out of band, while the service tree is applied
repeatedly. The detailed bring-up order is documented in
[bootstrap ordering](../bootstrap-ordering.md).

See the [ADR index](README.md).
