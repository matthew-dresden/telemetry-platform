# acm-cert-managed

Wraps the `acm-certificate` primitive (the prod/sandbox TLS certificate request) and adds the SAME
gated, default-OFF cross-account remote-state read pattern shipped on the `dns-prod-zone` reference
(#135). It is the reference module the `acm-collector` and `acm-portal` service units source.

The wrapped certificate is a pure REQUEST: `aws_acm_certificate_validation` is NOT created here
(per decision D24); the consuming unit owns validation. The collector/portal leaves set
`wait_for_validation = false` so the request returns immediately, and supply the mandatory pretty-name
SAN (D19). The cross-account validation CNAME for that SAN is written downstream by the dns-owner
`_singletons/pretty/validate-collector` / `validate-portal` units, which read this unit's
`domain_validation_options` from the env-account remote state.

## Composed modules

- `acm-certificate` (telemetry-platform) -- the TLS certificate request via `aws_acm_certificate`
  (`create_before_destroy = true`, no validation resource per D24)

It also creates two GATED, default-OFF resources (created only when the corresponding `tfstate_*`
inputs are set):

- `aws_kms_grant.tfstate_cross_account_read` -- an additive (lockout-safe) cross-account
  `kms:Decrypt` grant on this unit's remote-state CMK, per grantee in
  `tfstate_cmk_decrypt_grantee_arns` (with `data.aws_kms_key.tfstate` resolving the alias to a key id).
- `aws_s3_bucket_policy.tfstate_cross_account_read` -- a policy on this unit's own remote-state bucket
  that replicates terragrunt's `EnforcedTLS` + `RootAccess` statements (so terragrunt's additive
  backend policy management detects no drift) and adds cross-account read for
  `tfstate_bucket_read_grantee_arns`.

These let a cross-account terragrunt plan -- notably the dns-owner pretty/validate units, which read
this unit's `domain_validation_options` cross-account -- decrypt and read this unit's state.
terragrunt treats a `403` reading dependency state as fatal (its dependency `mock_outputs` rescue only
an *absent* state, not access-denied), so the cross-account plan role needs both the bucket-policy
read and the CMK decrypt. The standalone module leaves all five inputs at their empty defaults, so
zero grants / bucket policies are created and no plan-time AWS read occurs.

## State migration (moved block)

The `acm-collector` / `acm-portal` leaves previously sourced the `acm-certificate` PRIMITIVE directly,
so the certificate lived at the root state address `aws_acm_certificate.this`. This reference wraps that
primitive as `module.certificate`, so the address becomes `module.certificate.aws_acm_certificate.this`.
A `moved` block refactors the existing state in place -- there is no destroy/recreate of the live
certificate. On a fresh deploy (terratest, examples, a never-applied env) the `from` address is absent
from state, so the `moved` block is a documented no-op.

## Usage

When `use_pinned_module_sources = true` (prod), the terragrunt leaf sources this reference via a pinned
`git::...?ref=providers/aws/references/acm-cert-managed/v<semver>` URL. When false (sandbox/dev), the leaf
uses `${get_repo_root()}//providers/aws/references/acm-cert-managed` so the local relative defaults apply.

```hcl
# Pinned usage (use_pinned_module_sources = true, prod):
module "acm_collector" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/references/acm-cert-managed?ref=providers/aws/references/acm-cert-managed/v0.1.0"

  domain_name               = "collector-000.prod.telemetry.example.com"
  subject_alternative_names = ["collector.telemetry.example.com"]
  wait_for_validation       = false

  # Cross-account state read ENABLED only where the dns-owner account reads this state
  # (prod). The CMK grant inputs are left empty because the prod tfstate CMK is
  # account-wide and the dns-owner plan role already holds an account-level Decrypt grant.
  tfstate_bucket_name              = "telemetry-useast1-prod-000-acm-collector-000-tfstate"
  tfstate_bucket_account_id        = "111111111111"
  tfstate_bucket_read_grantee_arns = ["arn:aws:iam::444444444444:role/telemetry-platform-gha-tg-plan"]

  tags = { env = "prod" }
}
```

## Examples

