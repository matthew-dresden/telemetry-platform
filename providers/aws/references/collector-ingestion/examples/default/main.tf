data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# The collector CloudFront distribution reaches the internal ALB via a VPC origin; the
# ALB security group allows the AWS-managed CloudFront origin-facing prefix list on the
# HTTPS listener port. The live terragrunt leaves supply this id from a region-keyed
# config value (so the module performs no plan-time AWS read), but this self-contained
# terratest fixture resolves it from the data source -- a fixture is permitted live
# reads, and `terraform validate` does not evaluate data sources.
data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # Run-id-scoped uniqueness suffix (shared parallel-isolation idiom, D-2/D-4).
  # terratest injects a fresh per-run terratest_run_id (tt-<utc>-<rand>); its last
  # 6 chars are the random, collision-free tail. Folding it into BOTH the base name
  # and the namespace makes every globally/account-unique name this fixture derives
  # (S3 buckets, IAM roles, ECS cluster, the WAF-log KMS alias, the per-run Route53
  # sub-zone + its parent NS delegation, the namespace-scoped telemetry log group +
  # SSM path) unique per run, so two concurrent CI runs of THIS module apply in the
  # SAME qa account without colliding on a fixed name (the BucketAlreadyExists /
  # EntityAlreadyExists / Route53-already-exists failures that, when a run was
  # cancelled mid-apply, stranded a whole orphaned stack). The offline tfvars value
  # "offline-validate" keeps the suffix statically resolvable so trivy still proves
  # the access-log buckets' logging is enabled; substr/length are pure plan-time
  # functions (no AWS read, no opaque data source).
  run_suffix = substr(var.terratest_run_id, length(var.terratest_run_id) - 6, 6)
  name       = "${var.name}-${local.run_suffix}"

  # Fully-qualified 6-field namespace (product-region-env-envinstance-service-serviceinstance)
  # passed to the reference so it derives SET-SCOPED names (the telemetry ingest CloudWatch
  # log group and the ADOT SSM config path). The fixture's own log-group/SSM references below
  # are derived from this same namespace so the task-role IAM grant matches the names the
  # module actually creates (namespace-derived-names law). The run suffix is folded into the
  # serviceinstance field (no separator) so the namespace stays a valid 6-field value while
  # being unique per run (two concurrent runs never share the telemetry log group / SSM path).
  namespace = "${var.namespace}${local.run_suffix}"

  tags = merge(var.tags, {
    Purpose = "terratest-fixture"
  })

  # ---------------------------------------------------------------------------
  # Resource names derived from the name input -- nothing is hard-coded.
  # ---------------------------------------------------------------------------
  vpc_name            = "${local.name}-vpc"
  cluster_name        = "${local.name}-cluster"
  service_name        = "${local.name}-adot"
  task_role_name      = "${local.name}-adot-task-role"
  execution_role_name = "${local.name}-adot-exec-role"
  alb_name            = "${local.name}-alb"
  log_group_name      = "/telemetry/${local.name}/ingest"

  # Firehose + S3 names derived from name.
  # The S3 bucket namespace is GLOBAL: the firehose-destination bucket name carries the
  # account id so concurrent runs in different accounts (and the CI qa account) never
  # collide with a bucket of the same name owned by another account. Without the
  # account-id suffix, CreateBucket fails with AuthorizationHeaderMalformed when the
  # unqualified name is already squatted in another account/region.
  firehose_bucket_name = "${local.name}-firehose-dest-${local.account_id}"
  firehose_stream_name = "${local.name}-delivery"
  firehose_role_name   = "${local.name}-firehose-role"

  # Centralized access-log destination bucket name (CloudFront standard access logs +
  # Firehose-dest S3 server access logs). The fixture creates this bucket, so its name
  # carries the account id: the S3 namespace is GLOBAL and an unqualified name would
  # collide with a bucket of the same name owned by another account, failing CreateBucket
  # with AuthorizationHeaderMalformed. The account-id suffix comes from var.account_id (a
  # statically-resolvable input, 12-digit offline default; terratest injects the real
  # caller account id) -- NOT from the data.aws_caller_identity data source, whose value
  # is opaque to the static trivy scanner and made it report the CloudFront/S3 access-log
  # destination as logging-disabled (AWS-0010/AWS-0089).
  access_log_bucket = "${local.name}-access-logs-${var.account_id}"

  # KMS key alias for WAF log encryption.
  waf_kms_alias = "alias/${local.name}-waf-log"

  # Subnet layout -- derived from cidr_block, not hard-coded.
  subnet_layout = [
    {
      name              = "${local.name}-pub-1"
      cidr_block        = cidrsubnet(var.vpc_cidr_block, 4, 0)
      availability_zone = "${local.region}a"
      public            = true
    },
    {
      name              = "${local.name}-pub-2"
      cidr_block        = cidrsubnet(var.vpc_cidr_block, 4, 1)
      availability_zone = "${local.region}b"
      public            = true
    },
    {
      name              = "${local.name}-priv-1"
      cidr_block        = cidrsubnet(var.vpc_cidr_block, 4, 2)
      availability_zone = "${local.region}a"
      public            = false
    },
    {
      name              = "${local.name}-priv-2"
      cidr_block        = cidrsubnet(var.vpc_cidr_block, 4, 3)
      availability_zone = "${local.region}b"
      public            = false
    },
  ]

  # NAT gateway configuration -- one per public subnet for HA.
  nat_gateways = [
    {
      name               = "${local.name}-nat-1"
      public_subnet_name = "${local.name}-pub-1"
      connectivity_type  = "public"
    },
    {
      name               = "${local.name}-nat-2"
      public_subnet_name = "${local.name}-pub-2"
      connectivity_type  = "public"
    },
  ]

  # VPC interface endpoint service names for the region -- input-driven.
  interface_endpoint_service_names = [
    "com.amazonaws.${local.region}.ssm",
    "com.amazonaws.${local.region}.ssmmessages",
    "com.amazonaws.${local.region}.ecr.api",
    "com.amazonaws.${local.region}.ecr.dkr",
    "com.amazonaws.${local.region}.logs",
  ]

  # Private subnet names for interface endpoint attachment.
  interface_endpoint_subnet_names = [
    "${local.name}-priv-1",
  ]

  # S3 gateway endpoint service name -- input-driven.
  s3_gateway_endpoint_service_name = "com.amazonaws.${local.region}.s3"

  # ECS task execution role trust policy.
  execution_role_assume_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
      }
    ]
  })

  # ECS task role trust policy.
  task_role_assume_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
      }
    ]
  })

  # Task role inline policy -- CloudWatch Logs write scoped to the dedicated telemetry
  # ingest group the awscloudwatchlogs/awsemf ADOT exporters target. The :* suffix covers
  # every stream in the group. The task no longer writes to Firehose (the CWL subscription
  # filter does that via a separate role), so no firehose:PutRecord* is granted here.
  # logs:DescribeLogStreams is required by the awsemf exporter (structured metrics pipeline):
  # it calls DescribeLogStreams at startup (beyond the CreateLogStream/PutLogEvents the
  # awscloudwatchlogs exporters need) to look up the target stream's sequence token, so
  # without this action the exporter fails to start. No cloudwatch:PutMetricData is granted
  # -- awsemf delivers CloudWatch EMF log events; AWS extracts the metrics server-side from
  # the log payload, so the task never calls PutMetricData directly.
  telemetry_log_group_arn = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/telemetry/${local.namespace}/ingest/otlp-logs:*"

  task_role_inline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TelemetryCloudWatchLogsWrite"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
        ]
        Resource = [local.telemetry_log_group_arn]
      }
    ]
  })

  # Container definitions for the ADOT collector service.
  container_definitions = jsonencode([
    {
      name      = local.service_name
      image     = var.adot_image
      essential = true
      portMappings = [
        {
          containerPort = 4318
          protocol      = "tcp"
        },
        {
          # ADOT health_check extension port. Exposed on the task ENI so the internal ALB
          # target-group health check can reach 0.0.0.0:13133/ (HTTP 200) -- the OTLP
          # receiver on 4318 returns 404 on "/", so the ALB must probe this port instead.
          containerPort = 13133
          protocol      = "tcp"
        }
      ]
      environment = [
        {
          name  = "AOT_CONFIG_CONTENT_SSM_PATH"
          value = "/telemetry/${local.namespace}/ingest/adot-config"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = local.log_group_name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "adot"
        }
      }
    }
  ])

  # KMS key policy for the WAF log CMK.
  # Grants root admin access plus WAF/CloudWatch Logs access for WAF log delivery.
  waf_kms_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootAdminAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = ["kms:*"]
        Resource = "*"
      },
      {
        Sid    = "WafLoggingAccess"
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogsAccess"
        Effect = "Allow"
        Principal = {
          Service = "logs.${local.region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:log-group:*"
          }
        }
      },
    ]
  })

  # Firehose IAM role trust policy.
  firehose_assume_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "firehose.amazonaws.com" }
        Condition = {
          StringEquals = {
            "sts:ExternalId" = local.account_id
          }
        }
      }
    ]
  })

  # Firehose role inline policy -- S3 delivery access.
  firehose_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3DeliveryAccess"
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:ListBucketMultipartUploads",
          "s3:PutObject",
        ]
        Resource = [
          aws_s3_bucket.firehose_dest.arn,
          "${aws_s3_bucket.firehose_dest.arn}/*",
        ]
      }
    ]
  })

  # Per-run DNS sub-zone apex. The fixture creates and delegates this zone
  # (aws_route53_zone.sandbox + aws_route53_record.sandbox_zone_delegation); BOTH
  # collector FQDNs are NON-APEX names within it. The sub-zone apex is deliberately
  # NOT the pretty FQDN: the module writes the public collector record as a CNAME at
  # collector_pretty_fqdn, and Route53 forbids a CNAME at a zone apex
  # ("not permitted at apex"). Nesting the FQDNs one label below the apex avoids that.
  dns_subzone_apex = "${local.name}.${var.sandbox_domain}"

  # Pretty FQDN for the collector endpoint -- a non-apex name within the sub-zone, so the
  # module's public CNAME record is legal. In production this is collector.<pretty_apex>;
  # here it is collector.<name>.<sandbox_domain>.
  collector_pretty_fqdn = "collector.${local.dns_subzone_apex}"

  # Service FQDN nested under the pretty FQDN (svc.<pretty>) for two reasons:
  #   1. The is_active CloudFront distribution attaches BOTH collector_service_fqdn and
  #      collector_pretty_fqdn as aliases (CNAMEs). CloudFront rejects the viewer
  #      certificate unless the cert covers EVERY alias, so the fixture ACM cert carries
  #      collector_service_fqdn as a subject_alternative_name (InvalidViewerCertificate
  #      otherwise).
  #   2. Nesting it under the pretty FQDN keeps it inside the single delegated sub-zone so
  #      its ACM DNS-validation CNAME is publicly resolvable; a sibling subtree would fall
  #      outside the sub-zone and the validation CNAME would be re-suffixed and unresolvable.
  collector_service_fqdn = "svc.${local.collector_pretty_fqdn}"
}

