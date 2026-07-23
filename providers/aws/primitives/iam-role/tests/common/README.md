# iam-role -- common tests

Cross-example tests covering both `examples/basic` and `examples/with-inline`.

## Test cases

| Test | Description |
|------|-------------|
| `TestCommonIAMRoleTerraformVersion` | Asserts Terraform version satisfies the `>= 1.12.1` pinned constraint (`AssertTerraformVersion 1.15.5`). |
| `TestCommonIAMRoleRequiredOutputsBasic` | Asserts `role_arn`, `role_name`, `role_id` are non-empty for the basic example. |
| `TestCommonIAMRoleRequiredOutputsWithInline` | Asserts `role_arn`, `role_name`, `role_id` are non-empty for the with-inline example. |
| `TestCommonIAMRoleValidateBasic` | Asserts the basic example applies successfully (confirming validate passed). |
| `TestCommonIAMRoleValidateWithInline` | Asserts the with-inline example applies successfully (confirming validate passed). |
| `TestCommonIAMRoleIdempotencyBasic` | Asserts the basic example is idempotent. |
| `TestCommonIAMRoleIdempotencyWithInline` | Asserts the with-inline example is idempotent. |
