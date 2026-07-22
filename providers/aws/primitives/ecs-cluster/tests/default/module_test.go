//go:build terratest

package default_test

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


// setupClusterExample initializes and applies the default example with a unique name suffix.
// It returns the test context for further assertions.
func setupClusterExample(t *testing.T, prefix string) testctx.TestContext {
	t.Helper()
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	return testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-cluster-%s-%s", prefix, suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("ecs-%s-%s", prefix[:1], suffix[:8]),
		}, mustTaggingVars(t)),
	})
}

// TestDefaultECSClusterCreation tests that an ECS cluster is created with the expected ARN pattern.
func TestDefaultECSClusterCreation(t *testing.T) {
	ctx := setupClusterExample(t, "create")

	clusterArn := terraform.Output(t, ctx.Terraform, "cluster_arn")
	assert.Regexp(t, `^arn:aws:ecs:[a-z0-9-]+:[0-9]{12}:cluster/`, clusterArn,
		"cluster_arn must match ECS cluster ARN pattern")
}

// TestDefaultECSClusterResourceCounts asserts exactly one aws_ecs_cluster and one aws_ecs_cluster_capacity_providers exist.
func TestDefaultECSClusterResourceCounts(t *testing.T) {
	ctx := setupClusterExample(t, "counts")

	assertions.AssertResourceCount(t, ctx, "aws_ecs_cluster", 1)
	assertions.AssertResourceCount(t, ctx, "aws_ecs_cluster_capacity_providers", 1)
}

// TestDefaultECSClusterRequiredOutputs asserts all required outputs are present and non-empty.
func TestDefaultECSClusterRequiredOutputs(t *testing.T) {
	ctx := setupClusterExample(t, "outputs")

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "cluster_id"), "cluster_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "cluster_arn"), "cluster_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "cluster_name"), "cluster_name must not be empty")
}

// TestDefaultECSClusterTerraformVersion asserts that the Terraform version satisfies the pinned constraint.
func TestDefaultECSClusterTerraformVersion(t *testing.T) {
	ctx := setupClusterExample(t, "tfver")

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestDefaultECSClusterIdempotency asserts the default example is idempotent: a plan after apply must
// exit with code 0 (no changes).
func TestDefaultECSClusterIdempotency(t *testing.T) {
	ctx := setupClusterExample(t, "idem")

	exitCode := terraform.PlanExitCode(t, ctx.Terraform)
	assert.Equal(t, 0, exitCode, "plan after apply must exit 0 (no changes) for idempotency")
}

// TestDefaultECSClusterEmptyNameValidationError tests that an empty name fails validation.
func TestDefaultECSClusterEmptyNameValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-cluster-empty-name-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name": "",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when name is empty")
}

// TestDefaultECSClusterEmptyCapacityProvidersValidationError tests that an empty capacity_providers list fails validation.
func TestDefaultECSClusterEmptyCapacityProvidersValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-cluster-no-cp-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":               fmt.Sprintf("ecs-e-%s", suffix[:8]),
			"capacity_providers": []string{},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when capacity_providers is empty")
}

// TestDefaultECSClusterInvalidCapacityProviderValidationError tests that an invalid capacity provider fails validation.
func TestDefaultECSClusterInvalidCapacityProviderValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-cluster-bad-cp-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":               fmt.Sprintf("ecs-b-%s", suffix[:8]),
			"capacity_providers": []string{"EC2"},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when capacity_providers contains non-Fargate values")
}

// TestECSClusterFargateOnlyNoExplicitStrategyPlanSucceeds verifies that a cluster configured with
// capacity_providers=[FARGATE] and no explicit default_capacity_provider_strategy can be planned
// without errors. This is a regression guard for the PutClusterCapacityProviders bug where the
// module default included FARGATE_SPOT in the strategy even when FARGATE_SPOT was absent from
// capacity_providers.
func TestECSClusterFargateOnlyNoExplicitStrategyPlanSucceeds(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-cluster-fargate-only-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":               fmt.Sprintf("ecs-f-%s", suffix[:8]),
			"capacity_providers": []string{"FARGATE"},
			// No default_capacity_provider_strategy -- the module must not inject FARGATE_SPOT
			// into the effective strategy when only FARGATE is in capacity_providers.
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.NoError(t, err, "plan must succeed when capacity_providers=[FARGATE] and no strategy is set")
}
