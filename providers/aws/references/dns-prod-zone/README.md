# dns-prod-zone

Composes the full production DNS plane: the `route53-zone` primitive (hosted zone), the `kms-key`
primitive (the single prod DNS/cert customer-managed key aliased as `alias/telemetry-config`), and
the `ssm-parameter` primitive (non-secret zone metadata published to SSM via `for_each`, one
parameter per `ssm_parameters` key). This is a NEW-LOCAL reference module per docs/terragrunt-concepts.md
that downstream environment units (sandbox dns-prod-zone, prod dns-prod-zone + identity) consume.

The reference root declares no `resource` blocks. All infrastructure is delegated to the composed
modules.

The composed kms-key owns ONLY the `telemetry-config` CMK (docs/terragrunt-concepts.md). It does NOT
create `alias/telemetry-data` (owned by the data-lake reference, E1-F5-S2-T1) or
`alias/telemetry-spice` (owned by its own deploy-layer owner). The key policy is principal-scoped
with no wildcard principals, and grants the SSM service `kms:Decrypt` and `kms:GenerateDataKey`
under a `kms:ViaService = ssm.<region>.amazonaws.com` condition for SecureString config parameters.
It additionally grants the CloudWatch Logs service principal `logs.<region>.amazonaws.com` the
`kms:Encrypt`, `kms:Decrypt`, `kms:ReEncrypt*`, `kms:GenerateDataKey*`, and `kms:DescribeKey`
actions under an `ArnLike` condition on `kms:EncryptionContext:aws:logs:arn` (docs/terragrunt-concepts.md; AWS CloudWatch Logs CMK docs). This is required because a WAF-fronted service encrypts its
WAF CloudWatch log group with this `telemetry-config` CMK (`waf_log_kms_key_arn`); CloudWatch Logs
validates the grant at log-group create time, so without it `CreateLogGroup` fails with
`AccessDeniedException`. The account id in the encryption-context ARN is derived from
`data.aws_caller_identity.current` and the region from `var.region`, so no configuration value is
hardcoded in the module.

The key policy also grants the AWS Cost Anomaly Detection service principal
`costalerts.amazonaws.com` the `kms:GenerateDataKey*` and `kms:Decrypt` actions under a
`StringEquals` condition on `aws:SourceAccount`. This `telemetry-config` CMK encrypts the
observability reference's alerts SNS topic; Cost Anomaly Detection publishes from `costalerts`
and must be able to generate a data key / decrypt against the encrypting CMK, or publishing to the
encrypted topic fails. The account id in the condition is derived from
`data.aws_caller_identity.current` (never hardcoded), and the existing account-root
`EnableKeyAdministration` admin statement is left unchanged.

## Composed modules

- `route53-zone` (telemetry-platform v1.0.0) -- public hosted zone via `aws_route53_zone`
- `kms-key` (telemetry-platform v1.0.0) -- single prod DNS/cert CMK (`alias/telemetry-config`) via `aws_kms_key` + `aws_kms_alias`
- `ssm-parameter` (telemetry-platform v1.0.0) -- one `aws_ssm_parameter` per `ssm_parameters` map entry via `for_each`

It also creates two GATED, foundation-tier-only resources (default OFF, created only when
the corresponding `tfstate_*` inputs are set):

- `aws_kms_grant.tfstate_cross_account_read` -- an additive (lockout-safe) cross-account
  `kms:Decrypt` grant on this unit's remote-state CMK, per grantee in
  `tfstate_cmk_decrypt_grantee_arns` (with `data.aws_kms_key.tfstate` resolving the alias).
- `aws_s3_bucket_policy.tfstate_cross_account_read` -- a policy on this unit's own
  remote-state bucket that replicates terragrunt's `EnforcedTLS` + `RootAccess` statements
  and adds cross-account read for `tfstate_bucket_read_grantee_arns`.

