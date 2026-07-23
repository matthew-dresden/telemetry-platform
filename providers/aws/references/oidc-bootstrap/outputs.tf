output "role_arns" {
  description = "Map of role name to IAM role ARN for all OIDC assume-roles created by this reference. Keys match the input roles map keys. Consumed by downstream pipeline configuration (E4-F1-S2-T1, E7-F1-S1-T1)."
  value       = { for role_name, mod in module.oidc_role : role_name => mod.role_arn }
}
