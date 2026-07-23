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

// countResourcesByTypeAndPrefix counts resources of the given resourceType whose
// Terraform state address contains modulePrefix (e.g. "module.example.").
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

// TestBasicRouteTable applies the basic example once and runs all happy-path
// assertions as sub-tests sharing the same Terraform state. The parent test's
// t.Cleanup destroys the state only after all sub-tests complete, avoiding the
// sync.Once + per-test-cleanup race where the first sub-test's cleanup fires
// before subsequent sub-tests run.
func TestBasicRouteTable(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-rt-shared-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// ResourceCount: exactly 1 aws_route_table under module.example.
	t.Run("ResourceCount", func(t *testing.T) {
		count := countResourcesByTypeAndPrefix(t, ctx, "aws_route_table", "module.example.")
		assert.Equal(t, 1, count, "expected exactly 1 aws_route_table resource under module.example")
	})

	// MinimumRouteCount: at least 1 aws_route resource exists in state.
	t.Run("MinimumRouteCount", func(t *testing.T) {
		output, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
		assert.NoError(t, err, "terraform state list must not fail")

		routeCount := 0
		for _, line := range strings.Split(output, "\n") {
			line = strings.TrimSpace(line)
			if len(line) > 0 && strings.Contains(line, "aws_route.") {
				routeCount++
			}
		}
		assert.GreaterOrEqual(t, routeCount, 1, "at least 1 aws_route must exist in state")
	})

	// AssociationCount: exactly 2 aws_route_table_association resources under module.example.
	t.Run("AssociationCount", func(t *testing.T) {
		count := countResourcesByTypeAndPrefix(t, ctx, "aws_route_table_association", "module.example.")
		assert.Equal(t, 2, count, "expected exactly 2 aws_route_table_association resources under module.example")
	})

	// IDFormat: route_table_ids_list output contains an ID matching ^rtb-.
	t.Run("IDFormat", func(t *testing.T) {
		rtIDsRaw := terraform.OutputList(t, ctx.Terraform, "route_table_ids_list")
		assert.Len(t, rtIDsRaw, 1, "route_table_ids_list must have exactly 1 entry")
		assert.Regexp(t, `^rtb-`, rtIDsRaw[0], "route table id must match ^rtb-")
	})

	// RequiredOutputsNotEmpty: all required outputs are non-empty.
	t.Run("RequiredOutputsNotEmpty", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "route_table_ids"), "route_table_ids must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "route_table_ids_list"), "route_table_ids_list must not be empty")
	})

	// TerraformVersion: pinned Terraform version constraint is satisfied.
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// ValidatePasses: example passes terraform validate (confirmed by successful apply).
	t.Run("ValidatePasses", func(t *testing.T) {
		rtIDsRaw := terraform.OutputList(t, ctx.Terraform, "route_table_ids_list")
		assert.NotEmpty(t, rtIDsRaw, "route_table_ids_list must be non-empty, confirming validate passed")
	})

	// Idempotency: basic example produces zero changes on a second plan.
	// After the shared apply completes, a second plan must show exit code 0 (no resource changes).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode, "second plan must show zero resource changes (exit code 0 means no changes)")
	})
}

// TestBasicRouteTableInvalidVpcID asserts that a vpc_id not matching ^vpc- causes a plan error.
func TestBasicRouteTableInvalidVpcID(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-rt-invalid-vpc-%s", suffix),
		ExtraVars: map[string]interface{}{
			"vpc_id": "invalid-vpc-id",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when vpc_id does not match ^vpc-")
}

// TestBasicRouteTableEmptyList asserts that an empty route_tables list causes a plan error.
func TestBasicRouteTableEmptyList(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-rt-empty-list-%s", suffix),
		ExtraVars: map[string]interface{}{
			"route_tables": []map[string]interface{}{},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when route_tables list is empty")
}

// TestBasicRouteTableInvalidRouteTargetZero asserts that a route with zero targets causes a plan error.
func TestBasicRouteTableInvalidRouteTargetZero(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-rt-no-target-%s", suffix),
		ExtraVars: map[string]interface{}{
			"route_tables": []map[string]interface{}{
				{
					"name":       "invalid-rt",
					"subnet_ids": []string{"subnet-12345678"},
					"routes": []map[string]interface{}{
						{
							"destination_cidr_block": "0.0.0.0/0",
						},
					},
				},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when a route specifies zero of gateway_id/nat_gateway_id/vpc_endpoint_id")
}

// TestBasicRouteTableInvalidRouteTargetMultiple asserts that a route with more than one target causes a plan error.
func TestBasicRouteTableInvalidRouteTargetMultiple(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-rt-multi-target-%s", suffix),
		ExtraVars: map[string]interface{}{
			"route_tables": []map[string]interface{}{
				{
					"name":       "invalid-rt-multi",
					"subnet_ids": []string{"subnet-12345678"},
					"routes": []map[string]interface{}{
						{
							"destination_cidr_block": "0.0.0.0/0",
							"gateway_id":             "igw-12345678",
							"nat_gateway_id":         "nat-12345678",
						},
					},
				},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when a route specifies more than one of gateway_id/nat_gateway_id/vpc_endpoint_id")
}
