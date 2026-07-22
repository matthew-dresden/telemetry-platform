# s3-bucket common tests

Terratest suite covering both `examples/basic` and `examples/data-lake`. This suite is required because the module ships more than one example (per docs/terragrunt-concepts.md test matrix).

## Tests

| Test | Description |
|------|-------------|
| `TestCommonS3TerraformVersionBasic` | Asserts Terraform version satisfies the `1.15.5` constraint for the basic example. |
| `TestCommonS3TerraformVersionDataLake` | Asserts Terraform version satisfies the `1.15.5` constraint for the data-lake example. |
| `TestCommonS3RequiredOutputsBasic` | Asserts required outputs (`bucket_id`, `bucket_arn`, `bucket_domain_name`) are non-empty for the basic example. |
| `TestCommonS3RequiredOutputsDataLake` | Asserts required outputs (`bucket_id`, `bucket_arn`, `bucket_domain_name`) are non-empty for the data-lake example. |
| `TestCommonS3NoPublicAccessBasic` | Asserts `aws_s3_bucket_public_access_block` count 1 and bucket ARN matches `^arn:aws:s3:::` for the basic example. |
| `TestCommonS3NoPublicAccessDataLake` | Asserts `aws_s3_bucket_public_access_block` count 1 and bucket ARN matches `^arn:aws:s3:::` for the data-lake example. |
| `TestCommonS3IdempotencyBasic` | Asserts the basic example is idempotent: `aws_s3_bucket` count 1, `aws_s3_bucket_versioning` count 1, `aws_s3_bucket_public_access_block` count 1. |
| `TestCommonS3IdempotencyDataLake` | Asserts the data-lake example is idempotent: `aws_s3_bucket` count 1, `aws_s3_bucket_lifecycle_configuration` count 1, `aws_s3_bucket_public_access_block` count 1. |

## Running

```bash
cd ../..
make tf-test
```
