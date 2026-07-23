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

// TestOidcBootstrapDefault is the parent test that applies the default example once,
// runs all happy-path subtests against the live state, then destroys via t.Cleanup
// when the parent test (and all its subtests) complete.
//
// Using the parent-test pattern ensures t.Cleanup(terraform.Destroy) -- registered
// by testctx.RunSingleExample on the parent t -- fires only after ALL subtests
// complete. The previous sync.Once pattern registered cleanup on the first individual
// test's t, causing destroy to fire between test functions and leaving subsequent
// tests operating on empty state.
func TestOidcBootstrapDefault(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("oidc-bootstrap-default-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// RoleArnsOutputPresent asserts role_arns output is non-empty and contains at
	// least one entry matching the IAM role ARN pattern (AC-14).
	t.Run("RoleArnsOutputPresent", func(t *testing.T) {
		roleArnsJSON := terraform.OutputJson(t, ctx.Terraform, "role_arns")
		require.NotEmpty(t, roleArnsJSON, "role_arns must not be empty")

		var roleArns map[string]string
		require.NoError(t, json.Unmarshal([]byte(roleArnsJSON), &roleArns),
			"role_arns must be valid JSON map")

		assert.GreaterOrEqual(t, len(roleArns), 1,
			"role_arns must contain at least one entry matching the roles input map")

		arnPattern := regexp.MustCompile(`^arn:aws:iam::`)
		for roleName, roleArn := range roleArns {
			assert.Regexp(t, arnPattern, roleArn,
				"role_arns[%q] must match ^arn:aws:iam::", roleName)
		}
	})

	// OneRoleArnPerMapEntry asserts the number of role ARNs in the output matches
	// the number of entries in the roles input map (AC-14).
	t.Run("OneRoleArnPerMapEntry", func(t *testing.T) {
		roleArnsJSON := terraform.OutputJson(t, ctx.Terraform, "role_arns")
		require.NotEmpty(t, roleArnsJSON, "role_arns must not be empty")

		var roleArns map[string]string
		require.NoError(t, json.Unmarshal([]byte(roleArnsJSON), &roleArns),
			"role_arns must be valid JSON map")

		// The default example passes exactly one role in the roles map.
		assert.Equal(t, 1, len(roleArns),
			"role_arns must have exactly one entry for the single-role default example")
	})

	// NoOidcProviderCreated asserts no aws_iam_openid_connect_provider resource is
	// created (D40 -- provider is an operator prerequisite, never created here).
	t.Run("NoOidcProviderCreated", func(t *testing.T) {
		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")
		require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty output")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
			"terraform show -json output must be valid JSON")

		providers := findResourcesByType(t, state, "aws_iam_openid_connect_provider")
		assert.Empty(t, providers,
			"no aws_iam_openid_connect_provider resource must be created (D40 -- operator prerequisite)")
	})

	// IAMRoleResourceCount asserts the correct number of aws_iam_role resources are
	// created (one per entry in the roles map) (AC-14).
	t.Run("IAMRoleResourceCount", func(t *testing.T) {
		// Default example has exactly 1 role.
		assertions.AssertResourceCount(t, ctx, "aws_iam_role", 1)
	})

	// TerraformVersion asserts the pinned Terraform version constraint is satisfied
	// (AC-1).
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// Idempotency asserts a plan after apply exits with code 0 (no changes --
	// idempotency requirement) (AC-14).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan must show zero resource changes (exit code 0 means no changes)")
	})
}

// TestOidcBootstrapEmptyRolesMapFailsFast asserts that an empty roles map causes
// terraform plan to fail (fail-fast -- a bootstrap unit must create at least one role)
// (AC-14). This test is standalone and does not share state with the happy-path parent
// test.
func TestOidcBootstrapEmptyRolesMapFailsFast(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("oidc-bootstrap-empty-roles-%s", suffix),
		ExtraVars: map[string]interface{}{
			"roles": map[string]interface{}{},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err,
		"plan must fail when roles map is empty (fail-fast -- bootstrap must create at least one role)")
}

// ---------------------------------------------------------------------------
// State-walking helpers
// ---------------------------------------------------------------------------

// findResourcesByType walks the Terraform state tree and returns all resources
// matching the given resource type.
func findResourcesByType(t *testing.T, state map[string]interface{}, resourceType string) []map[string]interface{} {
	t.Helper()

	values, ok := state["values"].(map[string]interface{})
	if !ok {
		return nil
	}

	rootModule, ok := values["root_module"].(map[string]interface{})
	if !ok {
		return nil
	}

	return walkModuleForType(t, rootModule, resourceType)
}

// walkModuleForType recursively walks a Terraform state module tree collecting
// resources of the given type.
func walkModuleForType(t *testing.T, module map[string]interface{}, resourceType string) []map[string]interface{} {
	t.Helper()
	var result []map[string]interface{}

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType == resourceType {
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
			result = append(result, walkModuleForType(t, child, resourceType)...)
		}
	}

	return result
}
