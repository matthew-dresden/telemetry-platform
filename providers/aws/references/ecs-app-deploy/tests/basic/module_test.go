//go:build terratest

package basic_test

import (
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
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

// TestEcsAppDeployBasic applies the basic example once, runs all output and idempotency
// assertions as subtests sharing the applied state, and destroys via t.Cleanup when the
// parent test ends. This ensures destroy runs after all subtests complete (not after the
// first subtest), satisfying FR-3 tag wiring and the D-12 no-premature-destroy rule.
func TestEcsAppDeployBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	name := fmt.Sprintf("ecs-basic-%s", suffix)

	tagging := mustTaggingVars(t)
	extraVars := mergeExtraVars(t, map[string]interface{}{
		"name": name,
	}, tagging)

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      name,
		ExtraVars: extraVars,
	})

	t.Run("ServiceArnPresent", func(t *testing.T) {
		serviceArn := terraform.Output(t, ctx.Terraform, "service_arn")
		assert.NotEmpty(t, serviceArn,
			"service_arn must be non-empty (re-exported from ecs-service)")
	})

	t.Run("TaskDefinitionArnPresent", func(t *testing.T) {
		tdArn := terraform.Output(t, ctx.Terraform, "task_definition_arn")
		assert.NotEmpty(t, tdArn,
			"task_definition_arn must be non-empty (re-exported from ecs-service)")
	})

	t.Run("ServiceNameNotEmpty", func(t *testing.T) {
		serviceName := terraform.Output(t, ctx.Terraform, "service_name")
		assert.NotEmpty(t, serviceName,
			"service_name re-export must not be empty -- proves the wiring is not dangling (AC-3)")
	})

	t.Run("SsmParameterArnsMapSizeOne", func(t *testing.T) {
		arnsJSON := terraform.OutputJson(t, ctx.Terraform, "ssm_parameter_arns")
		require.NotEmpty(t, arnsJSON, "ssm_parameter_arns output must be non-empty JSON")

		var arns map[string]interface{}
		err := json.Unmarshal([]byte(arnsJSON), &arns)
		require.NoError(t, err, "ssm_parameter_arns must be valid JSON map")

		assert.Equal(t, 1, len(arns),
			"ssm_parameter_arns map must contain exactly 1 entry in the basic example")
	})

	t.Run("TaskRoleArnPresent", func(t *testing.T) {
		roleArn := terraform.Output(t, ctx.Terraform, "task_role_arn")
		assert.NotEmpty(t, roleArn,
			"task_role_arn must be non-empty (re-exported from iam-role)")
	})

	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan must show zero resource changes (idempotency requirement)")
	})
}