# ---------------------------------------------------------------------------
# D37 prerequisite: KMS key for WAF log encryption (docs/terragrunt-concepts.md).
# All resource names are derived from the name local.
# ---------------------------------------------------------------------------
resource "aws_kms_key" "waf_log" {
  description             = "WAF log encryption CMK for ${local.name} terratest fixture"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = local.waf_kms_policy

  tags = merge(local.tags, { Name = "${local.name}-waf-log-key" })
}

resource "aws_kms_alias" "waf_log" {
  name          = local.waf_kms_alias
  target_key_id = aws_kms_key.waf_log.key_id
}

# ---------------------------------------------------------------------------
# VPC Flow Logs destination + role fixtures. VPC Flow Logs are enabled on the
# composed vpc-network so VPC traffic is auditable (trivy AWS-0178). The CMK on
# the log group is the same fixture WAF/telemetry CMK whose policy already grants
# logs.<region>.amazonaws.com via the EncryptionContext condition.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/vpc/${local.name}/flow-logs"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.waf_log.arn

  tags = local.tags
}

resource "aws_iam_role" "flow_logs" {
  name = "${local.name}-vpc-flow-logs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "vpc-flow-logs.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = local.tags
}

# ---------------------------------------------------------------------------
# D37 prerequisite: S3 bucket as Firehose delivery destination.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "firehose_dest" {
  bucket        = local.firehose_bucket_name
  force_destroy = true

  tags = merge(local.tags, { Name = local.firehose_bucket_name })
}

