locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # OAC is only created when origin type is S3 and s3_oac_enabled is true
  create_oac = var.origin.origin_type == "s3" && var.origin.s3_oac_enabled

  # Response headers policy is created when enable_response_headers_policy is true
  create_response_headers_policy = var.enable_response_headers_policy

  # Custom origin config applies to the public-internet origin types ('alb', 'custom').
  # A 'vpc' origin uses vpc_origin_config instead (CloudFront reaches a private/internal
  # resource through an aws_cloudfront_vpc_origin), and an 's3' origin uses neither.
  is_custom_origin = contains(["alb", "custom"], var.origin.origin_type)

  # VPC origin: CloudFront reaches a private/internal ALB, NLB, or EC2 instance inside the
  # VPC via a CloudFront VPC origin (aws_cloudfront_vpc_origin) instead of over the public
  # internet. Routing is private (by the VPC origin), not by public DNS resolution of the
  # origin domain_name, so a domain_name that resolves to this distribution does not loop.
  is_vpc_origin = var.origin.origin_type == "vpc"

  # ---------------------------------------------------------------------------
  # CloudFront VPC origin Name -- deterministically bounded to the service limit.
  #
  # CloudFront's CreateVpcOrigin rejects a VPC origin Name longer than this limit with
  # "InvalidArgument: The parameter VPC Origin Name is too big.". AWS does not publish the
  # limit in the CloudFront API or CloudFormation reference, so it is asserted here as a
  # documented constant. Verified empirically against the live CloudFront API (us-east-1,
  # 2026-06-28): a 64-character Name is accepted and a 65-character Name is rejected. The
  # Name length is validated BEFORE the origin ARN, so the limit applies independently of
  # the target resource.
  #
  # origin_id is namespace-derived by the caller, so for the longer prod/sandbox collector
  # namespaces the natural "<origin_id>-vpc-origin" name exceeds the limit (e.g. the
  # sandbox namespace "telemetry-useast1-sandbox-000-collector_ingestion-000-adot" yields a
  # 69-character name). Bound the name DETERMINISTICALLY while keeping it namespace-derived
  # and unique:
  #   - when the desired name already fits, use it verbatim (no change for short namespaces
  #     such as qa, so existing distributions are not renamed/replaced);
  #   - otherwise truncate the namespace-derived portion and append
  #     "-<8 hex of sha256(desired name)>" so two distinct desired names that share a
  #     truncated prefix never collide on the bounded name.
  # The result is a pure function of origin_id, so it is stable across plans/applies (no
  # resource churn). sha256 here is a collision-resistant uniqueness discriminator, NOT a
  # security control; 8 hex chars keep the suffix short while making a truncated-prefix
  # collision between two distinct namespaces astronomically unlikely.
  vpc_origin_name_max_length  = 64
  vpc_origin_name_hash_length = 8
  vpc_origin_desired_name     = "${var.origin.origin_id}-vpc-origin"
  vpc_origin_name = (
    length(local.vpc_origin_desired_name) <= local.vpc_origin_name_max_length
    ? local.vpc_origin_desired_name
    : format(
      "%s-%s",
      substr(
        local.vpc_origin_desired_name,
        0,
        local.vpc_origin_name_max_length - local.vpc_origin_name_hash_length - 1,
      ),
      substr(sha256(local.vpc_origin_desired_name), 0, local.vpc_origin_name_hash_length),
    )
  )
}
