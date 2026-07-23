//go:build terratest

package basic_test

import (
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/assertions"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
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

// countResourcesByTypeAndPrefix counts resources of resourceType whose state address
// contains the given modulePrefix (e.g. "module.example.").
// The basic example instantiates the module under test in a block named "example",
// so its resources appear in state at module.example.<resourceType>.<name>.
func countResourcesByTypeAndPrefix(t *testing.T, ctx testctx.TestContext, resourceType, modulePrefix string) int {
	t.Helper()
	output, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
	assert.NoError(t, err, "terraform state list must not fail")
	count := 0
	needle := modulePrefix + resourceType + "."
	for _, line := range strings.Split(output, "\n") {
		if strings.Contains(line, needle) {
			count++
		}
	}
	return count
}

// TestBasicNatGateway applies the basic example once and runs all happy-path assertions
// as sub-tests sharing the same Terraform state. The parent test's t.Cleanup destroys
// the state only after all sub-tests complete, avoiding the sync.Once + per-test-cleanup
// race where the first sub-test's cleanup fires before subsequent sub-tests run.
func TestBasicNatGateway(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-nat-gw-shared-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// ResourceCount: exactly 1 aws_nat_gateway under module.example.
	t.Run("ResourceCount", func(t *testing.T) {
		count := countResourcesByTypeAndPrefix(t, ctx, "aws_nat_gateway", "module.example.")
		assert.Equal(t, 1, count, "expected exactly 1 aws_nat_gateway resource under module.example")
	})

	// EIPCount: exactly 1 aws_eip under module.example.
	t.Run("EIPCount", func(t *testing.T) {
		count := countResourcesByTypeAndPrefix(t, ctx, "aws_eip", "module.example.")
		assert.Equal(t, 1, count, "expected exactly 1 aws_eip resource under module.example")
	})

	// IDFormat: each nat_gateway_ids value matches ^nat-.
	t.Run("IDFormat", func(t *testing.T) {
		natGWIDs := terraform.OutputMap(t, ctx.Terraform, "nat_gateway_ids")
		assert.NotEmpty(t, natGWIDs, "nat_gateway_ids must not be empty")
		for name, id := range natGWIDs {
			assert.Regexp(t, `^nat-`, id, "nat_gateway_ids[%s] must match ^nat-", name)
		}
	})

	// IDsListLength: nat_gateway_ids_list has exactly 1 entry.
	t.Run("IDsListLength", func(t *testing.T) {
		natGWIDsList := terraform.OutputList(t, ctx.Terraform, "nat_gateway_ids_list")
		assert.Len(t, natGWIDsList, 1, "nat_gateway_ids_list must have exactly 1 entry")
		assert.Regexp(t, `^nat-`, natGWIDsList[0], "nat_gateway_ids_list[0] must match ^nat-")
	})

	// ElasticIPsPopulated: elastic_ips map is non-empty with non-empty values.
	t.Run("ElasticIPsPopulated", func(t *testing.T) {
		elasticIPs := terraform.OutputMap(t, ctx.Terraform, "elastic_ips")
		assert.NotEmpty(t, elasticIPs, "elastic_ips must not be empty")
		for name, ip := range elasticIPs {
			assert.NotEmpty(t, ip, "elastic_ips[%s] must not be empty", name)
		}
	})

	// RequiredOutputsNotEmpty: all required outputs are non-empty.
	t.Run("RequiredOutputsNotEmpty", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "nat_gateway_ids"), "nat_gateway_ids must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "nat_gateway_ids_list"), "nat_gateway_ids_list must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "elastic_ips"), "elastic_ips must not be empty")
	})

	// TerraformVersion: pinned Terraform version constraint is satisfied.
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// ValidatePasses: example passes terraform validate (confirmed by successful apply).
	t.Run("ValidatePasses", func(t *testing.T) {
		natGWIDsList := terraform.OutputList(t, ctx.Terraform, "nat_gateway_ids_list")
		assert.NotEmpty(t, natGWIDsList, "nat_gateway_ids_list must be non-empty, confirming validate passed")
	})

	// Idempotency: basic example produces zero changes on a second plan.
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode, "second plan must show zero resource changes (exit code 0 means no changes)")
	})
}

// TestBasicNatGatewayEmptyList asserts that an empty nat_gateways list causes a plan error.
func TestBasicNatGatewayEmptyList(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-nat-gw-empty-list-%s", suffix),
		ExtraVars: map[string]interface{}{
			"nat_gateways": []map[string]interface{}{},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when nat_gateways list is empty")
}

// TestBasicNatGatewayInvalidSubnetID asserts that a public_subnet_id not matching ^subnet- causes a plan error.
func TestBasicNatGatewayInvalidSubnetID(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-nat-gw-invalid-subnet-%s", suffix),
		ExtraVars: map[string]interface{}{
			"nat_gateways": []map[string]interface{}{
				{
					"name":             "nat-a",
					"public_subnet_id": "invalid-subnet-id",
				},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when public_subnet_id does not match ^subnet-")
}

// TestBasicNatGatewayInvalidConnectivityType asserts that a connectivity_type outside [public,private] causes a plan error.
func TestBasicNatGatewayInvalidConnectivityType(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-nat-gw-invalid-conn-%s", suffix),
		ExtraVars: map[string]interface{}{
			"nat_gateways": []map[string]interface{}{
				{
					"name":              "nat-a",
					"public_subnet_id":  "subnet-12345678",
					"connectivity_type": "invalid",
				},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when connectivity_type is not public or private")
}