These let a cross-account terragrunt plan -- notably the dns-owner `dns-delegation` unit,
which reads this foundation zone's `name_servers` from the env-account remote state --
decrypt and read this unit's state across accounts. terragrunt treats a 403 reading
dependency state as fatal (its dependency `mock_outputs` rescue only an *absent* state, not
access-denied), so the cross-account plan role needs both the CMK grant and the bucket-policy
read. See [Cross-account remote-state read](#cross-account-remote-state-read-foundation-tier).

## Usage

When `use_pinned_module_sources = true` (prod environments), the terragrunt leaf sources this reference
via a pinned `git::...?ref=providers/aws/references/dns-prod-zone/v<semver>` URL. When false (dev/sandbox),
the leaf uses `${get_repo_root()}//providers/aws/references/dns-prod-zone` so local relative defaults apply.

```hcl
# Pinned usage (use_pinned_module_sources = true, prod):
module "dns_prod_zone" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/references/dns-prod-zone?ref=providers/aws/references/dns-prod-zone/v1.0.0"

  zone_name = "prod.telemetry.example.com"
  kms_alias = "telemetry-config"
  region    = "us-east-1"

  kms_key_principals = [
    "arn:aws:iam::123456789012:root",
  ]

  ssm_parameters = {
    "/telemetry/prod/dns/zone-id" = {
      type  = "String"
      value = "Z1234567890ABC"
    }
  }

  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- hosted zone + single prod DNS/cert CMK + one SSM param via the
  `ssm_parameters` map. The fixture run-scopes the KMS alias to
  `alias/telemetry-config-<run-id>` (a KMS alias is unique per account+region, so the
  fixture must not reuse the production `alias/telemetry-config` name created by the
  foundation-tier unit)

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.12.1 |
| aws | ~> 6.0.0 |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| zone_name | DNS name for the prod hosted zone. Passed to the route53-zone primitive. Must match pattern ^[a-z0-9.-]+$. | string | n/a | yes |
| kms_alias | Alias suffix for the single prod DNS/cert CMK (docs/terragrunt-concepts.md). The kms-key primitive prefixes 'alias/' automatically. Creates alias/telemetry-config (docs/terragrunt-concepts.md). | string | n/a | yes |
| region | AWS region used to construct the kms:ViaService condition value ('ssm.\<region\>.amazonaws.com') in the KMS key policy. Must be a valid AWS region identifier (e.g. 'us-east-1'). | string | n/a | yes |
| ssm_parameters | Map of non-secret zone metadata to publish to SSM. Each key is the fully-qualified parameter name; each value provides type, value, and optional kms_key_id. Iterated via for_each (docs/terragrunt-concepts.md). | map(object({ type = string, value = string, kms_key_id = optional(string) })) | n/a | yes |
| kms_key_principals | List of IAM principal ARNs granted kms:Decrypt and kms:GenerateDataKey on the prod DNS/cert CMK. No wildcard principals permitted (docs/terragrunt-concepts.md). | list(string) | n/a | yes |
| force_destroy | Whether to destroy all records in the zone before zone deletion. Set to true only for ephemeral test zones. | bool | false | no |
| tags | Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source reference module. | string | "dns-prod-zone" | no |
| tfstate_cmk_alias | Alias name of this unit's remote-state CMK (e.g. `alias/222222222222-tfstate`), resolved to a key id for the cross-account kms:Decrypt grants. Only consulted when `tfstate_cmk_decrypt_grantee_arns` is non-empty. | string | "" | no |
| tfstate_cmk_decrypt_grantee_arns | Cross-account IAM principal ARNs granted kms:Decrypt on the remote-state CMK via an additive (lockout-safe) aws_kms_grant, so cross-account terragrunt plans can decrypt this foundation zone's remote state. Empty => no grant. | list(string) | [] | no |
| tfstate_bucket_name | Name of this unit's own remote-state S3 bucket. When `tfstate_bucket_read_grantee_arns` is non-empty, the module attaches an aws_s3_bucket_policy replicating terragrunt's EnforcedTLS + RootAccess statements and adding the cross-account read statements. Empty => no bucket policy. | string | "" | no |
| tfstate_bucket_account_id | The 12-digit account id that owns the remote-state bucket; principal of the replicated RootAccess statement. Required when `tfstate_bucket_read_grantee_arns` is non-empty. | string | "" | no |
| tfstate_bucket_read_grantee_arns | Cross-account IAM principal ARNs granted read-only access (s3:GetObject/GetObjectVersion; s3:ListBucket/GetBucketVersioning) to this unit's remote-state bucket. Empty => no bucket policy. | list(string) | [] | no |

## Outputs

| Name | Description |
|------|-------------|
| zone_id | The Route 53 hosted zone ID. Downstream dependency reads resolve via dependency.zone_id (D37). |
| name_servers | List of exactly four authoritative name servers for the hosted zone. |
| zone_arn | The Amazon Resource Name (ARN) of the Route 53 hosted zone. |
| kms_key_arn | The ARN of the single prod DNS/cert CMK (docs/terragrunt-concepts.md). Downstream dependency reads resolve via dependency.kms_key_arn (D37). |
| kms_key_id | The globally unique ID of the single prod DNS/cert CMK. Downstream dependency reads resolve via dependency.kms_key_id (D37). |

## Input validation

- `zone_name` must match `^[a-z0-9.-]+$` (lowercase alphanumeric, hyphens, dots).
- `kms_alias` must match `^[A-Za-z0-9/_-]+$` and must not include the `alias/` prefix.
- `region` must match `^[a-z]{2}-[a-z]+-[0-9]+$` (standard AWS region format, e.g. `us-east-1`).
- `ssm_parameters` must be non-empty; every entry type must be `String`, `StringList`, or `SecureString`.
- `kms_key_principals` must be non-empty and must not contain wildcard (`*`) principals.

## Child module source variables (const=true convention)

Each in-repo child module source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo local relative path without network access. Callers do not override these.

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `zone_source` | `../../primitives/route53-zone` | Source for the route53-zone primitive module |
| `kms_source` | `../../primitives/kms-key` | Source for the kms-key primitive module |
| `ssm_source` | `../../primitives/ssm-parameter` | Source for the ssm-parameter primitive module |

By default (when `use_pinned_module_sources = false` in `account.hcl`), each source resolves to the
relative in-repo path above. When `use_pinned_module_sources = true` (prod), the terragrunt leaf
sets every `*_source` input to the pinned `git::https://github.com/matthew-dresden/telemetry-platform.git//<path>?ref=<path>/v<semver>`
URL. The toggle flows from `account.hcl` through `_envcommon/dns-prod-zone.hcl` to the leaf.
See `docs/terraform-module-sourcing.md` for the end-to-end workflow.

## Cross-account remote-state read (foundation-tier)

When deployed as the foundation-tier `dns-prod-zone` unit
(`terragrunt/live/.../bootstrap/<role>/dns-prod-zone`), this reference's remote-state object
is read **across accounts** by the dns-owner `dns-delegation` unit, which consumes the zone's
`name_servers` to publish the delegating `NS` record in the dns-owner account. terragrunt
treats a `403` reading dependency state as fatal -- its dependency `mock_outputs` rescue only
an *absent* state, not an access-denied one -- so the dns-owner plan role must be able to
decrypt and read this unit's state cross-account.

The reference supplies that access with two gated, additive resources (default OFF):

| Input | Effect when set |
|-------|-----------------|
| `tfstate_cmk_decrypt_grantee_arns` + `tfstate_cmk_alias` | `aws_kms_grant` granting each principal `kms:Decrypt` on the remote-state CMK. A KMS grant is additive and cannot remove the key's root access (lockout-safe), unlike a key-policy edit. |
| `tfstate_bucket_read_grantee_arns` + `tfstate_bucket_name` + `tfstate_bucket_account_id` | `aws_s3_bucket_policy` on the remote-state bucket that replicates terragrunt's `EnforcedTLS` + `RootAccess` statements (so terragrunt's additive backend policy management sees no drift) and adds the cross-account read statements. |

The standalone module and its terratest leave all four inputs at their empty defaults, so
zero grants / bucket policies are created and no plan-time AWS read occurs. The
foundation-tier leaf wires the inputs (grantee ARNs derived from `common/accounts.json`; the
bucket name / account id / CMK alias from `root.hcl` locals).

## Policies

- `source_policy.rego` -- in-repo child sources are `source = var.<name>_source` (var-driven, const=true); prod-context pin gate enforces that in-repo sources resolve to pinned git URLs when `use_pinned_module_sources = true`
- `no_resources_policy` -- the reference root declares no `resource` blocks; all infrastructure is in composed modules
