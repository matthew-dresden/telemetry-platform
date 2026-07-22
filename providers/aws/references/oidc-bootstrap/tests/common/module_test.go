//go:build terratest

package common_test

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

// TestCommonOidcBootstrapDefault is the parent test that applies the default example
// once, runs all happy-path subtests against the live state, then destroys via
// t.Cleanup when the parent test (and all its subtests) complete.
//
// Using the parent-test pattern ensures t.Cleanup(terraform.Destroy) -- registered
// by testctx.RunSingleExample on the parent t -- fires only after ALL subtests
// complete. The previous sync.Once pattern registered cleanup on the first individual
// test's t, causing destroy to fire between test functions and leaving subsequent
// tests operating on empty state.
func TestCommonOidcBootstrapDefault(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("oidc-bootstrap-common-default-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// TerraformVersion asserts the pinned Terraform version is satisfied for the
	// default example (AC-1).
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// RequiredOutputs asserts the required role_arns output is present and non-empty
	// for the default example (AC-1).
	t.Run("RequiredOutputs", func(t *testing.T) {
		roleArnsJSON := terraform.OutputJson(t, ctx.Terraform, "role_arns")
		require.NotEmpty(t, roleArnsJSON, "role_arns must not be empty (default example)")

		var roleArns map[string]string
		require.NoError(t, json.Unmarshal([]byte(roleArnsJSON), &roleArns),
			"role_arns must be valid JSON map (default example)")

		assert.GreaterOrEqual(t, len(roleArns), 1,
			"role_arns must have at least one entry (default example)")
	})

	// Idempotency asserts the default example is idempotent (AC-1).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan on default example must show zero resource changes (idempotency)")
	})

	// ValidateAndFmt asserts the default example passes terraform validate and
	// terraform fmt -check -recursive (AC-1, AC-14).
	// terraform.Validate() calls `terraform validate` on the example directory,
	// confirming the HCL is syntactically valid and internally consistent.
	// terraform fmt -check -recursive confirms every .tf file is canonically formatted.
	t.Run("ValidateAndFmt", func(t *testing.T) {
		// terraform validate confirms the configuration is internally consistent.
		// A successful apply already implies validate passed; calling it explicitly here
		// makes the assertion visible and attributable in the test log.
		terraform.Validate(t, ctx.Terraform)

		// terraform fmt -check -recursive asserts that every .tf file under the example
		// directory is canonical HCL. An empty output means no files need reformatting.
		output, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "fmt", "-check", "-recursive")
		require.NoError(t, err,
			"terraform fmt -check must exit zero for default example; unformatted files: %s", output)
		assert.Empty(t, output,
			"terraform fmt -check must report no unformatted files in default example (AC-1)")
	})
}

// TestCommonOidcBootstrapProdSubset is the parent test that applies the prod-subset
// example once, runs all happy-path subtests against the live state, then destroys
// via t.Cleanup when the parent test (and all its subtests) complete.
//
// Using the parent-test pattern ensures t.Cleanup(terraform.Destroy) -- registered
// by testctx.RunSingleExample on the parent t -- fires only after ALL subtests
// complete. The previous sync.Once pattern registered cleanup on the first individual
// test's t, causing destroy to fire between test functions and leaving subsequent
// tests operating on empty state.
func TestCommonOidcBootstrapProdSubset(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "prod-subset", testctx.TestConfig{
		Name:      fmt.Sprintf("oidc-bootstrap-common-prod-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// TerraformVersion asserts the pinned Terraform version is satisfied for the
	// prod-subset example (AC-1).
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// RequiredOutputs asserts the required role_arns output is present and non-empty
	// for the prod-subset example (AC-1).
	t.Run("RequiredOutputs", func(t *testing.T) {
		roleArnsJSON := terraform.OutputJson(t, ctx.Terraform, "role_arns")
		require.NotEmpty(t, roleArnsJSON, "role_arns must not be empty (prod-subset example)")

		var roleArns map[string]string
		require.NoError(t, json.Unmarshal([]byte(roleArnsJSON), &roleArns),
			"role_arns must be valid JSON map (prod-subset example)")

		assert.Equal(t, 2, len(roleArns),
			"role_arns must have exactly 2 entries (prod-subset example)")
	})

	// Idempotency asserts the prod-subset example is idempotent (AC-1).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan on prod-subset example must show zero resource changes (idempotency)")
	})

	// ValidateAndFmt asserts the prod-subset example passes terraform validate and
	// terraform fmt -check -recursive (AC-1, AC-14).
	t.Run("ValidateAndFmt", func(t *testing.T) {
		// terraform validate confirms the configuration is internally consistent.
		terraform.Validate(t, ctx.Terraform)

		// terraform fmt -check -recursive asserts that every .tf file under the example
		// directory is canonical HCL. An empty output means no files need reformatting.
		output, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "fmt", "-check", "-recursive")
		require.NoError(t, err,
			"terraform fmt -check must exit zero for prod-subset example; unformatted files: %s", output)
		assert.Empty(t, output,
			"terraform fmt -check must report no unformatted files in prod-subset example (AC-1)")
	})
}