resource "aws_s3_bucket_public_access_block" "firehose_dest" {
  bucket = aws_s3_bucket.firehose_dest.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "firehose_dest" {
  bucket = aws_s3_bucket.firehose_dest.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "firehose_dest" {
  bucket = aws_s3_bucket.firehose_dest.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.waf_log.arn
    }
    bucket_key_enabled = true
  }
}

# S3 server access logging for the Firehose destination bucket to the centralized
# access-log bucket. The destination (aws_s3_bucket.access_logs) is created below in
# this fixture; in production it is owned outside this module. S3 server access log
# destination buckets must use SSE-S3 (AES256), so it is not CMK-encrypted.
resource "aws_s3_bucket_logging" "firehose_dest" {
  bucket        = aws_s3_bucket.firehose_dest.id
  target_bucket = local.access_log_bucket
  target_prefix = "${local.firehose_bucket_name}/"

  depends_on = [aws_s3_bucket_acl.access_logs]
}

# ---------------------------------------------------------------------------
# Centralized access-log DESTINATION bucket fixture.
#
# This single bucket is the destination for both:
#   - S3 server access logs (the Firehose-destination bucket), and
#   - CloudFront standard (legacy) access logs (the collector distribution).
#
# In production this bucket is owned outside the collector-ingestion reference
# and is passed in by name only. The reference does NOT create it, so the
# terratest fixture must stand up a real, correctly-configured destination so the
# apply-path CloudFront/S3 logging wiring has somewhere to write.
#
# CloudFront standard logging and S3 server access logging both REQUIRE the
# destination bucket to have ACLs enabled (BucketOwnerPreferred) and use SSE-S3
# (AES256) -- SSE-KMS with a customer CMK is REJECTED by both log delivery paths.
# The ACL grants FULL_CONTROL to the bucket owner and the CloudFront log-delivery
# canonical user, plus WRITE/READ_ACP to the S3 LogDelivery group. The bucket name
# is kept statically resolvable (no data-source interpolation) so trivy can resolve
# the logging target.
# ---------------------------------------------------------------------------
data "aws_canonical_user_id" "current" {}
data "aws_cloudfront_log_delivery_canonical_user_id" "current" {}

