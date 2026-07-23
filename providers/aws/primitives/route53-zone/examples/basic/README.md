# route53-zone -- basic example

This example demonstrates creating a Route53 public hosted zone with force_destroy enabled for ephemeral test environments.

## Usage

```hcl
module "route53_zone" {
  source = "../../"

  zone_name     = "example.com"
  force_destroy = true
}
```

## What this example creates

- One `aws_route53_zone` public hosted zone with force_destroy enabled for safe test cleanup.

## Inputs

See `variables.tf` for all configurable inputs.

## Outputs

| Name | Description |
|------|-------------|
| `zone_id` | The hosted zone ID. |
| `name_servers` | A list of name servers for the hosted zone. |
| `arn` | The Amazon Resource Name (ARN) of the hosted zone. |
| `zone_name` | The DNS name of the hosted zone. |
