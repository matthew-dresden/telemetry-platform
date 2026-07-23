# ssm-parameter -- securestring tests

Tests for the `examples/securestring` example: SecureString parameter at `/telemetry/prod/ingest/adot-config` bound to a fixture `telemetry-config` CMK.

## Security constraint

These tests MUST NOT read the secret value. All assertions use only parameter metadata outputs:
- `parameter_arn` -- the SSM parameter ARN
- `parameter_name` -- the fully qualified parameter name
- `parameter_version` -- the parameter version
- `kms_key_arn` -- the fixture CMK ARN (confirms binding without exposing the value)

## Test cases

| Test | Description |
|------|-------------|
| `TestSecureStringSSMParameterCreation` | Asserts the parameter ARN matches `^arn:aws:ssm:`. |
| `TestSecureStringSSMParameterResourceCount` | Asserts exactly 1 `aws_ssm_parameter`. |
| `TestSecureStringSSMParameterTypeInState` | Asserts parameter name follows the section-4 path and ARN matches; confirms SecureString is in state via resource count. |
| `TestSecureStringSSMParameterKMSBinding` | Asserts `kms_key_arn` matches `^arn:aws:kms:`, confirming the telemetry-config CMK was created and bound. |
| `TestSecureStringSSMParameterSectionFourPath` | Asserts `parameter_name` contains `/telemetry/prod/ingest/adot-config` (section-4 path). |
| `TestSecureStringSSMParameterIdempotency` | Asserts a second plan after apply shows no changes. |
