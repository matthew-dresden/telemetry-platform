resource "aws_sns_topic" "this" {
  name              = var.topic_name
  kms_master_key_id = var.kms_key_id

  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "this" {
  for_each = {
    for idx, s in var.subscribers : tostring(idx) => s
  }

  topic_arn = aws_sns_topic.this.arn
  protocol  = each.value.protocol
  endpoint  = each.value.endpoint
}

resource "aws_sns_topic_policy" "this" {
  count = var.policy_json != null ? 1 : 0

  arn    = aws_sns_topic.this.arn
  policy = var.policy_json
}
