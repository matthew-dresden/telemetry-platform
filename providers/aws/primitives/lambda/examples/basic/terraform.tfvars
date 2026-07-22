function_name    = "telemetry-basic-example-lambda"
description      = "Basic example Lambda function for the telemetry portal embed-URL plane"
runtime          = "python3.12"
handler          = "index.handler"
package_type     = "Zip"
s3_bucket        = "telemetry-artifacts-example"
s3_key           = "lambda/embed-url/index.zip"
source_code_hash = "dGVzdA=="
role             = "arn:aws:iam::123456789012:role/telemetry-example-lambda-role"
environment_variables = {
  SSM_PATH_PREFIX = "/telemetry/example/portal"
}
# Non-default timeout/memory_size so the example + terratest exercise the new
# variables (the portal embed-URL Lambda uses 30s/256MB to avoid timing out on
# the QuickSight + SSM round trip that exceeds the 3s AWS default).
timeout     = 30
memory_size = 256
tags = {
  Environment = "test"
  Purpose     = "lambda-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
