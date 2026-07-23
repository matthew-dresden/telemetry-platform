# route-table -- tests/basic

Terratest suite for the `examples/basic` fixture: 1 private route table with a default route to a NAT gateway and 2 subnet associations.

## What is tested

- `aws_route_table` resource count equals 1
- `aws_route` count is at least 1 (the default route to the NAT gateway)
- `aws_route_table_association` count equals 2
- `route_table_ids_list` contains exactly 1 entry matching `^rtb-`
- All required outputs are non-empty (`route_table_ids`, `route_table_ids_list`)
- Terraform version satisfies the `>= 1.12.1` constraint (`AssertTerraformVersion 1.15.5`)
- Plan after apply shows no changes (idempotency)
- `terraform validate` passes (confirmed by successful apply)
- Invalid `vpc_id` (not matching `^vpc-`) causes a plan error
- Empty `route_tables` list causes a plan error
- A route specifying zero of `gateway_id`/`nat_gateway_id`/`vpc_endpoint_id` causes a plan error

## Running

From the module root:

```bash
make tf-test
```

Or directly:

```bash
go test -v -timeout 20m ./tests/basic/...
```
