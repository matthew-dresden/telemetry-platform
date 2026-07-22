# state-bootstrap

Composes the Terraform remote-state infrastructure required before any per-account
apply can run: a KMS CMK, an S3 access-log bucket, an S3 artifact (state) bucket,
and a DynamoDB lock table. This is a NEW-LOCAL reference module (decision D14,
section 5.7) that the `state-bootstrap` terragrunt unit instantiates once per account.

The reference root declares no `resource` blocks. All infrastructure is delegated
to composed primitive modules.

## Composed modules

- `kms-key` (telemetry-platform v1.0.0) -- state CMK: `aws_kms_key` + `aws_kms_alias`
- `s3-bucket` (telemetry-platform v1.0.0) x2 -- access-log bucket and artifact (state) bucket via `aws_s3_bucket`
- `dynamodb-table` (terraform-modules v0.1.0) -- lock table via `aws_dynamodb_table`, wired through `kms_key_arn` (D46), `hash_key=LockID`, `attributes=[{name=LockID,type=S}]`, PITR enabled

## Usage

**Bootstrap leaf source (D-16 carve-out):** The `state-bootstrap` terragrunt leaf is a
bootstrap unit and always uses the in-repo source via `get_repo_root()` regardless of the
`use_pinned_module_sources` toggle. This carve-out exists because the bootstrap unit must
be applyable pre-push before the module git tag exists in the remote. The pin toggle governs
service units only; bootstrap units are exempt from the pinned-source requirement and from
the `tf_guard_pinned_sources` guard.

```hcl
# Both sandbox and prod bootstrap leaves use the in-repo source unconditionally (D-16):
terraform {
  source = "${get_repo_root()}//providers/aws/references/state-bootstrap"
}
```

The in-repo child module sources (`state_kms_key_source`, `access_log_bucket_source`,
`artifact_bucket_source`) still follow the pin toggle -- when `use_pinned_module_sources = true`
(prod), these inputs are set to pinned `git::...?ref=.../v<semver>` URLs; when false
(sandbox/dev), the in-repo relative-path defaults from `variables.tf` are used.

See `docs/terraform-module-sourcing.md` for the full sourcing model, including the
bootstrap carve-out and how the `tf_guard_pinned_sources` guard handles it.

## Examples

- `examples/default` -- KMS CMK + access-log bucket + artifact bucket + DynamoDB lock table (PITR, SSE via CMK)

## Child module source variables (const=true convention)

Each in-repo child module source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo local relative path without network access. Callers do not override these.

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `state_kms_key_source` | `../../primitives/kms-key` | Source for the kms-key primitive module |
| `access_log_bucket_source` | `../../primitives/s3-bucket` | Source for the access-log s3-bucket primitive module |
| `artifact_bucket_source` | `../../primitives/s3-bucket` | Source for the artifact (state) s3-bucket primitive module |

By default (when `use_pinned_module_sources = false` in `account.hcl`), each source resolves to the
relative in-repo path above. When `use_pinned_module_sources = true` (prod), the terragrunt leaf
sets every `*_source` input to the pinned `git::https://github.com/example-org/telemetry-platform.git//<path>?ref=<path>/v<semver>`
URL. The toggle flows from `account.hcl` directly to the leaf (state-bootstrap leaves do not include
`_envcommon` per the spec; the toggle is consumed from the `account_vars` local in the leaf).

Note: the leaf-level `terraform { source }` block is NOT toggled -- it always uses
`get_repo_root()` (D-16 bootstrap carve-out). Only the child `*_source` inputs are toggled.
See `docs/terraform-module-sourcing.md` for the full sourcing model and bootstrap carve-out.

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.12.1 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.0.0 |

## Providers

No providers.

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_access_log_bucket"></a> [access\_log\_bucket](#module\_access\_log\_bucket) | var.access\_log\_bucket\_source | n/a |
| <a name="module_artifact_bucket"></a> [artifact\_bucket](#module\_artifact\_bucket) | var.artifact\_bucket\_source | n/a |
| <a name="module_state_kms_key"></a> [state\_kms\_key](#module\_state\_kms\_key) | var.state\_kms\_key\_source | n/a |

## Resources

No resources.

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_bucket_prefix"></a> [bucket\_prefix](#input\_bucket\_prefix) | (Required) Prefix used to name the access-log and artifact S3 buckets. Must be lowercase alphanumeric with hyphens, 3-28 chars. | `string` | n/a | yes |
| <a name="input_kms_alias"></a> [kms\_alias](#input\_kms\_alias) | (Required) Alias suffix for the state KMS CMK (without the 'alias/' prefix). Must match [A-Za-z0-9/\_-]+. | `string` | n/a | yes |
| <a name="input_lock_table_name"></a> [lock\_table\_name](#input\_lock\_table\_name) | (Required) Name of the DynamoDB lock table. Must be 3-255 alphanumeric characters, underscores, dots, or hyphens. | `string` | n/a | yes |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"state-bootstrap"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_access_log_bucket"></a> [access\_log\_bucket](#output\_access\_log\_bucket) | The name of the S3 bucket that receives server access logs from the artifact (state) bucket. |
| <a name="output_artifact_bucket_name"></a> [artifact\_bucket\_name](#output\_artifact\_bucket\_name) | The name of the S3 bucket that stores Terraform state files. |
| <a name="output_lock_table_name"></a> [lock\_table\_name](#output\_lock\_table\_name) | The name of the DynamoDB table used for Terraform state locking. |
| <a name="output_state_kms_key_arn"></a> [state\_kms\_key\_arn](#output\_state\_kms\_key\_arn) | The ARN of the KMS CMK used to encrypt the state bucket, access-log bucket, and DynamoDB lock table. |
<!-- END_TF_DOCS -->

## Input validation

- `bucket_prefix` must be 3-28 characters, lowercase alphanumeric with hyphens, start and end with a letter or digit.
- `lock_table_name` must be 3-255 characters of alphanumerics, underscores, dots, or hyphens.
- `kms_alias` must match `[A-Za-z0-9/_-]+` without the `alias/` prefix.

## Policies

- `source_policy.rego` -- in-repo child sources are `source = var.<name>_source` (var-driven, const=true); the prod-context pin gate enforces that in-repo child sources resolve to pinned git URLs when `use_pinned_module_sources = true`. Note: the leaf-level `terraform { source }` for the bootstrap unit itself uses `get_repo_root()` unconditionally (D-16 carve-out); the `tf_guard_pinned_sources` guard exempts bootstrap path segments from the pinned-source requirement.
- `no_resources_policy` -- the reference root declares no `resource` blocks
