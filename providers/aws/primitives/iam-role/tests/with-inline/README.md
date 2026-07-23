# iam-role -- with-inline tests

Tests for the `examples/with-inline` example: Firehose trust policy with two inline policies.

## Test cases

| Test | Description |
|------|-------------|
| `TestWithInlineIAMRoleCreation` | Asserts the role ARN matches `^arn:aws:iam::[0-9]{12}:role/`. |
| `TestWithInlineIAMRolePolicyCount` | Asserts exactly 2 `aws_iam_role_policy` inline policy resources. |
| `TestWithInlineIAMRoleIdempotency` | Asserts a second plan after apply shows no changes. |
| `TestWithInlineIAMRoleMalformedInlinePolicy` | Asserts plan fails when an inline policy value is not valid JSON. |
