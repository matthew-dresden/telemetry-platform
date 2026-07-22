//go:build terratest

package with_alb_autoscaling_test

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


// Note: required outputs, idempotency, and Terraform version assertions for this example
// are covered by tests/common/module_test.go to avoid duplication (DRY).

// TestWithALBAutoscalingECSServiceCreation tests that an ECS service is created with the expected
// autoscaling resources and ALB attachment when autoscaling is enabled.
func TestWithALBAutoscalingECSServiceCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-alb-autoscaling", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-svc-alb-create-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("ecs-w-%s", suffix[:8]),
		}, mustTaggingVars(t)),
	})

	taskDefArn := terraform.Output(t, ctx.Terraform, "task_definition_arn")
	assert.Regexp(t, `^arn:aws:ecs:.*:task-definition/`, taskDefArn,
		"task_definition_arn must match ECS task definition ARN pattern")

	autoscalingTargetResourceId := terraform.Output(t, ctx.Terraform, "autoscaling_target_resource_id")
	assert.NotEmpty(t, autoscalingTargetResourceId, "autoscaling_target_resource_id must not be empty when autoscaling is enabled")
	assert.Regexp(t, `^service/`, autoscalingTargetResourceId,
		"autoscaling_target_resource_id must be in the form service/<cluster>/<service>")
}

// TestWithALBAutoscalingResourceCounts asserts one aws_appautoscaling_target and at least one
// aws_appautoscaling_policy exist when autoscaling is enabled.
func TestWithALBAutoscalingResourceCounts(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-alb-autoscaling", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-svc-alb-counts-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("ecs-r-%s", suffix[:8]),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_ecs_service", 1)
	assertions.AssertResourceCount(t, ctx, "aws_ecs_task_definition", 1)
	assertions.AssertResourceCount(t, ctx, "aws_appautoscaling_target", 1)
	// Two target-tracking policies: the CPU policy (always created when autoscaling is
	// enabled) plus the ALBRequestCountPerTarget policy (the example sets
	// request_count_target + alb_resource_label, so the load-proportional policy is added).
	assertions.AssertResourceCount(t, ctx, "aws_appautoscaling_policy", 2)
}

