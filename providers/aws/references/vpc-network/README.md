# vpc-network

Composes the full ingestion network plane: the reused `vpc` shell (terraform-modules v1.0.0) plus this
repo's `subnet`, `nat-gateway`, `route-table`, and `vpc-endpoint` primitives. This is a NEW-LOCAL
reference module (decision D6, section 5.6) that the `collector-ingestion` terragrunt unit instantiates
for networking. Its outputs (subnet ids) feed `alb`/`ecs-service` consumers.

The reference root declares no `resource` blocks. All infrastructure is delegated to the composed modules.

## Composed modules

- `vpc` (terraform-modules v1.0.0) -- VPC shell: `aws_vpc`, default SG, DHCP options, flow logs
- `subnet` (telemetry-platform v1.0.0) x2 -- public and private subnets via `aws_subnet`
- `nat-gateway` (telemetry-platform v1.0.0) -- NAT gateways via `aws_nat_gateway` + `aws_eip`
- `route-table` (telemetry-platform v1.0.0) x2 -- public and private route tables via `aws_route_table`
- `vpc-endpoint` (telemetry-platform v1.0.0) -- Interface and Gateway VPC endpoints via `aws_vpc_endpoint`

## Child module source variables (const=true convention)

Each in-repo child module source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo local relative path without network access. Callers do not override these.

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `public_subnets_source` | `../../primitives/subnet` | Source for the public subnet primitive module |
| `private_subnets_source` | `../../primitives/subnet` | Source for the private subnet primitive module |
| `nat_gateways_source` | `../../primitives/nat-gateway` | Source for the nat-gateway primitive module |
| `public_route_tables_source` | `../../primitives/route-table` | Source for the public route-table primitive module |
| `private_route_tables_source` | `../../primitives/route-table` | Source for the private route-table primitive module |
| `vpc_endpoints_source` | `../../primitives/vpc-endpoint` | Source for the vpc-endpoint primitive module |

The external `module "vpc"` (matthew-dresden/terraform-modules vpc v1.0.0) is NOT variable-ized per
the external-literal carve-out (spec section 4.4, AC-3). Its source remains a pinned git URL at all
times and is never affected by `use_pinned_module_sources`.

By default (when `use_pinned_module_sources = false` in `account.hcl`), each in-repo source resolves
to the relative path above. When `use_pinned_module_sources = true` (prod), the terragrunt leaf sets
every `*_source` input to the pinned `git::https://github.com/matthew-dresden/telemetry-platform.git//<path>?ref=<path>/v<semver>`
URL. The toggle flows from `account.hcl` through `_envcommon/collector-ingestion.hcl` (or the service
that instantiates this reference) to the leaf. See `docs/terraform-module-sourcing.md` for the
end-to-end workflow.

## Usage

When `use_pinned_module_sources = true` (prod environments), the terragrunt leaf sources this reference
via a pinned `git::...?ref=providers/aws/references/vpc-network/v<semver>` URL. When false (dev/sandbox),
the leaf uses `${get_repo_root()}//providers/aws/references/vpc-network` so local relative defaults apply.

