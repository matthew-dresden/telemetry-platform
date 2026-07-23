# 0016. Dev-rebuild: destroy and recreate from final code, never state-migrate

- Status: Accepted
- Era: Instance-set refactor (dev round 2)

## Context

Before the platform goes live, nothing it stands up is permanent. The
environments are pre-production rebuilds whose only purpose is to prove that the
declared code applies cleanly, so any data, state, or running resource they hold
is disposable. In that window, carrying old state forward across a structural
change is a liability rather than a convenience: a state migration encodes
assumptions about the shape of the resources it migrates, and those assumptions
break the moment the tree is restructured — directories renamed, units split,
namespaces re-derived, or instance sets renumbered. Migrations are most fragile
exactly when the code is still moving the most.

The live tree is keyed by environment (`sandbox`, `prod`, `qa`) and built from
immutable, numbered instance sets that are copied to a new number rather than
edited in place. That structure already makes a clean copy cheap and a clean
teardown safe, because the long-lived foundation tier — the state backend, KMS
keys, hosted-zone foundation, and account singletons — is separated from the
destroyable service tree.

## Decision

Rebuild pre-go-live environments by destroying the service tree and recreating
it fresh from the final code, never by migrating prior state.

- The source of truth is the code in the env-keyed, copyable live tree, not the
  state left behind by an earlier apply.
- A restructure — renaming directories, splitting units, re-deriving namespaces,
  or renumbering instance sets — is rolled out by recreating from the new code,
  not by hand-editing or migrating the existing state to match.
- The long-lived foundation tier is exempt: it is destroy-protected by design
  and survives the rebuild, so only the destroyable service tree is recreated.

## Consequences

Every rebuild is a fresh apply from the final code, which proves the code is
correct end to end: if the tree applies cleanly from nothing, there is no hidden
dependence on state that an earlier apply happened to leave behind, and no drift
between what the code declares and what is running. Restructures stay cheap,
because they never require writing a migration to reshape live state. The
tradeoff is that ingested data in a rebuilt environment is not preserved — this
is acceptable only while the environments are pre-go-live and disposable, and
the decision must be revisited before any environment holds data that must
survive a rebuild.

For the set-copy and teardown mechanics this relies on, see the
[instance-set architecture](../instance-sets-architecture.md).

See the [ADR index](README.md).