resource "aws_s3_bucket" "access_logs" {
  bucket        = local.access_log_bucket
  force_destroy = true

  tags = merge(local.tags, { Name = local.access_log_bucket })
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_ownership_controls" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  access_control_policy {
    owner {
      id = data.aws_canonical_user_id.current.id
    }

    # Bucket owner retains full control.
    grant {
      grantee {
        id   = data.aws_canonical_user_id.current.id
        type = "CanonicalUser"
      }
      permission = "FULL_CONTROL"
    }

    # CloudFront standard (legacy) logging writes as the CloudFront log-delivery
    # canonical user, which requires FULL_CONTROL on the destination bucket.
    grant {
      grantee {
        id   = data.aws_cloudfront_log_delivery_canonical_user_id.current.id
        type = "CanonicalUser"
      }
      permission = "FULL_CONTROL"
    }

    # S3 server access logging writes as the S3 LogDelivery group, which needs
    # WRITE on the bucket and READ_ACP on the bucket ACL.
    grant {
      grantee {
        type = "Group"
        uri  = "http://acs.amazonaws.com/groups/s3/LogDelivery"
      }
      permission = "WRITE"
    }

    grant {
      grantee {
        type = "Group"
        uri  = "http://acs.amazonaws.com/groups/s3/LogDelivery"
      }
      permission = "READ_ACP"
    }
  }

  depends_on = [
    aws_s3_bucket_ownership_controls.access_logs,
    aws_s3_bucket_public_access_block.access_logs,
  ]
}

# Log destination buckets must use SSE-S3 (AES256); CloudFront standard logging
# and S3 server access logging both reject a customer-managed CMK.
resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# The access-log bucket records its OWN server access logs under a dedicated
# prefix (an S3 bucket may be its own log target). This keeps server access
# logging enabled on every bucket in the fixture without an extra dependency.
resource "aws_s3_bucket_logging" "access_logs" {
  bucket        = aws_s3_bucket.access_logs.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "${local.access_log_bucket}/server-access/"

  depends_on = [aws_s3_bucket_acl.access_logs]
}

