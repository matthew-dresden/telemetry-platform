//go:build terratest

package default_test

import (
	"fmt"
	"os"
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

// TestDefaultALB applies the default example once and runs all happy-path assertions
// as sub-tests sharing the same Terraform state. The parent test's t.Cleanup destroys
// the state only after all sub-tests complete, avoiding the per-test-cleanup race where
// one sub-test's cleanup fires before subsequent sub-tests run.
func TestDefaultALB(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	tagging := mustTaggingVars(t)

	extraVars := map[string]interface{}{
		"name":             fmt.Sprintf("alb-%s", suffix[:8]),
		"project_tag":      tagging["project_tag"],
		"terratest_run_id": tagging["terratest_run_id"],
	}

	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("default-alb-shared-%s", suffix),
		ExtraVars: extraVars,
	})

	// ALBCreation: alb_arn must match the ALB ARN pattern.
	t.Run("ALBCreation", func(t *testing.T) {
		albArn := terraform.Output(t, ctx.Terraform, "alb_arn")
		assert.Regexp(t, `^arn:aws:elasticloadbalancing:`, albArn, "alb_arn must match ALB ARN pattern")
	})

	// DNSName: alb_dns_name must be non-empty.
	t.Run("DNSName", func(t *testing.T) {
		albDnsName := terraform.Output(t, ctx.Terraform, "alb_dns_name")
		assert.NotEmpty(t, albDnsName, "alb_dns_name must not be empty")
	})

	// ResourceCount: exactly 1 aws_lb and 1 aws_lb_target_group; 0 aws_lb_listener.
	// assertions.AssertResourceCount matches "module.example.<type>." which requires the
	// example's module block to be named "example" (examples/default/main.tf).
	t.Run("ResourceCount", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_lb", 1)
		assertions.AssertResourceCount(t, ctx, "aws_lb_target_group", 1)
		// Listeners are NOT in the alb module -- they belong to alb-listener (D6).
		assertions.AssertResourceCount(t, ctx, "aws_lb_listener", 0)
	})

	// RequiredOutputs: all required outputs are present and non-empty.
	t.Run("RequiredOutputs", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "alb_arn"), "alb_arn must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "alb_dns_name"), "alb_dns_name must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "alb_zone_id"), "alb_zone_id must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "target_group_arns"), "target_group_arns must not be empty")
	})

	// TerraformVersion: the pinned version constraint is satisfied.
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// Idempotency: a plan after apply must exit 0 (no changes).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode, "plan after apply must exit 0 (no changes) for the default example to be idempotent")
	})
}

// TestDefaultALBEmptySubnetIdsValidationError tests that an empty subnet_ids list fails validation.
// Uses InitAndPlanE only -- no AWS resources are created.
func TestDefaultALBEmptySubnetIdsValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("default-alb-no-subnets-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":       fmt.Sprintf("alb-e-%s", suffix[:8]),
			"subnet_ids": []string{},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when subnet_ids is empty (requires >= 2)")
}

// TestDefaultALBInvalidNameValidationError tests that a name with invalid characters fails validation.
// Uses InitAndPlanE only -- no AWS resources are created.
func TestDefaultALBInvalidNameValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("default-alb-bad-name-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name": "invalid name with spaces!",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when name contains invalid characters")
}
