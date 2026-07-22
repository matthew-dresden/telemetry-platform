resource "aws_quicksight_data_source" "athena" {
  count = var.athena_data_source != null ? 1 : 0

  aws_account_id = var.aws_account_id
  data_source_id = var.athena_data_source.data_source_id
  name           = var.athena_data_source.name
  type           = "ATHENA"

  parameters {
    athena {
      work_group = var.athena_data_source.workgroup_name
    }
  }

  ssl_properties {
    disable_ssl = false
  }

  # Permissions are input-driven (Standard-edition compatible). Each principal
  # must be a QuickSight user ARN supplied by the caller. When no permissions are
  # supplied the data source is created with only the implicit creator access.
  dynamic "permission" {
    for_each = var.data_source_permissions
    content {
      actions   = permission.value.actions
      principal = permission.value.principal
    }
  }

  tags = local.common_tags
}
