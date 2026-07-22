# kms-key -- common test suite

Cross-example Terratest suite required because the kms-key module has more than one example.

## Tests

| Test | Assertion |
|------|-----------|
| `TestCommonKMSTerraformVersion` | Terraform version equals `1.15.5` (satisfies `>= 1.12.1`). |
| `TestCommonKMSRequiredOutputsBasic` | All four required outputs (`key_id`, `key_arn`, `alias_name`, `alias_arn`) are non-empty for the basic example. |
| `TestCommonKMSRequiredOutputsWithPolicy` | All four required outputs are non-empty for the with-policy example. |
| `TestCommonKMSValidateBasic` | Basic example passes `terraform validate`. |
| `TestCommonKMSValidateWithPolicy` | With-policy example passes `terraform validate`. |
| `TestCommonKMSIdempotencyBasic` | Resource counts are stable on the basic example (idempotency). |
| `TestCommonKMSIdempotencyWithPolicy` | Resource counts are stable on the with-policy example (idempotency). |

## Running

```bash
go test -v -timeout 20m ./tests/common/...
```
