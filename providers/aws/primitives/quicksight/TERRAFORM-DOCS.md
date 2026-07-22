<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | 6.0.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_quicksight_data_source.athena](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/quicksight_data_source) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_athena_data_source"></a> [athena\_data\_source](#input\_athena\_data\_source) | (Optional) Configuration for an optional Athena data source pointing at a cost-capped workgroup. When null, no data source is created. | <pre>object({<br/>    data_source_id = string<br/>    name           = string<br/>    workgroup_name = string<br/>    catalog        = optional(string, "AwsDataCatalog")<br/>    database       = optional(string, "default")<br/>  })</pre> | `null` | no |
| <a name="input_aws_account_id"></a> [aws\_account\_id](#input\_aws\_account\_id) | (Required) The 12-digit AWS account ID where QuickSight is active. | `string` | n/a | yes |
| <a name="input_data_source_permissions"></a> [data\_source\_permissions](#input\_data\_source\_permissions) | (Optional) Explicit permission grants applied to the Athena data source. Each principal must be a QuickSight user ARN (Standard-edition compatible). When empty, the data source is created with only the implicit creator access. | <pre>list(object({<br/>    principal = string<br/>    actions   = list(string)<br/>  }))</pre> | `[]` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"quicksight"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_data_source_arn"></a> [data\_source\_arn](#output\_data\_source\_arn) | The ARN of the optional Athena QuickSight data source. Empty string when no data source is configured. |
| <a name="output_data_source_id"></a> [data\_source\_id](#output\_data\_source\_id) | The ID of the optional Athena QuickSight data source. Empty string when no data source is configured. |
<!-- END_TF_DOCS -->