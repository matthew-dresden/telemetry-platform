# 0008. Account id from the layer-path basename, not env vars

- Status: Accepted
- Era: Initial IaC design

## Context

Every Terragrunt unit must resolve which AWS account and region it applies to. One option is to inject that identity through environment variables: read the target account id and region from ambient process state at apply time.

Environment-variable injection is environment-coupled. The same configuration resolves to a different account depending on the variables that happen to be set in the shell or runner that invokes it, so a unit's target is not reproducible from the configuration alone and a folder cannot be copied to a new scope without also re-deriving its ambient settings.

## Decision

Derive AWS identity from the directory layer path rather than from environment variables.

The region layer resolves its value directly from the basename of its own directory:

```hcl
locals {
  aws_region = basename(get_terragrunt_dir())
}
```

The env layer's basename selects the account. `account.hcl` uses that basename to key `common/env_accounts.json` and resolve the account id, which abstracts the literal account number out of the path while keeping the selection a function of the path:

```hcl
locals {
  _env          = basename(get_terragrunt_dir())
  _env_accounts = jsondecode(file("${get_repo_root()}/terragrunt/common/env_accounts.json"))
  _entry        = lookup(local._env_accounts["envs"], local._env, null) != null ? local._env_accounts["envs"][local._env] : tobool("ERROR: env-class not found in env_accounts.json")

  aws_account_id = local._entry["account_id"]
}
```

The generated provider then asserts that resolved account id as its `allowed_account_ids`, so a unit can only apply against the account its path selects:

```hcl
provider "aws" {
  region              = "${local.region}"
  allowed_account_ids = ["${local.aws_account_id}"]
}
```

No account id, region, or `allowed_account_ids` value is read from an environment variable.

## Consequences

Identity is a pure function of the path. The region comes from the region-layer basename, the account is selected by the env-layer basename, and the provider's `allowed_account_ids` derives from that same path-resolved account id.

Because nothing in the identity chain depends on ambient process state, the same configuration always resolves to the same account and region regardless of how it is invoked, copying a folder to a new env-class resolves the new account and region with no edits to the copied files, and the provider's account guard fails fast if a unit is ever run against the wrong account.

See the [ADR index](README.md).
