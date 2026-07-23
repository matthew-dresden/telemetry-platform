# 0007. Per-module semver promotion (prove, release, pin)

- Status: Accepted
- Era: Initial IaC design

## Context

A change to an in-repo Terraform module under `providers/aws/` must reach
production along a predictable path that validates against real AWS before any
immutable artifact is cut. Two facts shape the design:

- A reference module never hardcodes a child `source`. It declares a
  `const = true` variable whose default is an in-repo relative path, and the
  Terragrunt leaf decides at deploy time whether that default stands or is
  overridden with a pinned git URL.
- Each environment resolves `use_pinned_module_sources` from
  `terragrunt/common/env_accounts.json`. When the value is `false`, a leaf
  sources the working-tree module by relative path via `get_repo_root()`; when
  `true`, a leaf pins an immutable git tag with `?ref=`.

```json
{
  "envs": {
    "sandbox": { "use_pinned_module_sources": false },
    "prod":    { "use_pinned_module_sources": true },
    "qa":      { "use_pinned_module_sources": false }
  }
}
```

If a reference module hardcoded a pinned git URL for each child, no one could run
`terraform init -backend=false`, `terraform validate`, or Terratest against that
module until the `?ref=` tag already existed in the remote. Every iteration would
require a full release round-trip before local work could begin. The const-source
variable plus the toggle removes that coupling: local development resolves against
the working tree, and real environments inject pinned URLs at deploy time.

## Decision

Promote a module change through three moves:

- **Prove against real AWS.** The module is exercised by its Terratest suite in
  the `qa` account through its `examples/` fixtures, and applied in the `sandbox`
  account from the in-repo source. Both run with `use_pinned_module_sources =
  false`, so neither needs a tag to exist.
- **Release a semver tag.** Once the change lands on `main`, the release pipeline
  derives the version bump and cuts a per-module tag of the form
  `<module_path>/vX.Y.Z`, then creates the matching release. The release pipeline
  is the only path to a pinnable version.
- **Pin from prod leaves.** The `prod` account resolves
  `use_pinned_module_sources = true`, so its leaves reference the immutable URL.

```hcl
source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/route53-record?ref=providers/aws/primitives/route53-record/v1.0.2"
```

## Consequences

- Production is pinned to immutable artifacts: each `?ref=` resolves to a specific
  released commit that cannot change after the tag is cut.
- Only prod consumes a released tag. The `qa` and `sandbox` accounts run the
  module straight from the working tree, which is exactly what lets a change be
  proven before any release tag exists.
- A prod apply against a tag the release pipeline has not published fails loudly
  at `terraform init` with an actionable "module not found" error, rather than
  silently falling back to local code, so prod can only ever run a released
  version.
- Two carve-outs do not pin a released tag, by design: bootstrap leaves always
  source the in-repo module via `get_repo_root()` because they create the state
  backend before any tag exists, and external `matthew-dresden/terraform-modules`
  sources stay hardcoded immutable literals because they live on a separate
  release cadence.

For the full promotion mechanics, see
[../module-promotion-flow.md](../module-promotion-flow.md).

See the [ADR index](README.md).
