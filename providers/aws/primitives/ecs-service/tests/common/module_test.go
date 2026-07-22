//go:build terratest

package common_test

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


// runCommonExampleAssertions runs shared validate/fmt/required-outputs/version/idempotency assertions
// for a given example directory path.
func runCommonExampleAssertions(t *testing.T, examplesRoot string, exampleName string, extraVars map[string]interface{}) {
	t.Helper()
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	name := fmt.Sprintf("ecs-svc-%s-%s", exampleName[:4], suffix[:8])
	vars := map[string]interface{}{
		"name": name,
	}
	for k, v := range extraVars {
		vars[k] = v
	}

	ctx := testctx.RunSingleExample(t, examplesRoot, exampleName, testctx.TestConfig{
		Name:      fmt.Sprintf("ecs-svc-common-%s-%s", exampleName, suffix),
		ExtraVars: mergeExtraVars(t, vars, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "service_id"), "service_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "service_name"), "service_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "task_definition_arn"), "task_definition_arn must not be empty")

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")

	exitCode := terraform.PlanExitCode(t, ctx.Terraform)
	assert.Equal(t, 0, exitCode, "plan after apply must exit 0 (no changes) -- idempotency required")
}

// TestCommonBasicRequiredOutputsAndIdempotency runs shared assertions against the basic example.
func TestCommonBasicRequiredOutputsAndIdempotency(t *testing.T) {
	runCommonExampleAssertions(t, "../../examples", "basic", map[string]interface{}{})
}

// TestCommonWithALBAutoscalingRequiredOutputsAndIdempotency runs shared assertions against the with-alb-autoscaling example.
func TestCommonWithALBAutoscalingRequiredOutputsAndIdempotency(t *testing.T) {
	runCommonExampleAssertions(t, "../../examples", "with-alb-autoscaling", map[string]interface{}{})
}
