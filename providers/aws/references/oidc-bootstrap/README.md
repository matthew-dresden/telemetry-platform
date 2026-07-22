# oidc-bootstrap

Composes the GitHub Actions OIDC assume-roles for each AWS account into a single
NEW-LOCAL reference so each per-account bootstrap unit can create only the subset of
roles it needs from one pinned source, instead of hand-authoring IAM trust policies
(docs/terragrunt-concepts.md, D8, D40).

The reference root declares no `resource` blocks and creates NO OIDC provider. All
infrastructure is delegated to the `iam-role` primitive wired by a published git tag
(`iam-role` telemetry-platform v1.0.0). The GitHub OIDC identity provider
(`arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com`) is a
one-time operator prerequisite per decision D40 for accounts that host OIDC-trust roles.

## Composed modules

- `oidc_role[*]` (iam-role telemetry-platform v1.0.0) -- one IAM role per entry in the
  `roles` input map. Each role's trust policy is input-driven from the map so the
  same reference serves sandbox, QA, prod, and root accounts without modification (D8).

## Trust policy modes

The module supports two trust-policy modes per role, selected by the role's `trust_policy_json`
and `sub` fields:

1. **OIDC trust (default):** `sub` is set and `trust_policy_json` is empty. The module
   generates an OIDC trust policy using `github_oidc_provider_arn` as the federated
   principal and `sub` as the `token.actions.githubusercontent.com:sub` condition. The
   `:sub` condition uses the `StringLike` operator so wildcard `sub` patterns (e.g.
   `repo:org/repo:*`) match; `:aud` uses `StringEquals` because it is the exact value
   `sts.amazonaws.com`. (A wildcard `sub` under `StringEquals` would treat `*` as a
   literal and match nothing, rejecting every assume-role call.) Used by
   QA (`telemetry-platform-gha-terratest`) and prod (`telemetry-platform-gha-tg-plan`,
   `telemetry-platform-gha-tg-apply`) OIDC roles. `github_oidc_provider_arn` is required
   when any role uses this mode -- omitting it fails fast (FR-10).

2. **Role-chaining trust (D-11):** `trust_policy_json` is a pre-built IAM trust policy
   JSON document. Takes precedence over OIDC trust when non-empty. Used by the root
   `telemetry-platform-dns-writer` role, which is assumed via `sts:AssumeRole` from the prod
   apply role, not directly by GitHub Actions. `github_oidc_provider_arn` may be omitted.

## The `roles` map schema

