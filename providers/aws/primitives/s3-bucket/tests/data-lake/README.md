# s3-bucket data-lake tests

Terratest suite for the `examples/data-lake` configuration.

## Tests

| Test | Description |
|------|-------------|
| `TestDataLakeS3BucketLifecycleConfigurationExists` | Asserts `aws_s3_bucket_lifecycle_configuration` resource count is 1. |
| `TestDataLakeS3BucketLifecycleDaysMatchTfvars` | Asserts lifecycle configuration exists and apply succeeded with the declared transition (365d) and expiration (730d) values. |
| `TestDataLakeS3BucketLifecycleStorageClassGlacier` | Asserts the module accepted GLACIER as the `transition_storage_class`. |
| `TestDataLakeS3BucketIdempotency` | Asserts the data-lake example is idempotent: `aws_s3_bucket` count 1, `aws_s3_bucket_lifecycle_configuration` count 1. |

## Running

```bash
cd ../..
make tf-test
```
