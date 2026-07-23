# kms-key -- with-policy test suite

Terratest suite for the `examples/with-policy` configuration (custom policy JSON, `multi_region=true`).

## Tests

| Test | Assertion |
|------|-----------|
| `TestWithPolicyKMSKeyCreation` | `key_arn` matches the KMS ARN pattern; `alias_name` is non-empty. |
| `TestWithPolicyKMSKeyResourceCount` | Exactly 1 `aws_kms_key` in state. |
| `TestWithPolicyKMSKeyPolicyInState` | Both `key_arn` and `alias_arn` are non-empty, confirming the key was created with the custom policy applied. |

## Running

```bash
go test -v -timeout 20m ./tests/with-policy/...
```
