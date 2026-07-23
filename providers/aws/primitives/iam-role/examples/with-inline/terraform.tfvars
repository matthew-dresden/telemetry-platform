name = "telemetry-firehose-delivery-with-inline"

assume_role_policy_json = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "firehose.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

description = "Firehose delivery role -- with-inline example"

inline_policies = {
  s3-write = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetBucketLocation",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::telemetry-data-lake-example",
        "arn:aws:s3:::telemetry-data-lake-example/*"
      ]
    }
  ]
}
EOF

  kms-encrypt = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "kms:GenerateDataKey",
        "kms:Decrypt"
      ],
      "Resource": "arn:aws:kms:*:*:key/*"
    }
  ]
}
EOF
}

tags = {
  Environment = "test"
  Purpose     = "iam-role-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
