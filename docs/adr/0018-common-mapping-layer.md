# 0018. common/ mapping layer and bare-basename account.hcl for a copyable tree

- Status: Accepted
- Era: Copyable-tree refactor

## Context

Per-env literals scattered across each environment's `account.hcl` made the
deployment tree non-copyable. Every env folder carried hardcoded
per-account and per-env-class values — the AWS account id, the named profile,
the deploy role, the service and pretty domains, and the alarm contacts. Because
those values lived inline in the copied files, standing up a new env-class or
numbered set by copying a folder meant hand-editing every literal in the copy.
That coupling defeated the namespace-derived, copy-to-instantiate model the rest
of the tree relies on: the folder path is supposed to be the only thing that
distinguishes one scope from another.

## Decision

Introduce a shared `common/` mapping layer and resolve every per-scope value from
it by derived scope, leaving the copied files free of per-account or per-env-class
literals.

`common/` holds env- and account-keyed JSON maps — `accounts.json`,
`domains.json`, `contacts.json`, and `env_accounts.json`. `root.hcl` first derives
a unit's identity (account id, env-class, service, instance) from its position in
the `terragrunt/live/` tree, then looks up the per-scope configuration in these
maps keyed by the derived account id and env-class. The composed values
(deploy role, service and pretty apex domains, custom-domain toggle, contacts) are
exposed to leaf units from the root rather than inherited from folder literals.

`account.hcl` is reduced to a bare basename identifier. It derives its env-class
from its own directory basename and resolves only the account id, named profile,
and pinned-source toggle from `env_accounts.json` keyed by that basename. No
per-env literal survives in the copied tree.

The generated AWS provider asserts `allowed_account_ids` built from the derived
account id, so a unit fails fast rather than applying against the wrong account
when ambient credentials do not match the scope its path implies. Every map lookup
is itself fail-fast: a derived account id or env-class absent from its map aborts
parsing with an actionable message naming the missing key and the file to edit,
with no fallback. `common/` is anchored on the repository root, placing it outside
the copy boundary so it resolves from any copied subtree depth.

## Consequences

The tree is copyable. Copying an env or set folder resolves its profile, role,
domains, and contacts purely from the derived path plus the `common/` data, with
zero edits to the copied files. Per-account duplication is eliminated: each value
is declared once in `common/` instead of being repeated across env folders.
Misconfiguration surfaces loudly at parse time — a missing account or env-class
row, or credentials pointed at the wrong account, fails before any AWS call. The
trade-offs: `common/` is now a single shared dependency every unit reads, and
adding a new env-class means adding rows to the relevant `common/` maps rather
than editing a folder.

See the [ADR index](README.md).
