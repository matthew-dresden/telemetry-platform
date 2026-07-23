<!-- BEGIN_TF_DOCS -->
# acm-certificate

Manages a single AWS ACM certificate configured for DNS validation, exposing the certificate ARN, domain validation options, domain name, and status for use by downstream modules.

Used for issuing TLS certificates for the telemetry-collector platform. Per decision D24, this primitive does NOT create `aws_acm_certificate_validation` -- the consuming unit is responsible for feeding `domain_validation_options` into its own validation resource. The ACM certificate must be created in `us-east-1` when used with CloudFront distributions.

## Resources managed

- `aws_acm_certificate` -- the requested certificate (no `aws_acm_certificate_validation`, per D24)

## Usage

```hcl
module "acm_certificate" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/acm-certificate?ref=providers/aws/primitives/acm-certificate/v0.1.0"

  domain_name = "example.com"
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- DNS validation, RSA_2048 key, provider pinned to us-east-1 for CloudFront compatibility

## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | >= 6.49.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_acm_certificate.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/acm_certificate) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_domain_name"></a> [domain\_name](#input\_domain\_name) | (Required) Fully qualified domain name for the certificate. Must match pattern ^[a-z0-9.-]+$. | `string` | n/a | yes |
| <a name="input_key_algorithm"></a> [key\_algorithm](#input\_key\_algorithm) | (Optional) Specifies the algorithm of the public and private key pair that your Amazon-issued certificate uses to encrypt data. Valid values: RSA\_2048, EC\_prime256v1, EC\_secp384r1. | `string` | `"RSA_2048"` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"acm-certificate"` | no |
| <a name="input_subject_alternative_names"></a> [subject\_alternative\_names](#input\_subject\_alternative\_names) | (Optional) List of domains that should be Subject Alternative Names in the issued certificate. Each element must match pattern ^[a-z0-9.*-]+$. | `list(string)` | `[]` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_validation_method"></a> [validation\_method](#input\_validation\_method) | (Optional) Method to use for domain validation. Valid values: DNS, EMAIL. | `string` | `"DNS"` | no |
| <a name="input_validation_record_fqdns"></a> [validation\_record\_fqdns](#input\_validation\_record\_fqdns) | (Optional) Reserved for consuming units. List of FQDNs that implement the validation. This input is not used by this module; the consuming unit is responsible for creating aws\_acm\_certificate\_validation using domain\_validation\_options (per decision D24). | `list(string)` | `[]` | no |
| <a name="input_wait_for_validation"></a> [wait\_for\_validation](#input\_wait\_for\_validation) | (Optional) Reserved input for consuming units. This module does NOT create aws\_acm\_certificate\_validation regardless of value (per decision D24). The validation resource must be created in the consuming unit using domain\_validation\_options from this module's outputs. | `bool` | `true` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_accepted_inputs"></a> [accepted\_inputs](#output\_accepted\_inputs) | Inputs accepted at the module interface for call-site symmetry but NOT consumed internally (per decision D24, certificate validation is owned by the consuming unit); surfaced for caller introspection and to assert interface stability. |
| <a name="output_certificate_arn"></a> [certificate\_arn](#output\_certificate\_arn) | The Amazon Resource Name (ARN) of the certificate. |
| <a name="output_certificate_domain_name"></a> [certificate\_domain\_name](#output\_certificate\_domain\_name) | The domain name for which the certificate is issued. |
| <a name="output_certificate_status"></a> [certificate\_status](#output\_certificate\_status) | Status of the certificate. |
| <a name="output_domain_validation_options"></a> [domain\_validation\_options](#output\_domain\_validation\_options) | Set of domain validation objects which can be used to complete certificate validation. Feed this into the consuming unit's aws\_acm\_certificate\_validation resource (per decision D24). |

## Input validation

- `domain_name` must match `^[a-z0-9.-]+$`. Plans fail if the pattern is not satisfied.
- `validation_method` must be one of `DNS` or `EMAIL`. Plans fail for any other value.
- `key_algorithm` must be one of `RSA_2048`, `EC_prime256v1`, or `EC_secp384r1`. Plans fail for any other value.
- `subject_alternative_names` elements must each match `^[a-z0-9.*-]+$`. Plans fail for invalid SAN entries.

## Decision D24 -- validation belongs to the consuming unit

This module intentionally does not create `aws_acm_certificate_validation`. The consuming unit must create its own `aws_acm_certificate_validation` resource using the `domain_validation_options` output from this module. This separation ensures the consuming unit controls the validation lifecycle and DNS record creation.
<!-- END_TF_DOCS -->