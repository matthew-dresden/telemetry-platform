resource "aws_iam_role" "this" {
  name                 = var.name
  description          = var.description
  path                 = var.path
  assume_role_policy   = var.assume_role_policy_json
  max_session_duration = var.max_session_duration
  permissions_boundary = var.permissions_boundary

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "managed" {
  for_each = toset(var.managed_policy_arns)

  role       = aws_iam_role.this.name
  policy_arn = each.value
}

resource "aws_iam_role_policy" "inline" {
  for_each = var.inline_policies

  name   = each.key
  role   = aws_iam_role.this.name
  policy = each.value
}
