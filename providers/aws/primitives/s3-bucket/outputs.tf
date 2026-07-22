output "bucket_id" {
  description = "The name of the bucket (same as bucket_name input)."
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  description = "The Amazon Resource Name (ARN) of the bucket."
  value       = aws_s3_bucket.this.arn
}

output "bucket_domain_name" {
  description = "The bucket domain name in the format <bucket>.s3.amazonaws.com."
  value       = aws_s3_bucket.this.bucket_domain_name
}

output "bucket_regional_domain_name" {
  description = "The bucket region-specific domain name in the format <bucket>.s3.<region>.amazonaws.com."
  value       = aws_s3_bucket.this.bucket_regional_domain_name
}
