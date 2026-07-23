# ssm-parameter -- common tests

Cross-example tests covering both `examples/basic` and `examples/securestring`.

## Test cases

| Test | Description |
|------|-------------|
| `TestCommonSSMParameterTerraformVersion` | Asserts Terraform version satisfies the `>= 1.12.1` pinned constraint (`AssertTerraformVersion 1.15.5`). |
| `TestCommonSSMParameterRequiredOutputsBasic` | Asserts `parameter_arn`, `parameter_name`, `parameter_version` are non-empty for the basic example. |
| `TestCommonSSMParameterRequiredOutputsSecureString` | Asserts `parameter_arn`, `parameter_name`, `parameter_version` are non-empty for the securestring example (metadata only, no secret value). |
| `TestCommonSSMParameterValidateBasic` | Asserts the basic example applies successfully (confirming validate passed). |
| `TestCommonSSMParameterValidateSecureString` | Asserts the securestring example applies successfully (confirming validate passed). |
| `TestCommonSSMParameterIdempotencyBasic` | Asserts the basic example is idempotent. |
| `TestCommonSSMParameterIdempotencySecureString` | Asserts the securestring example is idempotent. |
