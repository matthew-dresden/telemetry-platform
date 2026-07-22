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

// TestEcsAppDeployCommon applies the basic example once, runs all common output and version
// assertions as subtests sharing the applied state, and destroys via t.Cleanup when the
// parent test ends. Common tests run against the basic example -- no ALB or autoscaling
// required. This ensures destroy runs after all subtests complete (not after the first
// subtest), satisfying FR-3 tag wiring and the D-12 no-premature-destroy rule.
func TestEcsAppDeployCommon(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	name := fmt.Sprintf("ecs-common-%s", suffix)

	tagging := mustTaggingVars(t)
	extraVars := mergeExtraVars(t, map[string]interface{}{
		"name": name,
	}, tagging)

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      name,
		ExtraVars: extraVars,
	})

	t.Run("ValidateAndFmt", func(t *testing.T) {
		// terraform validate confirms the module configuration is internally consistent.
		// A successful apply (in the parent test) already implies validate passed; calling it
		// explicitly here makes the assertion visible and attributable in the test log.
		terraform.Validate(t, ctx.Terraform)

		// terraform fmt -check -recursive asserts every .tf file is canonical HCL.
		output, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "fmt", "-check", "-recursive")
		require.NoError(t, err,
			"terraform fmt -check must exit zero; unformatted files: %s", output)
		assert.Empty(t, output,
			"terraform fmt -check must report no unformatted files (AC-1)")
	})

	t.Run("RequiredOutputsPresent", func(t *testing.T) {
		// These outputs are always non-null in any working ecs-app-deploy deployment.
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "service_arn"),
			"service_arn must be declared and non-empty (AC-3)")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "service_name"),
			"service_name must be declared and non-empty (AC-3)")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "task_definition_arn"),
			"task_definition_arn must be declared and non-empty (AC-3)")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "task_role_arn"),
			"task_role_arn must be declared and non-empty (AC-3)")

		// ssm_parameter_arns is always non-empty (at least one SSM param in every example).
		assert.NotEmpty(t, terraform.OutputJson(t, ctx.Terraform, "ssm_parameter_arns"),
			"ssm_parameter_arns must be declared and non-empty (AC-3)")

		// target_group_arns is an empty map (not null) in the basic example because the
		// module always outputs a map (empty when no ALB is configured).
		assert.NotEmpty(t, terraform.OutputJson(t, ctx.Terraform, "target_group_arns"),
			"target_group_arns must be declared; returns empty map JSON when no ALB is configured (AC-3)")
	})

	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan must show zero resource changes (idempotency requirement)")
	})
}
