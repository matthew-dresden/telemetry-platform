# terragrunt/common/naming.hcl
#
# Generic, terraform-module-AGNOSTIC resource-name fitting.
#
# Problem: AWS resource names have wildly different max lengths and charsets. The
# fully-qualified namespace (<product>-<region>-<env>-<envinst>-<service>-<svcinst>)
# is already ~48 chars; appending a resource suffix (-cluster, -tg, -exec-role) easily
# overflows a target-group's 32-char cap or an S3 bucket's 63-char cap, causing a
# failed apply. Truncating blindly causes collisions across sibling units/instance sets.
#
# Contract (keeps modules name-agnostic):
#   - Reference modules NEVER construct names internally. EVERY named resource takes
#     its name as an explicit input (bucket_name, cluster_name, role_name, ...).
#   - The terragrunt layer computes every name and FITS it to the resource kind's
#     constraint using the deterministic rule below. So adding/removing a module needs
#     zero naming code in the module, and the fit logic lives in exactly one place.
#
# Deterministic fit rule (apply in each _envcommon, fed by local._naming.max.<kind>):
#
#     clean     = lower + strip-illegal-chars-for-kind(candidate)
#     fitted    = length(clean) <= max
#                   ? clean
#                   : "${substr(clean, 0, max - 9)}-${substr(md5(candidate), 0, 8)}"
#
#   The 8-hex md5 suffix is derived from the FULL path-based candidate (md5() returns name-legal lowercase hex; base32 has no terraform builtin, so hex is used), so two sibling
#   units or two instance sets that share a 23/54-char prefix still get distinct names,
#   and a copied folder (…-001-…) yields a different hash => unique, deterministic,
#   copy-safe. "max - 9" reserves room for "-" + 8 hex.
#
# Charset rules by kind (applied as `clean` normalization):
#   s3 / glue   : lowercase, [a-z0-9-] (glue also allows _ but we standardize on -)
#   dns_label   : lowercase, [a-z0-9-], no leading/trailing -
#   iam / others: [A-Za-z0-9-] (case preserved where the service allows)
#
# Namespace form fed in (caller's responsibility): the canonical namespace keeps "_"
# WITHIN a field (telemetry-useast1-sandbox-000-collector_ingestion-000). The "_"-hostile
# kinds below MUST be fed the FLATTENED root local.namespace_dns ("_"->"-"), never the
# canonical namespace, or the produced name is illegal:
#   s3, dns_label  -> feed namespace_dns (S3 + DNS reject "_")
#   glue           -> feed namespace_dns (we standardize on "-" even though Glue allows "_")
#   iam / others   -> the canonical namespace is fine ("_" is legal for these kinds)
# This helper only FITS to length; it does not flatten "_", so the caller picks the form.
#
# Tags are NEVER fitted (generous limits); the per-field metadata tags on every
# resource (set in root.hcl default_tags) carry the full unabbreviated field values,
# so a fitted/hashed name is always recoverable without parsing the string.

locals {
  # Central constraint table: AWS resource kind -> max name length.
  # Sourced from AWS service quotas/docs; the strictest common cases are pinned.
  _name_max = {
    s3                 = 63
    alb                = 32
    target_group       = 32
    iam_role           = 64
    iam_policy         = 128
    lambda             = 64
    firehose           = 64
    log_group          = 512
    kms_alias          = 256
    sns                = 256
    sqs                = 80
    glue               = 255
    dynamodb           = 255
    ecs                = 255
    security_group     = 255
    secret             = 512
    cloudwatch_alarm   = 255
    cloudfront_comment = 128
    dns_label          = 63
  }

  # Exposed under a single key so leaves read one config: local._naming.max.<kind>.
  _naming = {
    max = local._name_max
  }
}
