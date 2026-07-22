# glue-catalog-database -- terraform-docs reference

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
| aws_glue_catalog_database.this | resource |
| aws_glue_catalog_table.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| database_name | The name of the Glue catalog database. Must contain only lowercase letters, digits, and underscores. | string | n/a | yes |
| description | Description for the Glue catalog database. | string | "Telemetry usage analytics catalog" | no |
| location_uri | The location of the database (for example, an HDFS path). Must start with s3:// when provided. | string | null | no |
| create_table | When true, provisions an aws_glue_catalog_table for Parquet format conversion. Requires the table variable to be non-null. | bool | false | no |
| table | Flexible-schema table configuration. Required when create_table is true. Must include tool and dt partition keys when create_table is true. location must start with s3://. | object | null | no |
| tags | Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source module. | string | "glue-catalog-database" | no |

## Outputs

| Name | Description |
|------|-------------|
| database_name | The name of the Glue catalog database. |
| database_arn | The Amazon Resource Name (ARN) of the Glue catalog database. |
| catalog_id | The account-level catalog ID for this Glue database. |
| table_name | The name of the Glue catalog table (the Firehose format-conversion target). Null when create_table is false. |

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.12.1 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.0.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | 6.0.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_glue_catalog_database.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/glue_catalog_database) | resource |
| [aws_glue_catalog_table.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/glue_catalog_table) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_create_table"></a> [create\_table](#input\_create\_table) | (Optional) When true, provisions an aws\_glue\_catalog\_table for Parquet format conversion. Requires the table variable to be non-null. | `bool` | `false` | no |
| <a name="input_database_name"></a> [database\_name](#input\_database\_name) | (Required) The name of the Glue catalog database. Must contain only lowercase letters, digits, and underscores. | `string` | n/a | yes |
| <a name="input_description"></a> [description](#input\_description) | (Optional) Description for the Glue catalog database. | `string` | `"Telemetry usage analytics catalog"` | no |
| <a name="input_location_uri"></a> [location\_uri](#input\_location\_uri) | (Optional) The location of the database (for example, an HDFS path). Must start with s3:// when provided. | `string` | `null` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"glue-catalog-database"` | no |
| <a name="input_table"></a> [table](#input\_table) | (Optional) Flexible-schema table configuration. Required when create\_table is true. Must include tool and dt partition keys when create\_table is true. location must start with s3://. | <pre>object({<br/>    name                        = string<br/>    location                    = string<br/>    input_format                = string<br/>    output_format               = string<br/>    serde_serialization_library = string<br/>    columns = list(object({<br/>      name    = string<br/>      type    = string<br/>      comment = optional(string, "")<br/>    }))<br/>    partition_keys = optional(list(object({<br/>      name = string<br/>      type = string<br/>    })), [])<br/>  })</pre> | `null` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_catalog_id"></a> [catalog\_id](#output\_catalog\_id) | The account-level catalog ID for this Glue database. |
| <a name="output_database_arn"></a> [database\_arn](#output\_database\_arn) | The Amazon Resource Name (ARN) of the Glue catalog database. |
| <a name="output_database_name"></a> [database\_name](#output\_database\_name) | The name of the Glue catalog database. |
| <a name="output_table_name"></a> [table\_name](#output\_table\_name) | The name of the Glue catalog table (the Firehose format-conversion target). Null when create\_table is false. |
<!-- END_TF_DOCS -->