- `examples/basic` -- a single certificate request (DNS validation, `wait_for_validation = false`)
  with the cross-account state-read gate left OFF, so the example creates zero `aws_kms_grant` and zero
  `aws_s3_bucket_policy`.

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.15.5 |
| aws | >= 6.49.0 |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| domain_name | Fully qualified domain name for the certificate. Must match `^[a-z0-9.-]+$`. | string | n/a | yes |
| subject_alternative_names | SAN list; the pretty-name SAN is MANDATORY on the collector/portal certs (D19). Each entry must match `^[a-z0-9.*-]+$`. | list(string) | [] | no |
| validation_method | Domain validation method. One of DNS, EMAIL. | string | "DNS" | no |
| key_algorithm | Key pair algorithm. One of RSA_2048, EC_prime256v1, EC_secp384r1. | string | "RSA_2048" | no |
| wait_for_validation | Reserved input; this module never creates `aws_acm_certificate_validation` (D24). | bool | false | no |
| tags | Additional tags applied to all resources. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source reference module. | string | "acm-cert-managed" | no |
| tfstate_cmk_alias | Alias of this unit's remote-state CMK, resolved to a key id for the cross-account `kms:Decrypt` grants. Only consulted when `tfstate_cmk_decrypt_grantee_arns` is non-empty. | string | "" | no |
| tfstate_cmk_decrypt_grantee_arns | Cross-account principal ARNs granted `kms:Decrypt` on the remote-state CMK via an additive (lockout-safe) `aws_kms_grant`. Empty => no grant. | list(string) | [] | no |
| tfstate_bucket_name | Name of this unit's own remote-state S3 bucket. When `tfstate_bucket_read_grantee_arns` is non-empty, an `aws_s3_bucket_policy` is attached. Empty => no bucket policy. | string | "" | no |
| tfstate_bucket_account_id | The 12-digit account id that owns the remote-state bucket; principal of the replicated RootAccess statement. Required when `tfstate_bucket_read_grantee_arns` is non-empty. | string | "" | no |
| tfstate_bucket_read_grantee_arns | Cross-account principal ARNs granted read-only access (`s3:GetObject`/`GetObjectVersion`; `s3:ListBucket`/`GetBucketVersioning`) to this unit's remote-state bucket. Empty => no bucket policy. | list(string) | [] | no |
| certificate_source | (const=true) Source for the acm-certificate primitive child module. Defaults to the in-repo relative path; not overridden by callers. | string | "../../primitives/acm-certificate" | no |

## Outputs

| Name | Description |
|------|-------------|
| certificate_arn | The ARN of the certificate. Downstream dependency reads resolve via `dependency.certificate_arn` (D37). |
| domain_validation_options | Set of domain validation objects; consumed by the dns-owner pretty/validate units and downstream validation (D24). |
| certificate_domain_name | The domain name for which the certificate is issued. |
| certificate_status | Status of the certificate. |

## Input validation

- `domain_name` must match `^[a-z0-9.-]+$`.
- each `subject_alternative_names` entry must match `^[a-z0-9.*-]+$`.
- `validation_method` must be one of DNS, EMAIL.
- `key_algorithm` must be one of RSA_2048, EC_prime256v1, EC_secp384r1.
- `tfstate_cmk_decrypt_grantee_arns` / `tfstate_bucket_read_grantee_arns` entries must be full IAM
  principal ARNs (`arn:aws:iam::<account>:...`).
- `tfstate_bucket_account_id` must be empty or a 12-digit account id.

## Child module source variable (const=true convention)

The in-repo child source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo relative path without network access. Callers do not override it.

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `certificate_source` | `../../primitives/acm-certificate` | Source for the acm-certificate primitive module |

By default (`use_pinned_module_sources = false` in `account.hcl`) the source resolves to the relative
in-repo path above. When `use_pinned_module_sources = true` (prod) the terragrunt leaf sources this
reference itself via a pinned `?ref=` URL; the relative child path resolves within the fetched ref.

## Cross-account remote-state read gating

| Input | Effect when set |
|-------|-----------------|
| `tfstate_cmk_decrypt_grantee_arns` + `tfstate_cmk_alias` | `aws_kms_grant` granting each principal `kms:Decrypt` on the remote-state CMK (additive; lockout-safe). |
| `tfstate_bucket_read_grantee_arns` + `tfstate_bucket_name` + `tfstate_bucket_account_id` | `aws_s3_bucket_policy` on the remote-state bucket that replicates terragrunt's `EnforcedTLS` + `RootAccess` statements and adds the cross-account read statements. |

The prod `acm-collector`/`acm-portal` leaves enable ONLY the bucket policy: the prod tfstate CMK is
account-wide and the dns-owner plan role already holds an account-level `kms:Decrypt` grant, so the
per-unit CMK grant is unnecessary. Sandbox/qa leave the gate fully OFF (their pretty/validate units run
in-account). The grantee ARN is derived from `common/accounts.json` (the `is_dns_owner` account's
`plan_role_name`); the bucket name / account id from the namespace and `account.hcl`.

## Policies

- `source_policy.rego` -- the in-repo child source is `source = var.certificate_source` (var-driven,
  `const=true`); the prod-context pin gate enforces that in-repo sources resolve to pinned git URLs
  when `use_pinned_module_sources = true`.
