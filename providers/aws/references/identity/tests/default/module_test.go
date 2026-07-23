//go:build terratest

package default_test

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
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

// TestIdentityDefault is the parent test that applies the default example once,
// runs all happy-path subtests against the live state, then destroys via t.Cleanup
// when the parent test (and all its subtests) complete.
//
// Using the parent-test pattern ensures t.Cleanup(terraform.Destroy) -- registered
// by testctx.RunSingleExample on the parent t -- fires only after ALL subtests
// complete. The previous sync.Once pattern registered cleanup on the first individual
// test's t, causing destroy to fire between test functions and leaving subsequent
// tests operating on empty state.
func TestIdentityDefault(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("identity-default-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// AnalystRoleArnMatchesPattern asserts analyst_role_arn matches the IAM role
	// ARN pattern (AC-13).
	t.Run("AnalystRoleArnMatchesPattern", func(t *testing.T) {
		analystArn := terraform.Output(t, ctx.Terraform, "analyst_role_arn")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:iam::[0-9]{12}:role/`),
			analystArn,
			"analyst_role_arn must match ^arn:aws:iam::[0-9]{12}:role/",
		)
	})

	// AdminRoleArnMatchesPattern asserts admin_role_arn matches the IAM role
	// ARN pattern (AC-13).
	t.Run("AdminRoleArnMatchesPattern", func(t *testing.T) {
		adminArn := terraform.Output(t, ctx.Terraform, "admin_role_arn")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:iam::[0-9]{12}:role/`),
			adminArn,
			"admin_role_arn must match ^arn:aws:iam::[0-9]{12}:role/",
		)
	})

	// GroupRoleMapHasThreeKeys asserts group_role_map contains the viewer, author,
	// and admin keys (AC-13, D8).
	t.Run("GroupRoleMapHasThreeKeys", func(t *testing.T) {
		groupRoleMapRaw := terraform.OutputJson(t, ctx.Terraform, "group_role_map")
		require.NotEmpty(t, groupRoleMapRaw, "group_role_map must not be empty")

		var groupRoleMap map[string]string
		require.NoError(t, json.Unmarshal([]byte(groupRoleMapRaw), &groupRoleMap),
			"group_role_map must be valid JSON")

		assert.Contains(t, groupRoleMap, "viewer",
			"group_role_map must contain 'viewer' key (D8)")
		assert.Contains(t, groupRoleMap, "author",
			"group_role_map must contain 'author' key (D8)")
		assert.Contains(t, groupRoleMap, "admin",
			"group_role_map must contain 'admin' key (D8)")

		assert.NotEmpty(t, groupRoleMap["viewer"],
			"group_role_map.viewer must not be empty")
		assert.NotEmpty(t, groupRoleMap["author"],
			"group_role_map.author must not be empty")
		assert.NotEmpty(t, groupRoleMap["admin"],
			"group_role_map.admin must not be empty")
	})

	// IAMRoleResourceCount asserts at least 2 aws_iam_role resources exist in
	// state for the default example (analyst + admin) (AC-13).
	t.Run("IAMRoleResourceCount", func(t *testing.T) {
		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")
		require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty output")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
			"terraform show -json output must be valid JSON")

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok, "state JSON must contain a 'values' object")

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok, "state values must contain a 'root_module' object")

		roles := findIAMRoles(t, rootModule)
		assert.GreaterOrEqual(t, len(roles), 2,
			"default example must create at least 2 aws_iam_role resources (analyst + admin)")
	})

	// RequiredOutputsPresent asserts all required re-exported outputs are present
	// and non-empty for the default example (AC-3).
	t.Run("RequiredOutputsPresent", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "analyst_role_arn"),
			"analyst_role_arn must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "admin_role_arn"),
			"admin_role_arn must not be empty")
	})

	// GroupRoleMapAuthorArnMatchesAnalystArn asserts the author key in
	// group_role_map maps to the analyst_role_arn (D8).
	t.Run("GroupRoleMapAuthorArnMatchesAnalystArn", func(t *testing.T) {
		analystArn := terraform.Output(t, ctx.Terraform, "analyst_role_arn")
		require.NotEmpty(t, analystArn, "analyst_role_arn must be non-empty")

		groupRoleMapRaw := terraform.OutputJson(t, ctx.Terraform, "group_role_map")
		var groupRoleMap map[string]string
		require.NoError(t, json.Unmarshal([]byte(groupRoleMapRaw), &groupRoleMap))

		assert.Equal(t, analystArn, groupRoleMap["author"],
			"group_role_map.author must equal analyst_role_arn (D8)")
	})

	// GroupRoleMapAdminArnMatchesAdminArn asserts the admin key in group_role_map
	// maps to the admin_role_arn (D8).
	t.Run("GroupRoleMapAdminArnMatchesAdminArn", func(t *testing.T) {
		adminArn := terraform.Output(t, ctx.Terraform, "admin_role_arn")
		require.NotEmpty(t, adminArn, "admin_role_arn must be non-empty")

		groupRoleMapRaw := terraform.OutputJson(t, ctx.Terraform, "group_role_map")
		var groupRoleMap map[string]string
		require.NoError(t, json.Unmarshal([]byte(groupRoleMapRaw), &groupRoleMap))

		assert.Equal(t, adminArn, groupRoleMap["admin"],
			"group_role_map.admin must equal admin_role_arn (D8)")
	})

	// TerraformVersion asserts the pinned Terraform version is satisfied (AC-1).
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// Idempotency asserts a plan after apply exits with code 0 (AC-1).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan must show zero resource changes (idempotency requirement)")
	})
}

// TestIdentityDefaultEmptyViewerGroupNameFailsFast asserts that an empty
// viewer_group_name is rejected before any role is created (AC-13 -- fail-fast
// on input contract). This test is independent of the shared Terraform context
// since it performs a plan-only validation with a controlled failing input.
func TestIdentityDefaultEmptyViewerGroupNameFailsFast(t *testing.T) {
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: "identity-empty-viewer-group",
		ExtraVars: map[string]interface{}{
			"viewer_group_name": "",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err,
		"plan must fail when viewer_group_name is empty (fail-fast on input contract)")
}

// TestIdentityDefaultEmptyAuthorGroupNameFailsFast asserts that an empty
// author_group_name is rejected before any role is created (AC-13 -- fail-fast
// on input contract).
func TestIdentityDefaultEmptyAuthorGroupNameFailsFast(t *testing.T) {
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: "identity-empty-author-group",
		ExtraVars: map[string]interface{}{
			"author_group_name": "",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err,
		"plan must fail when author_group_name is empty (fail-fast on input contract)")
}

// TestIdentityDefaultEmptyAdminGroupNameFailsFast asserts that an empty
// admin_group_name is rejected before any role is created (AC-13 -- fail-fast
// on input contract).
func TestIdentityDefaultEmptyAdminGroupNameFailsFast(t *testing.T) {
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: "identity-empty-admin-group",
		ExtraVars: map[string]interface{}{
			"admin_group_name": "",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err,
		"plan must fail when admin_group_name is empty (fail-fast on input contract)")
}

// ---------------------------------------------------------------------------
// State-walking helpers
// ---------------------------------------------------------------------------

// findIAMRoles walks the Terraform state module tree and returns all
// aws_iam_role resource maps.
func findIAMRoles(t *testing.T, module map[string]interface{}) []map[string]interface{} {
	t.Helper()
	var result []map[string]interface{}

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType == "aws_iam_role" {
				result = append(result, res)
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			result = append(result, findIAMRoles(t, child)...)
		}
	}

	return result
}
