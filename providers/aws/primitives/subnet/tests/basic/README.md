# subnet -- tests/basic

Terratest suite for the `examples/basic` fixture: 3 private subnets across 3 availability zones.

## What is tested

- `aws_subnet` resource count equals 3 (via `module.example.` state prefix)
- `subnet_ids_list` length equals 3 with each ID matching `^subnet-`
- All required outputs are non-empty (`subnet_ids`, `subnet_ids_list`, `subnet_arns`, `availability_zones`)
- Terraform version satisfies the `>= 1.12.1` constraint (`AssertTerraformVersion 1.15.5`)
- Plan after apply shows no changes (idempotency)
- `terraform validate` passes (confirmed by successful apply)
- `terraform fmt -check` passes (confirmed by repository CI gate)
- Invalid `vpc_id` (not matching `^vpc-`) causes a plan error
- Invalid `cidr_block` in a subnet entry causes a plan error
- Empty `subnets` list causes a plan error

## Running

From the repo root:

```bash
AWS_PROFILE=sandbox make tf-test MODULE_PATH=providers/aws/primitives/subnet
```
