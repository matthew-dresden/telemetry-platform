# 0006. Identical module code across environments; only inputs differ

- Status: Accepted
- Era: Initial IaC design

## Context

The platform is delivered as Terraform reference modules deployed through a
Terragrunt live tree across two environments, sandbox and prod. A tempting
shortcut is to fork module code per environment so that each environment can be
tuned independently.

Forks let sandbox and prod diverge. A fix or hardening proven in one environment
is not guaranteed to be present in the other, drift accumulates silently, and
"works in sandbox" stops predicting "works in prod." Once two copies of the same
module exist, every future change has to be made and reviewed twice, and the two
copies drift further apart over time.

## Decision

Both environments deploy byte-identical reference-module code at identical
module versions. The only thing that differs between environments is the
resolved input values, which are drawn from the shared per-environment
configuration in `common/*.json` (keyed by environment and layered through the
Terragrunt scope tiers). Environment-specific behavior is therefore expressed as
data, never as a code branch.

Any change is proven in both environments rather than in one. The same artifact
is exercised against sandbox and prod inputs before the change is considered
done.

## Consequences

Parity is structural rather than a matter of discipline. Because the code is the
same artifact in both environments, divergence cannot accumulate, and a change
validated against one environment behaves the same way against the other once the
differing inputs are accounted for.

The cost is that any genuinely environment-specific value must be modeled as an
input and added to `common/*.json`, not hard-coded into a module, and that a
change must be exercised against both environments before it ships.

See the [ADR index](README.md).
