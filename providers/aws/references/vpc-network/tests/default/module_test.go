//go:build terratest

package default_test

import (
	"fmt"
	"os"
	"regexp"
	"testing"
	"time"

	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/stretchr/testify/assert"
)

// mustTaggingVars reads PROJECT_TAG and TERRATEST_RUN_ID from the environment.
// It calls t.Fatal with an actionable error message when either variable is unset.
// ExtraVars override committed tfvars values (spec section 4.3, D-12).
func mustTaggingVars(t *testing.T) map[string]interface{} {
	t.Helper()
	runID := os.Getenv("TERRATEST_RUN_ID")
	if runID == "" {
		t.Fatal("ERROR: TERRATEST_RUN_ID is not set; run via make tf-test")
	}
	projectTag := os.Getenv("PROJECT_TAG")
	if projectTag == "" {
		t.Fatal("ERROR: PROJECT_TAG is not set; run via make tf-test")
	}
	return map[string]interface{}{
		"project_tag":      projectTag,
		"terratest_run_id": runID,
	}
}

// mergeExtraVars merges tagging variables into an existing ExtraVars map.
// It calls t.Fatal when a key collision is detected to prevent silent overwrites.
func mergeExtraVars(t *testing.T, base map[string]interface{}, tagging map[string]interface{}) map[string]interface{} {
	t.Helper()
	result := make(map[string]interface{}, len(base)+len(tagging))
	for k, v := range base {
		result[k] = v
	}
	for k, v := range tagging {
		if _, exists := result[k]; exists {
			t.Fatalf("ExtraVars collision: key %q is defined both in the test and in tagging vars", k)
		}
		result[k] = v
	}
	return result
}

// TestVpcNetworkDefault applies the default example once and runs all happy-path
// assertions as sub-tests sharing the same Terraform state. The parent test's
// t.Cleanup destroys the state only after all sub-tests complete, avoiding the
// sync.Once + per-test-cleanup race where the first sub-test's cleanup fires
// before subsequent sub-tests run.
func TestVpcNetworkDefault(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	name := fmt.Sprintf("vpc-network-default-%s", suffix[:8])
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      name,
		ExtraVars: mergeExtraVars(t, map[string]interface{}{"name": name}, mustTaggingVars(t)),
	})

	t.Run("VpcIDOutput", func(t *testing.T) {
		vpcID := terraform.Output(t, ctx.Terraform, "vpc_id")
		assert.Regexp(t, regexp.MustCompile(`^vpc-`), vpcID, "vpc_id must match ^vpc-")
	})

	t.Run("InternetGatewayIDOutput", func(t *testing.T) {
		// The IGW is created INSIDE the module (docs/terragrunt-concepts.md) and exposed as an
		// output; it is no longer a required input. A valid IGW id is returned.
		igwID := terraform.Output(t, ctx.Terraform, "internet_gateway_id")
		assert.Regexp(t, regexp.MustCompile(`^igw-`), igwID, "internet_gateway_id must match ^igw-")
	})

	t.Run("InternetGatewayResourceCount", func(t *testing.T) {
		// Exactly one IGW is created by the module for the VPC it builds.
		assertions.AssertResourceCount(t, ctx, "aws_internet_gateway", 1)
	})

	t.Run("PublicSubnetIDs", func(t *testing.T) {
		publicSubnetIDs := terraform.OutputList(t, ctx.Terraform, "public_subnet_ids")
		assert.NotEmpty(t, publicSubnetIDs, "public_subnet_ids must be non-empty")
		for _, id := range publicSubnetIDs {
			assert.Regexp(t, regexp.MustCompile(`^subnet-`), id, "each public subnet id must match ^subnet-")
		}
	})

	t.Run("PrivateSubnetIDs", func(t *testing.T) {
		privateSubnetIDs := terraform.OutputList(t, ctx.Terraform, "private_subnet_ids")
		assert.NotEmpty(t, privateSubnetIDs, "private_subnet_ids must be non-empty")
		for _, id := range privateSubnetIDs {
			assert.Regexp(t, regexp.MustCompile(`^subnet-`), id, "each private subnet id must match ^subnet-")
		}
	})

	t.Run("NatGatewayIDs", func(t *testing.T) {
		natGatewayIDs := terraform.OutputList(t, ctx.Terraform, "nat_gateway_ids")
		assert.NotEmpty(t, natGatewayIDs, "nat_gateway_ids must be non-empty")
		for _, id := range natGatewayIDs {
			assert.Regexp(t, regexp.MustCompile(`^nat-`), id, "each nat gateway id must match ^nat-")
		}
	})

	t.Run("RouteTableIDs", func(t *testing.T) {
		routeTableIDs := terraform.OutputList(t, ctx.Terraform, "route_table_ids")
		assert.NotEmpty(t, routeTableIDs, "route_table_ids must be non-empty")
		for _, id := range routeTableIDs {
			assert.Regexp(t, regexp.MustCompile(`^rtb-`), id, "each route table id must match ^rtb-")
		}
	})

	t.Run("InterfaceEndpointIDs", func(t *testing.T) {
		interfaceEndpointIDs := terraform.OutputList(t, ctx.Terraform, "interface_endpoint_ids")
		assert.NotEmpty(t, interfaceEndpointIDs, "interface_endpoint_ids must be non-empty")
		for _, id := range interfaceEndpointIDs {
			assert.Regexp(t, regexp.MustCompile(`^vpce-`), id, "each interface endpoint id must match ^vpce-")
		}
	})

	t.Run("S3GatewayEndpointID", func(t *testing.T) {
		s3EndpointID := terraform.Output(t, ctx.Terraform, "s3_gateway_endpoint_id")
		assert.Regexp(t, regexp.MustCompile(`^vpce-`), s3EndpointID, "s3_gateway_endpoint_id must match ^vpce-")
	})

	t.Run("NatGatewayResourceCount", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_nat_gateway", 1)
	})

	t.Run("VpcEndpointResourceCount", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_vpc_endpoint", 2)
	})

	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	t.Run("ValidateAndFmt", func(t *testing.T) {
		vpcID := terraform.Output(t, ctx.Terraform, "vpc_id")
		assert.NotEmpty(t, vpcID, "vpc_id must be non-empty, confirming validate + apply passed")
	})

	t.Run("RequiredOutputsNotEmpty", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "vpc_id"), "vpc_id must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "internet_gateway_id"), "internet_gateway_id must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "public_subnet_ids"), "public_subnet_ids must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "private_subnet_ids"), "private_subnet_ids must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "nat_gateway_ids"), "nat_gateway_ids must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "route_table_ids"), "route_table_ids must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "interface_endpoint_ids"), "interface_endpoint_ids must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "s3_gateway_endpoint_id"), "s3_gateway_endpoint_id must not be empty")
	})

	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode, "second plan must show zero resource changes (exit code 0 means no changes)")
	})
}

// TestVpcNetworkInvalidVpcCIDR asserts that an invalid vpc_cidr_block causes a plan error.
func TestVpcNetworkInvalidVpcCIDR(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	name := fmt.Sprintf("vpc-network-invalid-cidr-%s", suffix[:8])
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: name,
		ExtraVars: map[string]interface{}{
			"name":           name,
			"vpc_cidr_block": "not-a-cidr",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when vpc_cidr_block is not a valid CIDR")
}
