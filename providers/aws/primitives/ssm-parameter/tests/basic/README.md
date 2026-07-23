# ssm-parameter -- basic tests

Tests for the `examples/basic` example: String parameter at the platform path convention.

## Test cases

| Test | Description |
|------|-------------|
| `TestBasicSSMParameterCreation` | Asserts the parameter ARN matches `^arn:aws:ssm:`. |
| `TestBasicSSMParameterResourceCount` | Asserts exactly 1 `aws_ssm_parameter`. |
| `TestBasicSSMParameterVersionAtLeastOne` | Asserts `parameter_version` is non-empty and not zero after creation. |
| `TestBasicSSMParameterOutputsNotEmpty` | Asserts `parameter_arn`, `parameter_name`, and `parameter_version` are non-empty. |
| `TestBasicSSMParameterIdempotency` | Asserts a second plan after apply shows no changes. |
| `TestBasicSSMParameterInvalidType` | Asserts plan fails for a type not in the allowed enum. |
| `TestBasicSSMParameterSecureStringWithoutKMSKeyFails` | Asserts plan fails when type is SecureString and kms_key_id is not provided. |