Each key in the `roles` map becomes the IAM role name. The value object carries:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sub` | string | no (default "") | `token.actions.githubusercontent.com:sub` claim value scoping OIDC trust (e.g. `repo:org/repo:environment:prod-apply`). Set for OIDC-trust roles; omit for role-chaining roles. |
| `trust_policy_json` | string | no (default "") | Pre-built IAM assume-role trust policy JSON document. When non-empty, takes precedence over OIDC trust generation (role-chaining mode, e.g. root dns-writer). |
| `managed_policy_arns` | list(string) | no (default []) | Managed policy ARNs to attach to the role |
| `inline_policies` | map(string) | no (default {}) | Map of inline policy name to JSON policy document |
| `description` | string | no (default "") | Human-readable description of the role's purpose |
| `max_session_duration` | number | no (default 3600) | Maximum session duration in seconds (3600-43200) |

An empty `roles` map is rejected at input validation. A bootstrap unit must create at
least one role (fail-fast on input contract).

## Provider prerequisite contract (D40)

The `github_oidc_provider_arn` input is **optional** (default empty string). It is
**required** when any role uses OIDC trust (`sub` is set and `trust_policy_json` is empty).
The GitHub OIDC provider is created once per AWS account by the operator as a bootstrap
prerequisite -- this reference never creates or manages it. The ARN pattern is:

```
arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com
```

Passing an ARN that does not match this pattern is rejected by input validation at plan
time. Omitting the ARN when any OIDC-trust role is present fails fast via a Terraform
`check` block that names the roles requiring the provider ARN (FR-10 fail-fast contract).

For accounts that host only role-chaining roles (e.g. root account with `telemetry-platform-dns-writer`),
`github_oidc_provider_arn` may be omitted entirely.

## Two-example matrix (docs/terragrunt-concepts.md)

| Example | Roles | Coverage |
|---------|-------|----------|
| `examples/default` | Single role (`telemetry-platform-gha-tg-sandbox`) with `environment:sandbox` sub binding | happy path, empty-map fail-fast |
| `examples/prod-subset` | Two prod roles: `telemetry-platform-gha-tg-plan` (PR plan trust) + `telemetry-platform-gha-tg-apply` (`environment:prod-apply` trust) | multi-role apply, trust policy content |

The `tests/common` suite runs validate, fmt, required outputs (`role_arns`),
`AssertTerraformVersion 1.15.5`, and idempotency across both examples.

## Output contract

Per docs/terragrunt-concepts.md, the following output is consumed by downstream units
(`E4-F1-S2-T1`, `E7-F1-S1-T1`):

| Output | Description |
|--------|-------------|
| `role_arns` | Map of role name to IAM role ARN, keyed by the `roles` map keys |

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

No providers.

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_oidc_role"></a> [oidc\_role](#module\_oidc\_role) | var.oidc\_role\_source | n/a |

## Resources

No resources.

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_github_oidc_provider_arn"></a> [github\_oidc\_provider\_arn](#input\_github\_oidc\_provider\_arn) | (Optional) ARN of the pre-existing GitHub Actions OIDC identity provider in the target account. Required when any role uses OIDC trust (sub field set). Omit for accounts that only create role-chaining roles (e.g. root dns-writer). The provider must be created as an operator prerequisite (D40); this reference never creates it. | `string` | `""` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"oidc-bootstrap"` | no |
| <a name="input_roles"></a> [roles](#input\_roles) | (Required) Map of role name to role configuration. For OIDC roles, set sub to the token.actions.githubusercontent.com:sub claim and provide github\_oidc\_provider\_arn. For role-chaining roles (e.g. root dns-writer), omit sub and provide trust\_policy\_json with the custom assume-role trust policy. Must contain at least one entry. | `map(object({ sub = optional(string, ""), managed_policy_arns = optional(list(string), []), inline_policies = optional(map(string), {}), description = optional(string, ""), max_session_duration = optional(number, 3600), trust_policy_json = optional(string, "") }))` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_role_arns"></a> [role\_arns](#output\_role\_arns) | Map of role name to IAM role ARN for all OIDC assume-roles created by this reference. Keys match the input roles map keys. Consumed by downstream pipeline configuration (E4-F1-S2-T1, E7-F1-S1-T1). |
<!-- END_TF_DOCS -->

## Child module source variables (const=true convention)

Each in-repo child module source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo local relative path without network access. Callers do not override these.

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `oidc_role_source` | `../../primitives/iam-role` | Source for the iam-role primitive module (for_each over roles map) |

By default (when `use_pinned_module_sources = false` in `account.hcl`), the source resolves to the
relative in-repo path above. When `use_pinned_module_sources = true` (prod), the terragrunt leaf
sets the `oidc_role_source` input to the pinned `git::https://github.com/example-org/telemetry-platform.git//<path>?ref=<path>/v<semver>`
URL. The toggle flows from `account.hcl` through `_envcommon/oidc-bootstrap.hcl` to the leaf.
See `docs/terraform-module-sourcing.md` for the end-to-end workflow.

## Policies

- `source_policy.rego` -- in-repo child sources are `source = var.<name>_source` (var-driven, const=true); prod-context pin gate enforces that in-repo sources resolve to pinned git URLs when `use_pinned_module_sources = true`
- `no_resources_policy` -- the reference root declares no `resource` blocks
- `composition_policy` -- the reference root uses only `module` blocks for infrastructure
