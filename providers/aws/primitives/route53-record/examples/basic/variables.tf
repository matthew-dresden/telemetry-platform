variable "zone_id" {
  type        = string
  description = "ID of the hosted zone in which to create the record."
}

variable "name" {
  type        = string
  description = "DNS name of the record."
}

variable "type" {
  type        = string
  description = "DNS record type. Valid values: A, AAAA, CAA, CNAME, DS, MX, NAPTR, NS, PTR, SOA, SPF, SRV, TXT."
  default     = "CNAME"
}

variable "records" {
  type        = list(string)
  description = "List of record values."
}

variable "ttl" {
  type        = number
  description = "Time to live for the DNS record in seconds."
  default     = 60
}

variable "tags" {
  type        = map(string)
  description = "Accepted for call-site symmetry. Not applied to the record (aws_route53_record does not support tags, per D3)."
  default     = {}
}

variable "project_tag" {
  type        = string
  description = "Project tag value applied to all resources via the provider default_tags block."
}

variable "terratest_run_id" {
  type        = string
  description = "Terratest run identifier applied to all resources via the provider default_tags block."
}
