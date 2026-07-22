# kms-key -- basic test suite

Terratest suite for the `examples/basic` configuration (rotation on, default KMS policy).

## Tests

| Test | Assertion |
|------|-----------|
| `TestBasicKMSKeyCreation` | `key_arn` matches `^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/`; `alias_name` starts with `alias/`. |
| `TestBasicKMSKeyResourceCounts` | Exactly 1 `aws_kms_key` and 1 `aws_kms_alias` in state. |
| `TestBasicKMSKeyOutputsNotEmpty` | All four required outputs are non-empty. |
| `TestBasicKMSKeyInvalidAlias` | Plan fails when `alias_name` contains invalid characters. |
| `TestBasicKMSKeyOutOfRangeDeletionWindow` | Plan fails when `deletion_window_in_days` is outside 7-30. |
| `TestBasicKMSKeyMalformedPolicyJSON` | Plan fails when `policy_json` is not valid JSON. |

## Running

```bash
go test -v -timeout 20m ./tests/basic/...
```
