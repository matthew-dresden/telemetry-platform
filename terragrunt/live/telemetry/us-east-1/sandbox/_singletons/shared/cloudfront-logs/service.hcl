# live/telemetry/us-east-1/<env>/_singletons/shared/cloudfront-logs/service.hcl
#
# Service layer for the cloudfront-logs unit.
# Basename resolves to "cloudfront-logs" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit provisions the centralized CloudFront standard-access-logging destination
# bucket that the collector-ingestion reference (v1.0.2) REQUIRES via its
# access_log_bucket_name input (references/collector-ingestion/variables.tf:222,
# logging_config in main.tf). CloudFront standard (legacy) logging mandates a
# destination bucket that is:
#   - SSE-S3 (AES256), NOT a customer-managed KMS CMK -- CloudFront log delivery
#     rejects an SSE-KMS destination.
#   - ACL-enabled (ObjectOwnership = BucketOwnerPreferred) with FULL_CONTROL granted
#     to the CloudFront log-delivery canonical user, so the awslogsdelivery group can
#     write log files.
#   - same-account, same-region (us-east-1).
#
# WHY A generate BLOCK (no module source):
# The released s3-bucket primitive (providers/aws/primitives/s3-bucket v1.0.1) hardcodes
# sse_algorithm = "aws:kms" and exposes no aws_s3_bucket_acl grant for the CloudFront
# log-delivery canonical user, so it CANNOT produce a CloudFront-logging-compatible
# destination. No other released reference/primitive can either, and providers/aws/** is
# frozen (re-release+re-pin is operator-gated). The proven, correct resource spec already
# exists in the collector-ingestion EXAMPLE fixture
# (providers/aws/references/collector-ingestion/examples/default/main.tf, the access_logs
# bucket) -- a real, applied CloudFront-logging destination. This unit MIRRORS that exact
# spec via a terragrunt generate block, so the destination is stood up with no change to
# any frozen module. The unit carries NO terraform.source: the generated .tf files
# (plus the root-generated provider/versions/backend) ARE the root module. A unit with
# no source is skipped by the pinned-source guard (it scans for `source =`), which is
# correct here -- there is no module to pin.
#
# IDENTITY (D31): sandbox and prod are byte-for-byte identical; only the namespace-derived
# bucket name (local.namespace_dns-cloudfront-logs) differs per env. No account id, region,
# or env string is a literal in the generated config -- all derive from the namespace and
# data sources (namespace-derived-names law).
#
# SELF-LOGGING (AWS-0089): a CloudFront/S3 log-destination bucket is a TERMINAL log target.
# To satisfy trivy AWS-0089 (and keep server access logging enabled on every bucket) it
# records its OWN S3 server access logs under a dedicated prefix, exactly like the
# state-bootstrap access-log bucket and the example fixture's access_logs bucket. There is
# NO trivy suppression.
#
# CONSUMERS:
#   - collector-ingestion leaf: consumes cloudfront_logs_bucket_name via the
#     _envcommon/collector-ingestion.hcl dependency wiring and passes it to the module's
#     access_log_bucket_name input.
#
# Applied with the env's deploy credentials. No _envcommon include at this service layer.

locals {
  # service resolves to the directory basename, i.e. "cloudfront-logs".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}
