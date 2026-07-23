# Route 53 DNS record wrapper.
# aws_route53_record does not support tags (per decision D3).
# The tags, managed_by_tag, and module_tag variables are accepted for
# call-site symmetry with other primitives but are not applied here.
#
# Exactly one of records or alias must be provided:
# - Static records path: set records (list of values) and ttl.
# - Alias path: set alias (object with name, zone_id, evaluate_target_health).
#   Records and ttl must not be set when using the alias path.
resource "aws_route53_record" "this" {
  zone_id = var.zone_id
  name    = var.name
  type    = var.type

  # Authoritative-record overwrite (default false; see var.allow_overwrite). Lets a unit that owns
  # a record (e.g. an NS delegation in a parent zone) overwrite a pre-existing record on rebuild
  # instead of failing with "already exists".
  allow_overwrite = var.allow_overwrite

  # Static records path: ttl and records are set only when alias is null.
  ttl     = var.alias == null ? var.ttl : null
  records = var.alias == null ? var.records : null

  # Alias path: emit a dynamic alias block when var.alias is non-null.
  dynamic "alias" {
    for_each = var.alias != null ? [var.alias] : []
    content {
      name                   = alias.value.name
      zone_id                = alias.value.zone_id
      evaluate_target_health = alias.value.evaluate_target_health
    }
  }

  lifecycle {
    precondition {
      condition     = (var.records != null) != (var.alias != null)
      error_message = "Exactly one of records or alias must be set. Supply records (with ttl) for static records, or alias for an AWS alias target -- not both, and not neither."
    }
  }
}
