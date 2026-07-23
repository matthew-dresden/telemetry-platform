variable "zone_id" {
  type        = string
  description = "(Required) ID of the hosted zone in which to create the record."
}

variable "name" {
  type        = string
  description = "(Required) DNS name of the record."
}

variable "type" {
  type        = string
  description = "(Required) DNS record type. Valid values: A, AAAA, CAA, CNAME, DS, MX, NAPTR, NS, PTR, SOA, SPF, SRV, TXT."

  validation {
    condition = contains(
      ["A", "AAAA", "CAA", "CNAME", "DS", "MX", "NAPTR", "NS", "PTR", "SOA", "SPF", "SRV", "TXT"],
      var.type
    )
    error_message = "type must be one of A, AAAA, CAA, CNAME, DS, MX, NAPTR, NS, PTR, SOA, SPF, SRV, or TXT."
  }
}

variable "records" {
  type        = list(string)
  description = "(Optional) List of static record values. Required when alias is null. Must not be set when alias is provided."
  default     = null

  validation {
    condition     = var.records == null || length(var.records) > 0
    error_message = "records must contain at least one value when provided."
  }
}

variable "ttl" {
  type        = number
  description = "(Optional) Time to live for the DNS record in seconds. Required when alias is null. Must be positive. Must not be set when alias is provided."
  default     = null

  validation {
    condition     = var.ttl == null || var.ttl > 0
    error_message = "ttl must be a positive integer when provided."
  }
}

variable "alias" {
  type = object({
    name                   = string
    zone_id                = string
    evaluate_target_health = bool
  })
  description = "(Optional) Alias target configuration. Mutually exclusive with records and ttl. Use when pointing the record at a CloudFront distribution, ELB, or other AWS alias target. Must not be set when records is provided."
  default     = null
}

# tags, managed_by_tag, and module_tag are accepted for call-site symmetry with other
# primitives. aws_route53_record does not support tags (per decision D3); these inputs
# are declared here for interface consistency and are not applied to the record.
variable "tags" {
  type        = map(string)
  description = "(Optional) Accepted for call-site symmetry with other primitives. aws_route53_record does not support tags (per decision D3); this input is not applied to the record."
  default     = {}
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Accepted for call-site symmetry. Not applied to the record (aws_route53_record does not support tags, per D3)."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Accepted for call-site symmetry. Not applied to the record (aws_route53_record does not support tags, per D3)."
  default     = "route53-record"
}

variable "allow_overwrite" {
  type        = bool
  description = "(Optional) Allow Terraform to overwrite an existing record of the same name+type instead of failing. Set true only for records this unit is authoritative for (e.g. an NS delegation written into a parent zone), so a rebuild can re-point the record to a freshly created child zone idempotently. Default false: never clobber a record the unit does not own."
  default     = false
}
