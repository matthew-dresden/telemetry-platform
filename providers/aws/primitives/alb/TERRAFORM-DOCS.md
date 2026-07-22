# alb -- terraform-docs reference

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
| aws_security_group.this | resource |
| aws_lb.this | resource |
| aws_lb_target_group.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | LB name. Must be alphanumeric and hyphens only, max 32 characters (ALB name limit). | string | n/a | yes |
| internal | Whether the ALB is internal. Defaults to true because CloudFront is the public edge for this platform. | bool | true | no |
| vpc_id | VPC ID where the ALB is deployed. | string | n/a | yes |
| subnet_ids | List of subnet IDs. At least 2 subnets across availability zones are required (spec 5.A). | list(string) | n/a | yes |
| security_group_ids | List of existing security group IDs to attach to the ALB. If empty and create_security_group is true, a managed SG is created. | list(string) | [] | no |
| create_security_group | Whether to create a managed security group for the ALB. | bool | true | no |
| ingress_cidr_blocks | Allowed ingress CIDR blocks (CloudFront managed prefix list preferred; supply via this var). | list(string) | [] | no |
| idle_timeout | Idle timeout seconds. Must be between 1 and 4000 inclusive. | number | 60 | no |
| enable_deletion_protection | Deletion protection. | bool | false | no |
| access_logs | S3 access logs. When null, access logging is disabled. | object | null | no |
| target_groups | Target groups (the ADOT OTLP/HTTP port, typically 4318). Must be non-empty. | list(object) | n/a | yes |
| tags | Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source module. | string | "alb" | no |

## Outputs

| Name | Description |
|------|-------------|
| alb_arn | ALB ARN (passed to alb-listener). |
| alb_dns_name | DNS (CloudFront origin). |
| alb_zone_id | Zone ID. |
| target_group_arns | Map of TG name to ARN (for alb-listener and ecs-service to attach). |
| security_group_id | Managed SG id. |

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
| [aws_lb.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb) | resource |
| [aws_lb_target_group.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb_target_group) | resource |
| [aws_security_group.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/security_group) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_access_logs"></a> [access\_logs](#input\_access\_logs) | (Optional) S3 access log configuration. When null, access logging is disabled. | <pre>object({<br/>    bucket  = string<br/>    prefix  = optional(string, "")<br/>    enabled = optional(bool, true)<br/>  })</pre> | `null` | no |
| <a name="input_create_security_group"></a> [create\_security\_group](#input\_create\_security\_group) | (Optional) Whether to create a managed security group for the ALB. | `bool` | `true` | no |
| <a name="input_enable_deletion_protection"></a> [enable\_deletion\_protection](#input\_enable\_deletion\_protection) | (Optional) Whether to enable deletion protection on the ALB. | `bool` | `false` | no |
| <a name="input_idle_timeout"></a> [idle\_timeout](#input\_idle\_timeout) | (Optional) ALB idle connection timeout in seconds. Must be between 1 and 4000. | `number` | `60` | no |
| <a name="input_ingress_cidr_blocks"></a> [ingress\_cidr\_blocks](#input\_ingress\_cidr\_blocks) | (Optional) Allowed ingress CIDR blocks. CloudFront managed prefix list is preferred; supply via this variable. | `list(string)` | `[]` | no |
| <a name="input_internal"></a> [internal](#input\_internal) | (Optional) Whether the ALB is internal. Defaults to true because CloudFront is the public edge for this platform. | `bool` | `true` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"alb"` | no |
| <a name="input_name"></a> [name](#input\_name) | (Required) LB name. Must be alphanumeric and hyphens only, max 32 characters (ALB name limit). | `string` | n/a | yes |
| <a name="input_security_group_ids"></a> [security\_group\_ids](#input\_security\_group\_ids) | (Optional) List of existing security group IDs to attach to the ALB. If empty and create\_security\_group is true, a managed SG is created. | `list(string)` | `[]` | no |
| <a name="input_subnet_ids"></a> [subnet\_ids](#input\_subnet\_ids) | (Required) List of subnet IDs. At least 2 subnets across availability zones are required (spec 5.A). | `list(string)` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_target_groups"></a> [target\_groups](#input\_target\_groups) | (Required) List of target groups to create. Must be non-empty. The ADOT OTLP/HTTP port is typically 4318. | <pre>list(object({<br/>    name        = string<br/>    port        = number<br/>    protocol    = string<br/>    target_type = string<br/>    health_check = object({<br/>      path                = string<br/>      port                = optional(string, "traffic-port")<br/>      protocol            = optional(string, "HTTP")<br/>      healthy_threshold   = optional(number, 3)<br/>      unhealthy_threshold = optional(number, 3)<br/>      interval            = optional(number, 30)<br/>      timeout             = optional(number, 5)<br/>      matcher             = optional(string, "200")<br/>    })<br/>  }))</pre> | n/a | yes |
| <a name="input_vpc_id"></a> [vpc\_id](#input\_vpc\_id) | (Required) VPC ID where the ALB is deployed. | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_alb_arn"></a> [alb\_arn](#output\_alb\_arn) | The ARN of the Application Load Balancer. Passed to alb-listener as load\_balancer\_arn. |
| <a name="output_alb_dns_name"></a> [alb\_dns\_name](#output\_alb\_dns\_name) | The DNS name of the Application Load Balancer. Used as the CloudFront origin domain. |
| <a name="output_alb_zone_id"></a> [alb\_zone\_id](#output\_alb\_zone\_id) | The canonical hosted zone ID of the Application Load Balancer (for Route53 alias records). |
| <a name="output_security_group_id"></a> [security\_group\_id](#output\_security\_group\_id) | The ID of the managed security group, or null when create\_security\_group is false. |
| <a name="output_target_group_arns"></a> [target\_group\_arns](#output\_target\_group\_arns) | Map of target group name to ARN. Passed to alb-listener and ecs-service for attachment. |
<!-- END_TF_DOCS -->