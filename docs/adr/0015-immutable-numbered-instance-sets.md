# 0015. Immutable numbered instance sets with DNS blue/green cutover

- Status: Accepted
- Era: Instance-set refactor (dev round 2)

## Context

The serving path is built from set-scoped resources: an ACM certificate, a
CloudFront distribution, an ALB, an ECS service, and DNS records. Editing a live
set in place drifts the running infrastructure away from its declared state.
Worse, some set-scoped resources cannot be changed in place safely — renaming a
live ACM certificate deadlocked the apply because the serving distribution
depended on the very resource being mutated. A change to configuration or a
module version needed a path that never touches the resources the running set
depends on, and a rollback that does not rely on reverting edits inside a live
set.

## Decision

Deploy serving infrastructure as immutable, numbered instance sets. Each set
owns its own certificate, CloudFront distribution, ALB, ECS service, and DNS
records, every one named from the set number so two sets coexist with zero name
collisions.

A change is never an in-place edit. Stand up a brand-new numbered set alongside
the live one and apply it inactive, with no client traffic, then test it on its
own set-scoped address. Promotion flips a single active switch and re-applies
the pretty CNAME unit, which retargets the stable, human-facing name at the
newly active set.

```hcl
locals {
  active = "000"
}
```

The pretty unit composes a name-to-name CNAME from configuration plus the active
switch, so retargeting carries no dependency on the per-set DNS unit:

```text
collector.<pretty_apex>   CNAME   collector-<env_active>.<service_apex>
```

Long-lived resources — the shared singleton tier and the bootstrap foundation,
including the data lake — sit outside any set and survive every swap, so a
cutover never moves or drops ingested data.

## Consequences

Cutover and rollback become a reversible DNS retarget: flip the active switch,
re-apply the pretty unit, and clients follow the short-TTL CNAME to the new set;
reverse the same one line to roll back while the previous set is still standing.
Running sets are frozen artifacts, so configuration drift is eliminated — changes
ship as whole new sets rather than in-place patches. The tradeoff is that two
complete sets run side by side during a cutover window until the old set is
retired, and each set carries its own certificate, distribution, and serving
stack, increasing the number of resources under management.

See the [ADR index](README.md).