```hcl
# Pinned usage (use_pinned_module_sources = true, prod):
module "vpc_network" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/references/vpc-network?ref=providers/aws/references/vpc-network/v1.0.0"

  vpc_name       = "telemetry-collector"
  vpc_cidr_block = "10.0.0.0/16"

  public_subnets = [
    { name = "public-a", cidr_block = "10.0.0.0/24", availability_zone = "us-east-1a" },
    { name = "public-b", cidr_block = "10.0.1.0/24", availability_zone = "us-east-1b" },
    { name = "public-c", cidr_block = "10.0.2.0/24", availability_zone = "us-east-1c" },
  ]

  private_subnets = [
    { name = "private-a", cidr_block = "10.0.10.0/24", availability_zone = "us-east-1a" },
    { name = "private-b", cidr_block = "10.0.11.0/24", availability_zone = "us-east-1b" },
    { name = "private-c", cidr_block = "10.0.12.0/24", availability_zone = "us-east-1c" },
  ]

  nat_gateways = [
    { name = "nat-a", public_subnet_name = "public-a", connectivity_type = "public" },
  ]

  interface_endpoint_service_names = [
    "com.amazonaws.us-east-1.ssm",
    "com.amazonaws.us-east-1.ecr.api",
    "com.amazonaws.us-east-1.logs",
  ]

  interface_endpoint_subnet_names = ["private-a"]

  s3_gateway_endpoint_service_name = "com.amazonaws.us-east-1.s3"

  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/default` -- VPC + 3 public + 3 private subnets across 3 AZs + 1 NAT + public/private
  route tables + SSM Interface endpoint + S3 Gateway endpoint + VPC Flow Logs (CloudWatch, KMS-encrypted)

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.12.1 |
| aws | ~> 6.0.0 |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| vpc_name | Name for the VPC and associated resources. | string | n/a | yes |
| vpc_cidr_block | The IPv4 CIDR block for the VPC. | string | n/a | yes |
| enable_dns_hostnames | Enable DNS hostnames in the VPC. | bool | true | no |
| enable_flow_logs | Whether to enable VPC Flow Logs. | bool | false | no |
| flow_logs_iam_role_arn | ARN for the IAM role used for VPC Flow Logs. Required if enable_flow_logs is true. | string | null | no |
| flow_logs_destination_arn | ARN of the CloudWatch log group or S3 bucket for VPC Flow Logs. Required if enable_flow_logs is true. | string | null | no |
| public_subnets | List of public subnet definitions (name, cidr_block, availability_zone). | list(object) | n/a | yes |
| private_subnets | List of private subnet definitions (name, cidr_block, availability_zone). | list(object) | n/a | yes |
| nat_gateways | List of NAT gateway definitions (name, public_subnet_name, connectivity_type). | list(object) | n/a | yes |
| interface_endpoint_service_names | List of AWS service names for Interface-type VPC endpoints. | list(string) | n/a | yes |
| interface_endpoint_subnet_names | List of private subnet names to attach to Interface endpoints. | list(string) | n/a | yes |
| s3_gateway_endpoint_service_name | The AWS service name for the S3 Gateway endpoint. | string | n/a | yes |
| tags | Additional tags applied to all resources. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag. | string | "vpc-network" | no |

## Outputs

| Name | Description |
|------|-------------|
| vpc_id | The ID of the VPC. |
| internet_gateway_id | The ID of the Internet Gateway created and attached to the VPC (public route gateway, docs/terragrunt-concepts.md). |
| public_subnet_ids | Ordered list of public subnet IDs. |
| private_subnet_ids | Ordered list of private subnet IDs. |
| nat_gateway_ids | Ordered list of NAT gateway IDs. |
| route_table_ids | Ordered list of private route table IDs. |
| interface_endpoint_ids | Ordered list of Interface-type VPC endpoint IDs. |
| s3_gateway_endpoint_id | The ID of the S3 Gateway VPC endpoint. |

## Input validation

- `vpc_cidr_block` must be a valid CIDR block validated by `can(cidrhost(...))`.
- `public_subnets` must be non-empty with valid CIDR blocks.
- `private_subnets` must be non-empty with valid CIDR blocks.
- `nat_gateways` must be non-empty; each `connectivity_type` must be `public` or `private`.
- `interface_endpoint_service_names` must be non-empty.
- `interface_endpoint_subnet_names` must be non-empty.

## Policies

- `source_policy.rego` -- in-repo child sources are `source = var.<name>_source` (var-driven, const=true); the external `vpc` (terraform-modules) source remains a pinned literal; prod-context pin gate enforces that in-repo sources resolve to pinned git URLs when `use_pinned_module_sources = true`
- The reference composes the REUSED `vpc` shell plus the NEW-LOCAL networking primitives (subnets/NAT/route-tables/endpoints) and owns the `aws_internet_gateway` for the VPC it builds (docs/terragrunt-concepts.md). The IGW has no upstream primitive and is the public route gateway, so it is created here and exposed via the `internet_gateway_id` output rather than required as an input.
