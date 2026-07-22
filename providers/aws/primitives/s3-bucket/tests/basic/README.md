# s3-bucket basic tests

Terratest suite for the `examples/basic` configuration.

## Tests

| Test | Description |
|------|-------------|
| `TestBasicS3BucketARNFormat` | Asserts `bucket_arn` output matches `^arn:aws:s3:::`. |
| `TestBasicS3BucketPublicAccessBlock` | Asserts `aws_s3_bucket_public_access_block` resource count is 1. |
| `TestBasicS3BucketEncryptionPresent` | Asserts `aws_s3_bucket_server_side_encryption_configuration` resource count is 1. |
| `TestBasicS3BucketRequiredOutputsNotEmpty` | Asserts all required outputs (`bucket_id`, `bucket_arn`, `bucket_domain_name`, `bucket_regional_domain_name`) are non-empty. |
| `TestBasicS3BucketInvalidTransitionStorageClass` | Asserts plan fails for `transition_storage_class = "STANDARD_IA"`. |
| `TestBasicS3BucketSSEWithoutKMSKeyARN` | Asserts plan fails when `kms_key_arn` does not match `^arn:aws:kms:` (targets module root directly since the example is self-contained). |
| `TestBasicS3BucketExpirationBeforeTransition` | Asserts plan fails when `expiration_days <= transition_days`. |

## Running

From the repo root:

```bash
AWS_PROFILE=sandbox make tf-test MODULE_PATH=providers/aws/primitives/s3-bucket
```
