//go:build terratest

package prod_subset_test

import (
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	"github.com/gruntwork-io/terratest/modules/terraform"
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

// TestOidcBootstrapProdSubset is the parent test that applies the prod-subset example
// once, runs all happy-path subtests against the live state, then destroys via
// t.Cleanup when the parent test (and all its subtests) complete.
//
// Using the parent-test pattern ensures t.Cleanup(terraform.Destroy) -- registered
// by testctx.RunSingleExample on the parent t -- fires only after ALL subtests
// complete. The previous sync.Once pattern registered cleanup on the first individual
// test's t, causing destroy to fire between test functions and leaving subsequent
// tests operating on empty state.
func TestOidcBootstrapProdSubset(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "prod-subset", testctx.TestConfig{
		Name:      fmt.Sprintf("oidc-bootstrap-prod-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// IAMRoleCount asserts exactly 2 aws_iam_role resources are created -- one for
	// telemetry-platform-gha-tg-plan and one for telemetry-platform-gha-tg-apply (AC-14).
	t.Run("IAMRoleCount", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_iam_role", 2)
	})

	// RoleArnsHasTwoEntries asserts the role_arns output map contains exactly 2
	// entries matching the two prod roles (AC-14).
	t.Run("RoleArnsHasTwoEntries", func(t *testing.T) {
		roleArnsJSON := terraform.OutputJson(t, ctx.Terraform, "role_arns")
		require.NotEmpty(t, roleArnsJSON, "role_arns must not be empty")

		var roleArns map[string]string
		require.NoError(t, json.Unmarshal([]byte(roleArnsJSON), &roleArns),
			"role_arns must be valid JSON map")

		assert.Equal(t, 2, len(roleArns),
			"prod-subset example must create exactly 2 roles (plan + apply)")

		_, hasPlan := roleArns["telemetry-platform-gha-tg-plan"]
		assert.True(t, hasPlan,
			"role_arns must contain 'telemetry-platform-gha-tg-plan' key")

		_, hasApply := roleArns["telemetry-platform-gha-tg-apply"]
		assert.True(t, hasApply,
			"role_arns must contain 'telemetry-platform-gha-tg-apply' key")
	})

	// ApplyRoleTrustPolicyContainsProdApply asserts the apply role's trust policy
	// document in Terraform state contains "environment:prod-apply" (AC-14).
	t.Run("ApplyRoleTrustPolicyContainsProdApply", func(t *testing.T) {
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

		applyRoleTrustPolicy := findApplyRoleTrustPolicy(t, rootModule)
		require.NotEmpty(t, applyRoleTrustPolicy,
			"apply role trust policy must be found in state")

		assert.Contains(t, applyRoleTrustPolicy, "environment:prod-apply",
			"apply role trust policy must contain 'environment:prod-apply' sub binding (D8)")
	})

	// ApplyRoleTrustPolicyUsesStringLikeForSub asserts the apply role's trust
	// policy places the token.actions.githubusercontent.com:sub condition under
	// the StringLike operator (not StringEquals). The sub value is a wildcard
	// pattern (repo:org/repo:*); StringEquals treats "*" as a literal and matches
	// nothing, rejecting every sts:AssumeRoleWithWebIdentity call. The :aud
	// condition stays StringEquals because it is an exact value (sts.amazonaws.com).
	t.Run("ApplyRoleTrustPolicyUsesStringLikeForSub", func(t *testing.T) {
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

		applyRoleTrustPolicy := findApplyRoleTrustPolicy(t, rootModule)
		require.NotEmpty(t, applyRoleTrustPolicy,
			"apply role trust policy must be found in state")

		stringEqualsSub, stringLikeSub := subConditionOperators(t, applyRoleTrustPolicy)

		assert.Equal(t, "", stringEqualsSub,
			"token.actions.githubusercontent.com:sub must NOT appear under StringEquals (a wildcard sub under StringEquals matches nothing)")
		assert.Equal(t, "repo:example-org/telemetry-platform:environment:prod-apply", stringLikeSub,
			"token.actions.githubusercontent.com:sub must appear under StringLike with the prod-apply wildcard sub value")
	})

	// Idempotency asserts a plan after apply exits with code 0 (no changes --
	// idempotency requirement) (AC-14).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan on prod-subset must show zero resource changes (idempotency)")
	})
}

// ---------------------------------------------------------------------------
// State-walking helpers
// ---------------------------------------------------------------------------

// subConditionOperators parses an IAM trust policy JSON document and returns the
// value bound to token.actions.githubusercontent.com:sub under the StringEquals
// operator and under the StringLike operator, respectively. An empty string for
// either means the sub key is absent under that operator. It fails the test if
// the policy is not parseable or has no statements.
func subConditionOperators(t *testing.T, policyJSON string) (stringEqualsSub string, stringLikeSub string) {
	t.Helper()

	const subKey = "token.actions.githubusercontent.com:sub"

	var policy struct {
		Statement []struct {
			Condition map[string]map[string]interface{} `json:"Condition"`
		} `json:"Statement"`
	}
	require.NoError(t, json.Unmarshal([]byte(policyJSON), &policy),
		"trust policy must be valid JSON")
	require.NotEmpty(t, policy.Statement,
		"trust policy must contain at least one statement")

	for _, stmt := range policy.Statement {
		if eq, ok := stmt.Condition["StringEquals"]; ok {
			if v, ok := eq[subKey].(string); ok {
				stringEqualsSub = v
			}
		}
		if like, ok := stmt.Condition["StringLike"]; ok {
			if v, ok := like[subKey].(string); ok {
				stringLikeSub = v
			}
		}
	}

	return stringEqualsSub, stringLikeSub
}

// findApplyRoleTrustPolicy walks the state to find the assume_role_policy of the
// telemetry-platform-gha-tg-apply role and returns it as a string.
func findApplyRoleTrustPolicy(t *testing.T, module map[string]interface{}) string {
	t.Helper()

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_iam_role" {
				continue
			}

			values, ok := res["values"].(map[string]interface{})
			if !ok {
				continue
			}

			name, _ := values["name"].(string)
			if name == "telemetry-platform-gha-tg-apply" {
				policy, _ := values["assume_role_policy"].(string)
				return policy
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			result := findApplyRoleTrustPolicy(t, child)
			if result != "" {
				return result
			}
		}
	}

	return ""
}
