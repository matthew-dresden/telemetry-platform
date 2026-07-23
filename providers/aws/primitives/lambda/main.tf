# Lambda function (Zip package type from S3).
# The deployment artifact is referenced by S3 bucket, key, and source_code_hash;
# no code or credentials are embedded in this module (trust model per spec Section 3.6).
resource "aws_lambda_function" "this" {
  function_name    = var.function_name
  description      = var.description
  runtime          = var.runtime
  handler          = var.handler
  package_type     = var.package_type
  s3_bucket        = var.s3_bucket
  s3_key           = var.s3_key
  source_code_hash = var.source_code_hash
  role             = var.role
  timeout          = var.timeout
  memory_size      = var.memory_size

  # Emit the environment block only when environment_variables is non-empty.
  # An empty block would cause Terraform to emit environment = {} which is valid
  # but unnecessary; the dynamic pattern avoids the empty block entirely.
  dynamic "environment" {
    for_each = length(var.environment_variables) > 0 ? [var.environment_variables] : []
    content {
      variables = environment.value
    }
  }

  # Active X-Ray tracing is enabled by default (secure-by-default) so requests are
  # sampled and traced. Override tracing_mode to "PassThrough" to only trace when an
  # upstream caller already sampled the request.
  tracing_config {
    mode = var.tracing_mode
  }

  tags = local.common_tags
}
