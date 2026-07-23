//go:build terratest

package common_test

import (
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/assertions"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
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

// TestCommonIdentityDefault is the parent test that applies the default example once,
// runs all subtests against the live state, then destroys via t.Cleanup when
// the parent test (and all its subtests) complete.
//
// Using the parent-test pattern ensures t.Cleanup(terraform.Destroy) -- registered
// by testctx.RunSingleExample on the parent t -- fires only after ALL subtests
// complete. The previous sync.Once pattern registered cleanup on the first individual
// test's t, causing destroy to fire between test functions and leaving subsequent
// tests operating on empty state.
func TestCommonIdentityDefault(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("identity-common-default-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// TerraformVersion asserts the pinned Terraform version is satisfied for
	// the default example (AC-1).
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// RequiredOutputs asserts all required outputs are present and non-empty
	// for the default example (AC-3).
	t.Run("RequiredOutputs", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "analyst_role_arn"),
			"analyst_role_arn must not be empty (default example)")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "admin_role_arn"),
			"admin_role_arn must not be empty (default example)")

		groupRoleMapRaw := terraform.OutputJson(t, ctx.Terraform, "group_role_map")
		assert.NotEmpty(t, groupRoleMapRaw,
			"group_role_map must not be empty (default example)")

		var groupRoleMap map[string]string
		require.NoError(t, json.Unmarshal([]byte(groupRoleMapRaw), &groupRoleMap),
			"group_role_map must be valid JSON")
		assert.Contains(t, groupRoleMap, "viewer", "group_role_map must contain 'viewer' key")
		assert.Contains(t, groupRoleMap, "author", "group_role_map must contain 'author' key")
		assert.Contains(t, groupRoleMap, "admin", "group_role_map must contain 'admin' key")
	})

	// Idempotency asserts the default example is idempotent: a plan after apply
	// exits with code 0 (AC-1).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan on default example must show zero resource changes (idempotency)")
	})

	// ValidateOutputsAfterApply asserts the default example passes validate by
	// confirming a real output is non-empty after apply (AC-1).
	t.Run("ValidateOutputsAfterApply", func(t *testing.T) {
		analystArn := terraform.Output(t, ctx.Terraform, "analyst_role_arn")
		assert.NotEmpty(t, analystArn,
			"analyst_role_arn must be non-empty after successful apply, confirming validate passed")
	})
}

// TestCommonIdentityWithEcsRoles is the parent test that applies the with-ecs-roles
// example once, runs all subtests against the live state, then destroys via t.Cleanup
// when the parent test (and all its subtests) complete.
func TestCommonIdentityWithEcsRoles(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-ecs-roles", testctx.TestConfig{
		Name:      fmt.Sprintf("identity-common-ecs-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// TerraformVersion asserts the pinned Terraform version is satisfied for
	// the with-ecs-roles example (AC-1).
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// RequiredOutputs asserts all required outputs are present and non-empty
	// for the with-ecs-roles example (AC-3).
	t.Run("RequiredOutputs", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "analyst_role_arn"),
			"analyst_role_arn must not be empty (with-ecs-roles example)")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "admin_role_arn"),
			"admin_role_arn must not be empty (with-ecs-roles example)")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "ecs_task_role_arn"),
			"ecs_task_role_arn must not be empty (with-ecs-roles example)")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "ecs_task_execution_role_arn"),
			"ecs_task_execution_role_arn must not be empty (with-ecs-roles example)")
		assert.NotEmpty(t, terraform.OutputJson(t, ctx.Terraform, "group_role_map"),
			"group_role_map must not be empty (with-ecs-roles example)")
	})

	// Idempotency asserts the with-ecs-roles example is idempotent: a plan after
	// apply exits with code 0 (AC-1).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan on with-ecs-roles example must show zero resource changes (idempotency)")
	})

	// ValidateOutputsAfterApply asserts the with-ecs-roles example passes validate
	// by confirming a real output is non-empty after apply (AC-1).
	t.Run("ValidateOutputsAfterApply", func(t *testing.T) {
		ecsTaskArn := terraform.Output(t, ctx.Terraform, "ecs_task_role_arn")
		assert.NotEmpty(t, ecsTaskArn,
			"ecs_task_role_arn must be non-empty after successful apply, confirming validate passed")
	})
}
