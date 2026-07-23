<!-- BEGIN_TF_DOCS -->
# alb-listener

Attaches listeners (and optional listener rules) to an existing Application Load Balancer created by the `alb` module. Listeners are a SEPARATE module from `alb` (D6): the `alb` module does NOT embed listeners.

This platform's collector ALB uses an HTTPS listener (HTTPS-only); an HTTP listener is supported as an option (redirect-to-HTTPS) but the platform default attaches only HTTPS.

## Resources managed

- `aws_lb_listener` -- one or more listeners (HTTPS default; optional HTTP redirect)
- `aws_lb_listener_rule` -- optional path/host rules (when `listener_rules` is non-empty)

## Usage

```hcl
module "alb_listener" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/alb-listener?ref=providers/aws/primitives/alb-listener/v0.1.0"

  load_balancer_arn = module.alb.alb_arn

  listeners = [
    {
      name            = "https"
      port            = 443
      protocol        = "HTTPS"
      ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"
      certificate_arn = module.acm_certificate.certificate_arn
      default_action = {
        type             = "forward"
        target_group_arn = module.alb.target_group_arns["adot-otlp"]
      }
    }
  ]
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/https` -- HTTPS listener on 443 forwarding to a target group with ssl_policy + ACM cert
- `examples/http-redirect` -- HTTP 80 redirect-to-HTTPS plus HTTPS 443 listener

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
| [aws_lb_listener.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb_listener) | resource |
| [aws_lb_listener_rule.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb_listener_rule) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_listener_rules"></a> [listener\_rules](#input\_listener\_rules) | (Optional) Listener rules to create. Priority must be between 1 and 50000. | <pre>list(object({<br/>    listener_name = string<br/>    priority      = number<br/>    conditions = list(object({<br/>      field  = string<br/>      values = list(string)<br/>    }))<br/>    action = object({<br/>      type             = string<br/>      target_group_arn = optional(string)<br/>    })<br/>  }))</pre> | `[]` | no |
| <a name="input_listeners"></a> [listeners](#input\_listeners) | (Required) Listeners to create. HTTPS listeners require ssl\_policy and a valid ACM certificate\_arn. Forward actions require target\_group\_arn. | <pre>list(object({<br/>    name            = string<br/>    port            = number<br/>    protocol        = string<br/>    ssl_policy      = optional(string)<br/>    certificate_arn = optional(string)<br/>    default_action = object({<br/>      type             = string<br/>      target_group_arn = optional(string)<br/>      redirect = optional(object({<br/>        port        = string<br/>        protocol    = string<br/>        status_code = string<br/>      }))<br/>    })<br/>  }))</pre> | n/a | yes |
| <a name="input_load_balancer_arn"></a> [load\_balancer\_arn](#input\_load\_balancer\_arn) | (Required) ARN of the target ALB (from alb module output alb\_arn). | `string` | n/a | yes |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"alb-listener"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_https_listener_arn"></a> [https\_listener\_arn](#output\_https\_listener\_arn) | The ARN of the HTTPS listener. For wiring and monitoring the collector OTLP listener. |
| <a name="output_listener_arns"></a> [listener\_arns](#output\_listener\_arns) | Map of listener name to ARN. |
| <a name="output_listener_rule_arns"></a> [listener\_rule\_arns](#output\_listener\_rule\_arns) | Map of rule key to ARN. |

## Input validation

- `load_balancer_arn` must start with `arn:aws:elasticloadbalancing:`. Plans fail for non-ARN values.
- `listeners` must be non-empty. Plans fail with an empty list.
- Each listener `protocol` must be `HTTP` or `HTTPS`.
- HTTPS listeners require both `ssl_policy` (non-null, non-empty) and a valid ACM `certificate_arn` starting with `arn:aws:acm:`.
- Listeners with a `forward` default action must provide a non-empty `target_group_arn`.
- Listeners with a `redirect` default action must provide a `redirect` configuration block.
- Each `listener_rules` entry must have `priority` between 1 and 50000.
<!-- END_TF_DOCS -->