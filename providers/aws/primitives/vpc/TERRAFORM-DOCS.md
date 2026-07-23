# vpc -- terraform-docs reference

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.15.5 |
| aws | >= 6.49.0 |

## Providers

| Name | Version |
|------|---------|
| aws | >= 6.49.0 |

## Resources

| Name | Type |
|------|------|
| aws_vpc.vpc | resource |
| aws_default_security_group.this | resource |
| aws_vpc_dhcp_options.this | resource |
| aws_vpc_dhcp_options_association.this | resource |
| aws_flow_log.vpc_flow_log | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | (Required) Name for the VPC and associated resources. | string | n/a | yes |
| cidr_block | (Optional) The IPv4 CIDR block for the VPC. | string | null | no |
| instance_tenancy | (Optional) A tenancy option for instances launched into the VPC. | string | "default" | no |
| ipv4_ipam_pool_id | (Optional) The ID of an IPv4 IPAM pool you want to use for allocating this VPC's CIDR. | string | null | no |
| ipv4_netmask_length | (Optional) The netmask length of the IPv4 CIDR you want to allocate to this VPC. Requires specifying a ipv4_ipam_pool_id. | number | null | no |
| ipv6_cidr_block | (Optional) IPv6 CIDR block to request from an IPAM Pool. Can be set explicitly or derived from IPAM using ipv6_netmask_length. | string | null | no |
| ipv6_ipam_pool_id | (Optional) IPAM Pool ID for a IPv6 pool. Conflicts with assign_generated_ipv6_cidr_block. | string | null | no |
| ipv6_netmask_length | (Optional) Netmask length to request from IPAM Pool. Conflicts with ipv6_cidr_block. Valid values are from 44 to 60 in increments of 4. | number | null | no |
| ipv6_cidr_block_network_border_group | (Optional) Restricts advertisement of public addresses to specific Network Border Groups such as LocalZones. | string | null | no |
| enable_dns_support | (Optional) A boolean flag to enable/disable DNS support in the VPC. Defaults to true. | bool | true | no |
| enable_dns_hostnames | (Optional) A boolean flag to enable/disable DNS hostnames in the VPC. Defaults false. | bool | false | no |
| enable_network_address_usage_metrics | (Optional) Indicates whether Network Address Usage metrics are enabled for your VPC. Defaults to false. | bool | false | no |
| assign_generated_ipv6_cidr_block | (Optional) Requests an Amazon-provided IPv6 CIDR block with a /56 prefix length for the VPC. Conflicts with ipv6_ipam_pool_id. | bool | false | no |
| enable_ipam | (Optional) Whether to enable IPAM for this VPC. AWS only supports 1 instance of IPAM per AWS account. | bool | false | no |
| enable_flow_logs | (Optional) Whether to enable VPC Flow Logs. | bool | true | no |
| flow_logs_iam_role_arn | (Optional) The ARN for the IAM role used to post flow logs to a CloudWatch Logs log group. Required if enable_flow_logs is true. | string | null | no |
| flow_logs_destination_arn | (Optional) The ARN of the CloudWatch log group or S3 bucket where VPC Flow Logs will be pushed. Required if enable_flow_logs is true. | string | null | no |
| flow_logs_traffic_type | (Optional) The type of traffic to capture. Valid values: ACCEPT, REJECT, ALL. | string | "ALL" | no |
| flow_logs_name_suffix | (Optional) Suffix for flow logs name. | string | "flow-logs" | no |
| dhcp_options | (Optional) DHCP options configuration. When null, uses AWS default DHCP options. | object | null | no |
| default_security_group_ingress | (Optional) List of ingress rules for the default security group. | list(any) | [] | no |
| default_security_group_egress | (Optional) List of egress rules for the default security group. | list(any) | [] | no |
| default_domain_name_servers | (Optional) Default domain name servers for DHCP options when not specified. | list(string) | ["AmazonProvidedDNS"] | no |
| tags | (Optional) A map of tags to assign to the resource. | map(string) | {} | no |
| managed_by_tag | (Optional) Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | (Optional) Value for the Module tag. | string | "vpc" | no |

## Outputs

| Name | Description |
|------|-------------|
| vpc_arn | The ARN of the VPC |
| vpc_id | The ID of the VPC |
| vpc_cidr_block | The CIDR block of the VPC |
| vpc_ipv6_cidr_block | The IPv6 CIDR block of the VPC |
| vpc_instance_tenancy | Tenancy of instances spin up within VPC |
| vpc_enable_dns_support | Whether or not the VPC has DNS support |
| vpc_enable_dns_hostnames | Whether or not the VPC has DNS hostname support |
| vpc_main_route_table_id | The ID of the main route table associated with this VPC |
| vpc_default_network_acl_id | The ID of the network ACL created by default on VPC creation |
| vpc_default_security_group_id | The ID of the security group created by default on VPC creation |
| vpc_default_route_table_id | The ID of the route table created by default on VPC creation |
| vpc_owner_id | The ID of the AWS account that owns the VPC |
| vpc_tags_all | A map of tags assigned to the resource, including those inherited from the provider default_tags configuration block |
| flow_log_id | The Flow Log ID |
| flow_log_arn | The ARN of the Flow Log |
