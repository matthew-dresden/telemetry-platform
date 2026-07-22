//go:build terratest

package basic_test

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


// Note: required outputs, idempotency, and Terraform version assertions for the basic example
// are covered by tests/common/module_test.go to avoid duplication (DRY).

// TestBasicECSServiceCreation tests that an ECS service and task definition are created with expected ARN patterns.
func TestBasicECSServiceCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-svc-basic-create-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("ecs-b-%s", suffix[:8]),
		}, mustTaggingVars(t)),
	})

	taskDefArn := terraform.Output(t, ctx.Terraform, "task_definition_arn")
	assert.Regexp(t, `^arn:aws:ecs:.*:task-definition/`, taskDefArn,
		"task_definition_arn must match ECS task definition ARN pattern")
}

// TestBasicECSServiceResourceCounts asserts exactly one aws_ecs_service, one aws_ecs_task_definition,
// and zero aws_appautoscaling_target (autoscaling disabled in basic example).
func TestBasicECSServiceResourceCounts(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-svc-basic-counts-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("ecs-c-%s", suffix[:8]),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_ecs_service", 1)
	assertions.AssertResourceCount(t, ctx, "aws_ecs_task_definition", 1)
	assertions.AssertResourceCount(t, ctx, "aws_appautoscaling_target", 0)
	assertions.AssertResourceCount(t, ctx, "aws_appautoscaling_policy", 0)
}

// TestBasicECSServiceEmptySubnetIdsValidationError tests that fewer than 2 subnet IDs fail validation.
func TestBasicECSServiceEmptySubnetIdsValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-svc-basic-no-subnets-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":           fmt.Sprintf("ecs-e-%s", suffix[:8]),
			"override_subnets": []string{"subnet-00000001"},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when fewer than 2 subnet IDs are provided")
}

// TestBasicECSServiceInvalidAutoscalingConfigValidationError tests that enable_autoscaling=true
// with a null autoscaling config fails the fail-fast validation.
func TestBasicECSServiceInvalidAutoscalingConfigValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-svc-basic-bad-as-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":              fmt.Sprintf("ecs-a-%s", suffix[:8]),
			"enable_autoscaling": true,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when enable_autoscaling is true but autoscaling config is null")
}