# ---------------------------------------------------------------------------
# D37 prerequisite: IAM role for Firehose delivery.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "firehose" {
  name               = local.firehose_role_name
  assume_role_policy = local.firehose_assume_policy

  tags = merge(local.tags, { Name = local.firehose_role_name })
}

resource "aws_iam_role_policy" "firehose" {
  name   = "${local.firehose_role_name}-s3-policy"
  role   = aws_iam_role.firehose.id
  policy = local.firehose_role_policy
}

# ---------------------------------------------------------------------------
# D37 prerequisite: Kinesis Firehose delivery stream backed by S3.
# ---------------------------------------------------------------------------
resource "aws_kinesis_firehose_delivery_stream" "fixture" {
  name        = local.firehose_stream_name
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose.arn
    bucket_arn = aws_s3_bucket.firehose_dest.arn
    prefix     = "telemetry/"
  }

  tags = merge(local.tags, { Name = local.firehose_stream_name })

  depends_on = [aws_iam_role_policy.firehose, aws_s3_bucket_public_access_block.firehose_dest]
}

# ---------------------------------------------------------------------------
# D37 prerequisite: Route53 public hosted zone for the per-run DNS sub-zone.
#
# This is a NEW delegated sub-zone (<name>.<sandbox_domain>), NOT the apex
# sandbox_domain zone. The apex sandbox_domain zone already exists live and is
# NS-delegated from the dns-owner apex; standing up a second apex zone of the same
# name here would create an orphan zone whose nameservers nothing points to, so the
# ACM-validation CNAME written into it would never be publicly resolvable. Creating a
# dedicated sub-zone and delegating it from the live parent
# (aws_route53_record.sandbox_zone_delegation below) makes both the validation CNAMEs
# and the public collector CNAME resolvable.
#
# The sub-zone apex is <name>.<sandbox_domain>, NOT collector_pretty_fqdn: the module
# writes the public collector record as a CNAME at collector_pretty_fqdn, and a CNAME is
# forbidden at a zone apex. Keeping collector_pretty_fqdn one label below the apex makes
# that record a legal non-apex CNAME.
# ---------------------------------------------------------------------------
resource "aws_route53_zone" "sandbox" {
  name    = local.dns_subzone_apex
  comment = "Sandbox DNS sub-zone for ${local.name} terratest fixture"

  tags = merge(local.tags, { Name = "${local.name}-zone" })
}

# ---------------------------------------------------------------------------
# Delegate the per-run sub-zone from the live parent zone. Writing the sub-zone's
# name_servers as an NS record in the parent (sandbox_parent_zone_id = the live,
# already-delegated qa.platform zone) makes the sub-zone publicly resolvable so the
# ACM DNS-validation CNAMEs the fixture writes into it are visible to ACM's public
# resolvers and the aws_acm_certificate_validation waiter reaches ISSUED.
# ---------------------------------------------------------------------------
resource "aws_route53_record" "sandbox_zone_delegation" {
  zone_id = var.sandbox_parent_zone_id
  name    = aws_route53_zone.sandbox.name
  type    = "NS"
  ttl     = 60
  records = aws_route53_zone.sandbox.name_servers
}

# ---------------------------------------------------------------------------
# D37 prerequisite: ACM certificate with Route53 DNS validation.
# The certificate covers BOTH collector FQDNs that the is_active CloudFront
# distribution attaches as aliases: collector_pretty_fqdn (collector.<sub-zone apex>)
# as the primary domain and collector_service_fqdn (svc.collector.<sub-zone apex>) as a
# subject_alternative_name. CloudFront rejects the viewer certificate with
# InvalidViewerCertificate unless the cert covers EVERY alias on the distribution.
# Both names are within the delegated sub-zone, so both DNS-validation CNAMEs resolve.
# ---------------------------------------------------------------------------
resource "aws_acm_certificate" "collector" {
  domain_name       = local.collector_pretty_fqdn
  validation_method = "DNS"

  subject_alternative_names = [
    local.collector_service_fqdn,
  ]

  tags = merge(local.tags, { Name = "${local.name}-cert" })

  lifecycle {
    create_before_destroy = true
  }
}

