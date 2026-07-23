# iam-role -- basic tests

Tests for the `examples/basic` example: ECS-tasks trust policy with one managed policy attachment.

## Test cases

| Test | Description |
|------|-------------|
| `TestBasicIAMRoleCreation` | Asserts the role ARN matches `^arn:aws:iam::[0-9]{12}:role/`. |
| `TestBasicIAMRoleResourceCounts` | Asserts exactly 1 `aws_iam_role` and 1 `aws_iam_role_policy_attachment`. |
| `TestBasicIAMRoleOutputsNotEmpty` | Asserts `role_arn`, `role_name`, and `role_id` are non-empty. |
| `TestBasicIAMRoleIdempotency` | Asserts a second plan after apply shows no changes. |
| `TestBasicIAMRoleMalformedTrustPolicy` | Asserts plan fails for malformed `assume_role_policy_json`. |
| `TestBasicIAMRoleNameTooLong` | Asserts plan fails when role name exceeds 64 characters. |
