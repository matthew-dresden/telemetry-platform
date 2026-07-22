# acm-certificate -- basic example

This example demonstrates creating an ACM certificate with DNS validation in us-east-1 (required for CloudFront). The provider block in `versions.tf` pins the AWS region to `us-east-1`.

Per decision D24, the `aws_acm_certificate_validation` resource is NOT created in this primitive. The consuming unit is responsible for creating DNS validation records and the validation resource using the `domain_validation_options` output.

## Usage

```hcl
module "acm_certificate" {
  source = "../../"

  domain_name         = "example.com"
  validation_method   = "DNS"
  wait_for_validation = false
}
```

## What this example creates

- One `aws_acm_certificate` with DNS validation in us-east-1.
- No `aws_acm_certificate_validation` -- the consuming unit handles that (per D24).

## Inputs

See `variables.tf` for all configurable inputs.

## Outputs

| Name | Description |
|------|-------------|
| `certificate_arn` | The Amazon Resource Name (ARN) of the certificate. |
| `domain_validation_options` | Set of domain validation objects for completing certificate validation in the consuming unit. |
| `certificate_domain_name` | The domain name for which the certificate is issued. |
| `certificate_status` | Status of the certificate. |