# Route53 validation records for the ACM certificate.
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.collector.domain_validation_options :
    dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id = aws_route53_zone.sandbox.zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]

  allow_overwrite = true
}

# ---------------------------------------------------------------------------
# collector-ingestion reference module -- all D37 prerequisites supplied
# from resources created above; no external TF_VAR values required beyond
# project_tag and terratest_run_id (AC-FIX-002).
#
# The Internet Gateway (docs/terragrunt-concepts.md) and the ADOT ECS task security group
# (docs/terragrunt-concepts.md) are now created INSIDE the module tree (vpc-network owns the IGW;
# collector-ingestion owns the ADOT task SG), so the fixture no longer creates
# them and no longer passes internet_gateway_id / adot_security_group_ids.
# ---------------------------------------------------------------------------
module "example" {
  source = "../../"

  # D37 inputs -- wired from fixture-created resources, never externally supplied.
  firehose_delivery_stream_arn = aws_kinesis_firehose_delivery_stream.fixture.arn
  collector_service_fqdn       = local.collector_service_fqdn
  collector_pretty_fqdn        = local.collector_pretty_fqdn
  prod_hosted_zone_id          = aws_route53_zone.sandbox.zone_id
  certificate_arn              = aws_acm_certificate.collector.arn
  domain_validation_options = [
    for dvo in aws_acm_certificate.collector.domain_validation_options : {
      domain_name           = dvo.domain_name
      resource_record_name  = dvo.resource_record_name
      resource_record_type  = dvo.resource_record_type
      resource_record_value = dvo.resource_record_value
    }
  ]

  # WAF log CMK wired from the fixture-created KMS key.
  waf_log_kms_key_arn = aws_kms_key.waf_log.arn

  # Centralized access-log destination bucket name for the CloudFront standard logs.
  access_log_bucket_name = local.access_log_bucket

  # CloudFront origin-facing managed prefix list id allowed inbound on the internal ALB
  # SG so CloudFront reaches the ALB through the VPC origin (resolved from the data source
  # above; the live leaves supply it from region-keyed config instead).
  cloudfront_origin_facing_prefix_list_id = data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id

  # VPC Flow Logs wired to the fixture log group + role.
  enable_flow_logs          = true
  flow_logs_iam_role_arn    = aws_iam_role.flow_logs.arn
  flow_logs_destination_arn = aws_cloudwatch_log_group.flow_logs.arn

  # Telemetry CloudWatch Logs ingest hop (awscloudwatchlogs exporter -> log group ->
  # subscription filter -> Firehose). The CMK reuses the fixture WAF-log CMK whose
  # policy already grants logs.<region> via the ArnLike EncryptionContext condition.
  telemetry_log_kms_key_arn = aws_kms_key.waf_log.arn
  cwl_to_firehose_role_name = "${local.name}-cwl-to-fh"

  # docs/terragrunt-concepts.md runtime inputs.
  adot_image                          = var.adot_image
  adot_task_cpu                       = var.adot_task_cpu
  adot_task_memory                    = var.adot_task_memory
  adot_receiver_max_request_body_size = var.adot_receiver_max_request_body_size
  memory_limiter_limit_mib            = var.memory_limiter_limit_mib
  memory_limiter_spike_limit_mib      = var.memory_limiter_spike_limit_mib
  rate_limit_per_ip                   = var.rate_limit_per_ip
  vpc_cidr_block                      = var.vpc_cidr_block
  subnet_layout                       = local.subnet_layout
  structured_otlp_service_names       = var.structured_otlp_service_names

  # vpc-network required inputs -- wired from fixture locals. The IGW is created
  # inside vpc-network; the ADOT task SG is created inside collector-ingestion.
  nat_gateways                     = local.nat_gateways
  interface_endpoint_service_names = local.interface_endpoint_service_names
  interface_endpoint_subnet_names  = local.interface_endpoint_subnet_names
  s3_gateway_endpoint_service_name = local.s3_gateway_endpoint_service_name

