# 0004. Four-account AWS topology

- Status: Accepted
- Era: Initial IaC design

## Context

The telemetry platform spans development, continuous integration, production, and DNS ownership. These concerns have different blast radiuses and different trust requirements, and they must not interfere with one another. In particular, production must never be a test target: running module tests or speculative applies against the production account would risk corrupting live state and live infrastructure.

AWS account boundaries are the strongest isolation control available. Sharing a single account across these concerns would couple their state backends, IAM roles, and resource namespaces, and would make it impossible to grant continuous integration the broad permissions it needs to create and destroy test infrastructure without also exposing production.

## Decision

Adopt four AWS accounts, each with a distinct role:

- Sandbox — the development account. It is applied locally by an operator (`ci_deploy = false`) rather than by CICD, giving day-to-day development a low-friction target that is fully isolated from production.
- QA — the continuous-integration account. CICD assumes a Terratest role (`telemetry-platform-gha-terratest`) through GitHub OIDC and runs module tests here. QA is the only test target.
- Prod — the production account, deploy-only. CICD plans and applies through separate plan and apply roles assumed via GitHub OIDC. No tests run against production.
- Root / DNS owner — owns the long-lived apex hosted zone. A DNS-writer role (`telemetry-platform-dns-writer`), role-chained from the prod apply role, writes the apex delegation records.

Each account hosts its own GitHub OIDC roles and its own hardened remote-state backend, so the accounts can be bootstrapped and operated independently. The bringup order and the dependencies between these tiers are described in [bootstrap-ordering.md](../bootstrap-ordering.md).

## Consequences

Account isolation is a primary control. Production state and infrastructure cannot be reached by tests, which are confined to QA, and development churn in sandbox cannot affect either. The separation also lets each account grant only the permissions its role requires: broad create/destroy rights in QA, a constrained plan-and-apply path in prod, and a narrowly scoped DNS-writer in the root account.

Cross-account work — for example writing apex name-server records in the DNS-owner account during a production apply — requires explicit role assumption or role-chaining rather than ambient access, which adds configuration but preserves least privilege. The cost of the topology is that all four accounts must each be bootstrapped with a GitHub OIDC provider and a remote-state backend before CICD can operate.

See the [ADR index](README.md).
