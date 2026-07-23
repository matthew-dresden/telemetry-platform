# 0014. Namespace-derived, collision-free resource naming

- Status: Accepted
- Era: Instance-set refactor (dev round 2)

## Context

Resource names were env-scoped and hand-constructed. Two problems followed from
that. Env-scoped names cannot coexist: two numbered serving sets, or two
environments, that build the same logical resource collide on a single global
name. And hand-constructed names drift from tree position, so a name no longer
reflects where in the live tree the unit actually lives. The immutable
instance-set model requires multiple numbered sets to stand up side by side, which
is impossible while names are env-scoped or assembled inside modules.

## Decision

Define one canonical identity, the **namespace**, derived entirely from a unit's
position in the `terragrunt/live/` tree. `root.hcl` joins six fields —
`product`, `region`, `environment`, `environment_instance`, `service`,
`service_instance` — with `-`, replacing any in-field `-` with `_` first so each
field stays a single token. A `namespace_dns` form collapses every `_` to `-` for
`_`-hostile resource kinds (S3, DNS, ACM, CloudFront).

The Terragrunt layer computes every resource name and fits it to the target
resource kind's length and charset constraints (`terragrunt/common/naming.hcl`);
modules never construct names and accept every name as an explicit input. When a
name would exceed a length limit, it is truncated to a prefix plus an 8-character
`md5` suffix taken over the canonical namespace, guaranteeing uniqueness even when
sibling units share the truncated prefix. The hash is hex because there is no
Terraform `base32` builtin. Every resource also carries one tag per namespace
field via provider `default_tags`, so a length-fitted or hashed name is always
recoverable from its tags.

## Consequences

Names are collision-free by construction. Numbered instance sets (`000`, `001`, …)
and parallel environments coexist because every name is set-scoped through the
namespace. Copying an environment or set folder produces correct, tree-following
names — including the remote-state bucket name derived from `namespace_dns` — with
no per-name edits. The trade-offs: hashed names are opaque on their own, mitigated
by the per-field tags that recover the originating fields; and modules can never
assume a name, so every name must be threaded in as an input.

See the [ADR index](README.md).