  # Deployment topology inputs.
  # is_active=true attaches the pretty-name CloudFront alias (this fixture stands up a single
  # active set). namespace drives the SET-SCOPED telemetry log group + ADOT SSM config path.
  is_active           = true
  namespace           = local.namespace
  env                 = local.name
  vpc_name            = local.vpc_name
  cluster_name        = local.cluster_name
  service_name        = local.service_name
  task_role_name      = local.task_role_name
  execution_role_name = local.execution_role_name
  alb_name            = local.alb_name
  log_group_name      = local.log_group_name

  # IAM roles trust policies and inline policies.
  execution_role_assume_policy_json = local.execution_role_assume_policy
  task_role_assume_policy_json      = local.task_role_assume_policy
  task_role_inline_policies = {
    TelemetryLogsAccess = local.task_role_inline_policy
  }

  # Container definition for the ADOT ECS task.
  container_definitions = local.container_definitions

  tags = local.tags

  depends_on = [
    aws_kinesis_firehose_delivery_stream.fixture,
    aws_kms_key.waf_log,
    aws_acm_certificate.collector,
    aws_route53_zone.sandbox,
    aws_s3_bucket_acl.access_logs,
    # The sub-zone must be delegated from the parent and the validation CNAMEs written
    # BEFORE the module's aws_acm_certificate_validation waiter starts polling, otherwise
    # the validation records are not publicly resolvable and the waiter never reaches ISSUED.
    aws_route53_record.sandbox_zone_delegation,
    aws_route53_record.cert_validation,
  ]
}

# -- Outputs re-exported from the collector-ingestion reference (AC-1) --

output "cloudfront_domain_name" {
  description = "CloudFront distribution domain name (AC-1)."
  value       = module.example.cloudfront_domain_name
}

output "cloudfront_hosted_zone_id" {
  description = "CloudFront distribution hosted zone ID (AC-1)."
  value       = module.example.cloudfront_hosted_zone_id
}

output "alb_arn" {
  description = "ARN of the Application Load Balancer (AC-1)."
  value       = module.example.alb_arn
}

output "ecs_service_name" {
  description = "Name of the ADOT ECS service (AC-1)."
  value       = module.example.ecs_service_name
}

output "ecs_cluster_name" {
  description = "Name of the ADOT ECS cluster; used with ecs_service_name to locate the running task definition for the config-fingerprint assertion."
  value       = module.example.ecs_cluster_name
}

# -- Echo outputs for Terratest contract assertions --

output "firehose_delivery_stream_arn_echo" {
  description = "Echo of the firehose_delivery_stream_arn input (D37 contract assertion)."
  value       = module.example.firehose_delivery_stream_arn_echo
}

output "waf_managed_rule_names" {
  description = "Comma-separated list of WAF managed rule group names (docs/terragrunt-concepts.md assertion)."
  value       = module.example.waf_managed_rule_names
}

output "waf_managed_rule_groups_echo" {
  description = "JSON-encoded WAF managed rule groups config incl. the CommonRuleSet SizeRestrictions_BODY -> count override (re-exported for Terratest, docs/terragrunt-concepts.md, BUG-1)."
  value       = module.example.waf_managed_rule_groups_echo
}

output "waf_rate_limit_per_ip_echo" {
  description = "Echo of the rate_limit_per_ip input value (D44 assertion)."
  value       = module.example.waf_rate_limit_per_ip_echo
}

output "waf_logging_enabled_echo" {
  description = "Echo of the WAF logging_enabled flag (docs/terragrunt-concepts.md assertion)."
  value       = module.example.waf_logging_enabled_echo
}

output "waf_log_kms_key_arn_echo" {
  description = "Echo of the waf_log_kms_key_arn input (docs/terragrunt-concepts.md assertion)."
  value       = module.example.waf_log_kms_key_arn_echo
}

output "adot_config_content_echo" {
  description = "Rendered ADOT AOT_CONFIG_CONTENT for Terratest assertions (D5, D27.3)."
  value       = module.example.adot_config_content_echo
}

output "adot_config_sha256_echo" {
  description = "sha256 fingerprint of the rendered ADOT config, embedded in the task definition (ADOT_CONFIG_SHA256) so config changes roll the service; passed through for Terratest assertions."
  value       = module.example.adot_config_sha256_echo
}

