# ssm-parameter -- basic example

This example demonstrates creating a String SSM parameter for non-secret configuration, following the `/telemetry/<env>/<plane>/<key>` path convention.

## Usage

```hcl
module "example" {
  source = "../../"

  name        = "/telemetry/prod/ingest/waf-rate-limit"
  type        = "String"
  value       = "100"
  description = "WAF rate limit threshold"
}
```

## What this example creates

- One `aws_ssm_parameter` of type `String` at the platform path convention.

## Inputs

See `variables.tf` for all configurable inputs.

## Outputs

| Name | Description |
|------|-------------|
| `parameter_arn` | The Amazon Resource Name (ARN) of the SSM parameter. |
| `parameter_name` | The fully qualified name of the SSM parameter. |
| `parameter_version` | The version of the SSM parameter. |
