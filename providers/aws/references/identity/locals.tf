locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # Derive the group-to-role mapping from input group names and role names.
  # No group or role names are hard-coded here -- all values come from inputs (AC-13, D8).
  # viewer maps to the viewer_group_name input (read-only via Identity Center permission set only).
  # author maps to the TelemetryAnalyst role ARN (D2r read-only Athena/Glue/S3-results).
  # admin maps to the TelemetryAdmin role ARN (D3r manage QuickSight/datasets).
  # The group name inputs (author_group_name, admin_group_name) are consumed here as map keys
  # alongside the permission_set_account_id, which is passed into the per-role description tag.
  group_role_map = {
    viewer = var.viewer_group_name
    author = module.analyst.role_arn
    admin  = module.admin.role_arn
  }

  # Group name annotations stored for downstream consumption (e.g., quicksight reference).
  # Each entry pairs the Identity Center group name with its associated IAM role ARN.
  # The author group maps to the analyst role (D8); the admin group maps to the admin role.
  group_annotations = {
    viewer_group = var.viewer_group_name
    author_group = var.author_group_name
    admin_group  = var.admin_group_name
  }

  # Permission-set account tag -- surfaces the account_id in resource tags so that
  # operators can identify which account hosts the Identity Center permission sets.
  permission_set_account_tag = var.permission_set_account_id
}
