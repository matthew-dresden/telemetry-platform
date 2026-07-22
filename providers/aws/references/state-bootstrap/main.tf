# ---------------------------------------------------------------------------
# State CMK -- encrypts both S3 buckets.
# ---------------------------------------------------------------------------
module "state_kms_key" {
  source = var.state_kms_key_source

  alias_name          = var.kms_alias
  description         = "Customer managed key for Terraform remote state encryption"
  enable_key_rotation = true

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "kms-key"
}

# ---------------------------------------------------------------------------
# Access-log bucket -- receives server access logs from the artifact bucket.
# This terminal log bucket SELF-LOGS: its own access logs are written back to
# itself under the "<bucket_name>/" prefix (the s3-bucket primitive's
# target_prefix). S3 permits a bucket to be its own log target with a prefix --
# there is no circular recursion -- which clears trivy AWS-0089 (logging
# disabled) without a suppression. This mirrors the self-logging terminal
# access-log buckets in the portal and collector-ingestion reference examples.
# ---------------------------------------------------------------------------
module "access_log_bucket" {
  source = var.access_log_bucket_source

  bucket_name              = local.access_log_bucket_name
  force_destroy            = false
  versioning_enabled       = true
  kms_key_arn              = module.state_kms_key.key_arn
  bucket_key_enabled       = true
  access_log_target_bucket = local.access_log_bucket_name

  # ObjectOwnership = BucketOwnerPreferred (ACLs ENABLED) on the access-log bucket.
  # Terragrunt's S3 remote-state access logging (accesslogging_bucket_name in root.hcl)
  # writes a LogDelivery ACL grant onto this bucket. That grant requires ACLs to be
  # enabled; the s3-bucket primitive defaults object_ownership to BucketOwnerEnforced
  # (ACLs OFF), which rejects the grant with AccessControlListNotSupported. Setting
  # BucketOwnerPreferred here matches the lower-env (sandbox/qa) access-log buckets and
  # the existing live prod bucket, so the state-bootstrap unit plans 0 changes (no drift).
  # block_public_access still blocks ALL public ACLs, so enabling ACLs here is for the
  # bucket-owner/LogDelivery grant only and does not weaken the public-access posture.
  object_ownership = "BucketOwnerPreferred"

  block_public_access = {
    block_public_acls       = true
    block_public_policy     = true
    ignore_public_acls      = true
    restrict_public_buckets = true
  }

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "s3-bucket"

  depends_on = [module.state_kms_key]
}

# ---------------------------------------------------------------------------
# Artifact bucket -- holds Terraform state files; access logs go to the
# access-log bucket defined above.
# ---------------------------------------------------------------------------
module "artifact_bucket" {
  source = var.artifact_bucket_source

  bucket_name              = local.artifact_bucket_name
  force_destroy            = false
  versioning_enabled       = true
  kms_key_arn              = module.state_kms_key.key_arn
  bucket_key_enabled       = true
  access_log_target_bucket = local.access_log_bucket_name

  block_public_access = {
    block_public_acls       = true
    block_public_policy     = true
    ignore_public_acls      = true
    restrict_public_buckets = true
  }

  # TLS-only bucket policy: deny all HTTP (aws:SecureTransport=false) requests.
  # Required by spec AC #24/#25 hardening posture for the state bucket.
  bucket_policy_json = local.artifact_bucket_tls_policy

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "s3-bucket"

  depends_on = [module.state_kms_key, module.access_log_bucket]
}

# ---------------------------------------------------------------------------
# DynamoDB lock table removed (spec section 0.3 / D5, AC-14, E8-F6-S1-T1).
# S3-native conditional-write locking via remote_state { use_lockfile = true }
# (set at the root in E8-F3-S1-T2) is the single locking mechanism.
# ---------------------------------------------------------------------------
