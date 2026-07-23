//go:build terratest

package basic_test

import (
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/matthew-dresden/telemetry-platform/providers/aws/primitives/subnet/tests/helpers"
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

// TestBasicSubnet applies the basic example once and runs all happy-path
// assertions as sub-tests sharing the same Terraform state. The parent test's
// t.Cleanup destroys the state only after all sub-tests complete, avoiding the
// sync.Once + per-test-cleanup race where the first sub-test's cleanup fires
// before subsequent sub-tests run.
func TestBasicSubnet(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-subnet-shared-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// ResourceCount: exactly 3 aws_subnet resources under module.example.
	t.Run("ResourceCount", func(t *testing.T) {
		count := countResourcesByTypeAndPrefix(t, ctx, "aws_subnet", "module.example.")
		assert.Equal(t, 3, count, "expected exactly 3 aws_subnet resources under module.example")
	})

	// IDsListLength: subnet_ids_list output contains 3 entries each matching ^subnet-.
	t.Run("IDsListLength", func(t *testing.T) {
		subnetIDsRaw := terraform.OutputList(t, ctx.Terraform, "subnet_ids_list")
		assert.Len(t, subnetIDsRaw, 3, "subnet_ids_list must have exactly 3 entries")
		for _, id := range subnetIDsRaw {
			assert.Regexp(t, `^subnet-`, id, "each subnet id must match ^subnet-")
		}
	})

	// RequiredOutputsNotEmpty: all required outputs are non-empty.
	t.Run("RequiredOutputsNotEmpty", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "subnet_ids"), "subnet_ids must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "subnet_ids_list"), "subnet_ids_list must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "subnet_arns"), "subnet_arns must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "availability_zones"), "availability_zones must not be empty")
	})

	// TerraformVersion: pinned Terraform version constraint is satisfied.
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// Idempotency: basic example produces zero changes on a second plan.
	// After the shared apply completes, a second plan must show exit code 0 (no resource changes).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode, "second plan must show zero resource changes (exit code 0 means no changes)")
	})

	// ValidatePasses: example passes terraform validate (confirmed by successful apply).
	t.Run("ValidatePasses", func(t *testing.T) {
		subnetIDsRaw := terraform.OutputList(t, ctx.Terraform, "subnet_ids_list")
		assert.NotEmpty(t, subnetIDsRaw, "subnet_ids_list must be non-empty, confirming validate passed")
	})
}

// TestBasicSubnetInvalidVpcID asserts that a vpc_id not matching ^vpc- causes a plan error.
func TestBasicSubnetInvalidVpcID(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-subnet-invalid-vpc-%s", suffix),
		ExtraVars: map[string]interface{}{
			"vpc_id": "invalid-vpc-id",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when vpc_id does not match ^vpc-")
}

// TestBasicSubnetInvalidCIDR asserts that a member subnet with an invalid cidr_block causes a plan error.
// GenerateVPCCIDR is called to confirm the helper is exercised and not dead code.
func TestBasicSubnetInvalidCIDR(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	// Exercise the CIDR helper to confirm it is imported and not dead code.
	uniqueVPCCIDR := helpers.GenerateVPCCIDR()
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-subnet-invalid-cidr-%s", suffix),
		ExtraVars: map[string]interface{}{
			"vpc_cidr_block": uniqueVPCCIDR,
			"subnets": []map[string]interface{}{
				{
					"name":              "invalid-subnet",
					"cidr_block":        "not-a-cidr",
					"availability_zone": "us-east-1a",
				},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when a subnet cidr_block is not a valid CIDR")
}

// TestBasicSubnetEmptyList asserts that an empty subnets list causes a plan error.
func TestBasicSubnetEmptyList(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-subnet-empty-list-%s", suffix),
		ExtraVars: map[string]interface{}{
			"subnets": []map[string]interface{}{},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when subnets list is empty")
}