# -- Fixture resource ARNs for Terratest assertions --

output "fixture_firehose_arn" {
  description = "ARN of the fixture Firehose delivery stream (created as D37 prerequisite)."
  value       = aws_kinesis_firehose_delivery_stream.fixture.arn
}

output "fixture_waf_kms_key_arn" {
  description = "ARN of the fixture WAF log KMS key (created as D37 prerequisite)."
  value       = aws_kms_key.waf_log.arn
}

output "fixture_hosted_zone_id" {
  description = "ID of the fixture Route53 hosted zone (created as D37 prerequisite)."
  value       = aws_route53_zone.sandbox.zone_id
}

output "fixture_certificate_arn" {
  description = "ARN of the fixture ACM certificate (created as D37 prerequisite)."
  value       = aws_acm_certificate.collector.arn
}

output "adot_security_group_id" {
  description = "ID of the ADOT ECS task security group created inside the collector-ingestion module (docs/terragrunt-concepts.md)."
  value       = module.example.adot_security_group_id
}

# -- ADOT health-check / SG echoes re-exported from the reference for Terratest
# assertions (the module exposes these; the fixture must re-export them so the
# tests' terraform.Output calls against the fixture root resolve). --

output "adot_health_check_port_echo" {
  description = "Echo of the ADOT health_check extension bind port (re-exported for Terratest)."
  value       = module.example.adot_health_check_port_echo
}

output "adot_target_group_health_check_path_echo" {
  description = "Echo of the ALB target-group health check path (re-exported for Terratest)."
  value       = module.example.adot_target_group_health_check_path_echo
}

output "adot_target_group_health_check_port_echo" {
  description = "Echo of the ALB target-group health check port (re-exported for Terratest)."
  value       = module.example.adot_target_group_health_check_port_echo
}

output "adot_target_group_health_check_matcher_echo" {
  description = "Echo of the ALB target-group health check success matcher (re-exported for Terratest)."
  value       = module.example.adot_target_group_health_check_matcher_echo
}

output "adot_security_group_ingress_ports_echo" {
  description = "Comma-separated sorted ADOT task SG ingress from_port list (re-exported for Terratest)."
  value       = module.example.adot_security_group_ingress_ports_echo
}

# -- ADOT high-concurrency autoscaling/baseline echoes re-exported for Terratest assertions. --

output "adot_desired_count_echo" {
  description = "Echo of the ADOT service desired task count (baseline for high-concurrency ingestion, re-exported for Terratest)."
  value       = module.example.adot_desired_count_echo
}

output "adot_enable_autoscaling_echo" {
  description = "Echo of whether ADOT Application Auto Scaling is enabled (re-exported for Terratest)."
  value       = module.example.adot_enable_autoscaling_echo
}

output "adot_autoscaling_echo" {
  description = "JSON echo of the ADOT autoscaling config (min/max capacity, target metric, cooldowns; re-exported for Terratest)."
  value       = module.example.adot_autoscaling_echo
}

# -- Telemetry CloudWatch Logs ingest-hop echoes re-exported for Terratest assertions. --

output "telemetry_log_group_name" {
  description = "Name of the dedicated telemetry CloudWatch Logs ingest group (re-exported for Terratest)."
  value       = module.example.telemetry_log_group_name
}

output "telemetry_log_group_arn" {
  description = "ARN of the dedicated telemetry CloudWatch Logs ingest group (re-exported for Terratest)."
  value       = module.example.telemetry_log_group_arn
}

output "cwl_to_firehose_role_arn" {
  description = "ARN of the CloudWatch-Logs-to-Firehose delivery role (re-exported for Terratest)."
  value       = module.example.cwl_to_firehose_role_arn
}

output "cwl_to_firehose_trust_source_arns_echo" {
  description = "JSON list of aws:SourceArn values in the CWL-to-Firehose role trust policy (must contain both the bare log-group ARN and the ':*' form; re-exported for Terratest, BUG-2)."
  value       = module.example.cwl_to_firehose_trust_source_arns_echo
}

output "telemetry_subscription_filter_destination_arn" {
  description = "Subscription filter destination (data-lake Firehose ARN) echo (re-exported for Terratest)."
  value       = module.example.telemetry_subscription_filter_destination_arn
}
