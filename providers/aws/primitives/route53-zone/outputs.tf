output "zone_id" {
  description = "The hosted zone ID."
  value       = aws_route53_zone.this.zone_id
}

output "name_servers" {
  description = "A list of name servers in associated (or default) delegation set. AWS always returns exactly four entries."
  value       = aws_route53_zone.this.name_servers
}

output "arn" {
  description = "The Amazon Resource Name (ARN) of the hosted zone."
  value       = aws_route53_zone.this.arn
}

output "zone_name" {
  description = "The DNS name of the hosted zone."
  value       = aws_route53_zone.this.name
}
