# alb-listener -- terraform-docs reference

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.12.1 |
| aws | ~> 6.0.0 |

## Providers

| Name | Version |
|------|---------|
| aws | ~> 6.0.0 |

## Resources

| Name | Type |
|------|------|
| aws_lb_listener.this | resource |
| aws_lb_listener_rule.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| load_balancer_arn | Target ALB ARN (from alb output alb_arn). | string | n/a | yes |
| listeners | Listeners to create. HTTPS requires ssl_policy and ACM certificate_arn. Forward actions require target_group_arn. Must be non-empty. | list(object) | n/a | yes |
| listener_rules | Optional path/host rules. Each priority must be between 1 and 50000. | list(object) | [] | no |
| tags | Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source module. | string | "alb-listener" | no |

## Outputs

| Name | Description |
|------|-------------|
| listener_arns | Map listener name to ARN. |
| https_listener_arn | The HTTPS listener ARN (the collector OTLP listener). For wiring/monitoring. |
| listener_rule_arns | Map rule key to ARN. |

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.12.1 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.0.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | ~> 6.0.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_lb_listener.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb_listener) | resource |
| [aws_lb_listener_rule.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb_listener_rule) | resource |

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

| Name | Description |
| ---- | ----------- |
| <a name="output_https_listener_arn"></a> [https\_listener\_arn](#output\_https\_listener\_arn) | The ARN of the HTTPS listener. For wiring and monitoring the collector OTLP listener. |
| <a name="output_listener_arns"></a> [listener\_arns](#output\_listener\_arns) | Map of listener name to ARN. |
| <a name="output_listener_rule_arns"></a> [listener\_rule\_arns](#output\_listener\_rule\_arns) | Map of rule key to ARN. |
<!-- END_TF_DOCS -->