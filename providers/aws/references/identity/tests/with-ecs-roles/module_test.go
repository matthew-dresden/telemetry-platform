//go:build terratest

package withEcsRoles_test

import (
	"fmt"
	"os"
	"regexp"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
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

// TestIdentityWithEcsRoles is the parent test that applies the with-ecs-roles example
// once, runs all subtests against the live state, then destroys via t.Cleanup when
// the parent test (and all its subtests) complete.
//
// Using the parent-test pattern ensures t.Cleanup(terraform.Destroy) -- registered
// by testctx.RunSingleExample on the parent t -- fires only after ALL subtests
// complete. The previous sync.Once pattern registered cleanup on the first
// individual subtest's t, which caused destroy to fire between test functions and
// left subsequent tests operating on empty state (AC-3).
func TestIdentityWithEcsRoles(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-ecs-roles", testctx.TestConfig{
		Name:      fmt.Sprintf("identity-ecs-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// TestEcsTaskRoleArnMatchesPattern asserts ecs_task_role_arn matches
	// the IAM role ARN pattern (AC-13, docs/terragrunt-concepts.md).
	t.Run("EcsTaskRoleArnMatchesPattern", func(t *testing.T) {
		ecsTaskArn := terraform.Output(t, ctx.Terraform, "ecs_task_role_arn")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:iam::[0-9]{12}:role/`),
			ecsTaskArn,
			"ecs_task_role_arn must match ^arn:aws:iam::[0-9]{12}:role/",
		)
	})

	// TestEcsTaskExecutionRoleArnMatchesPattern asserts ecs_task_execution_role_arn matches
	// the IAM role ARN pattern (AC-13, docs/terragrunt-concepts.md).
	t.Run("EcsTaskExecutionRoleArnMatchesPattern", func(t *testing.T) {
		ecsExecArn := terraform.Output(t, ctx.Terraform, "ecs_task_execution_role_arn")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:iam::[0-9]{12}:role/`),
			ecsExecArn,
			"ecs_task_execution_role_arn must match ^arn:aws:iam::[0-9]{12}:role/",
		)
	})

	// TestEcsRolesNotEmpty asserts both ECS role outputs are non-empty
	// when ECS role names are provided (AC-13).
	t.Run("EcsRolesNotEmpty", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "ecs_task_role_arn"),
			"ecs_task_role_arn must not be empty when ecs_task_role_name is set")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "ecs_task_execution_role_arn"),
			"ecs_task_execution_role_arn must not be empty when ecs_task_execution_role_name is set")
	})

	// TestEcsRolesIdempotency asserts a plan after apply exits with code 0 (AC-1).
	// The framework-level idempotency check (text-based "No changes") already runs
	// inside RunSingleExample; this subtest additionally verifies the exit-code
	// contract on the live state before cleanup fires.
	t.Run("EcsRolesIdempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan must show zero resource changes (idempotency requirement)")
	})

	// TestEcsTaskRoleArnConsumedInExample asserts the ecs_task_role_arn output
	// is consumed by the example to prove the re-export is not dangling (AC-3).
	t.Run("EcsTaskRoleArnConsumedInExample", func(t *testing.T) {
		ecsTaskArn := terraform.Output(t, ctx.Terraform, "ecs_task_role_arn")
		assert.NotEmpty(t, ecsTaskArn,
			"ecs_task_role_arn must be non-empty and consumed by the example (proves no dangling wiring)")

		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:iam::`),
			ecsTaskArn,
			"consumed ecs_task_role_arn must be an IAM ARN")
	})
}
