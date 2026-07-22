output "distribution_id" {
  description = "The CloudFront distribution ID."
  value       = aws_cloudfront_distribution.this.id
}

output "distribution_arn" {
  description = "The ARN of the CloudFront distribution."
  value       = aws_cloudfront_distribution.this.arn
}

output "distribution_domain_name" {
  description = "The domain name of the CloudFront distribution (e.g. dxxxx.cloudfront.net). Use as the alias target for Route53 A/AAAA records."
  value       = aws_cloudfront_distribution.this.domain_name
}

output "distribution_hosted_zone_id" {
  description = "The hosted zone ID of the CloudFront distribution. Used for Route53 alias A/AAAA records. The fixed CloudFront alias zone ID is Z2FDTNDATAQYW2 (AWS global constant)."
  value       = aws_cloudfront_distribution.this.hosted_zone_id
}

output "oac_id" {
  description = "The ID of the Origin Access Control (OAC) created for S3 origins. Null when origin_type is not 's3' or s3_oac_enabled is false."
  value       = try(aws_cloudfront_origin_access_control.this[0].id, null)
}

output "vpc_origin_id" {
  description = "The ID of the CloudFront VPC origin created for a 'vpc' origin (used by the distribution origin's vpc_origin_config). Null when origin_type is not 'vpc'."
  value       = try(aws_cloudfront_vpc_origin.this[0].id, null)
}

output "vpc_origin_arn" {
  description = "The ARN of the CloudFront VPC origin created for a 'vpc' origin. Null when origin_type is not 'vpc'."
  value       = try(aws_cloudfront_vpc_origin.this[0].arn, null)
}

output "vpc_origin_name" {
  description = "The Name assigned to the CloudFront VPC origin (aws_cloudfront_vpc_origin). Namespace-derived from origin.origin_id and deterministically bounded to CloudFront's VPC origin Name limit (64 chars) -- see local.vpc_origin_name. Null when origin_type is not 'vpc'."
  value       = local.is_vpc_origin ? local.vpc_origin_name : null
}
