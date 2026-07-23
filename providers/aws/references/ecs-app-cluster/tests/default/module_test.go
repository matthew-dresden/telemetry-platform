//go:build terratest

package default_test

import (
	"fmt"
	"os"
	"regexp"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/assertions"
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

// TestEcsAppCluster applies the default example once, runs all output and version assertions
// as subtests sharing the applied state, and destroys via t.Cleanup when the parent test ends.
// This ensures destroy runs after all subtests complete (not after the first subtest).
func TestEcsAppCluster(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("ecs-app-cluster-default-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	t.Run("ClusterArnOutput", func(t *testing.T) {
		clusterArn := terraform.Output(t, ctx.Terraform, "cluster_arn")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:ecs:[a-z0-9-]+:[0-9]{12}:cluster/`),
			clusterArn,
			"cluster_arn must match ECS cluster ARN pattern",
		)
	})

	t.Run("ExecutionRoleArnOutput", func(t *testing.T) {
		roleArn := terraform.Output(t, ctx.Terraform, "execution_role_arn")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:iam::`),
			roleArn,
			"execution_role_arn must match ^arn:aws:iam::",
		)
	})

	t.Run("DashboardArnOutput", func(t *testing.T) {
		dashboardArn := terraform.Output(t, ctx.Terraform, "dashboard_arn")
		assert.NotEmpty(t, dashboardArn, "dashboard_arn must be present and non-empty")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:cloudwatch:`),
			dashboardArn,
			"dashboard_arn must match CloudWatch dashboard ARN pattern",
		)
	})

	t.Run("ClusterNameOutput", func(t *testing.T) {
		clusterName := terraform.Output(t, ctx.Terraform, "cluster_name")
		assert.NotEmpty(t, clusterName, "cluster_name must not be empty")
	})

	t.Run("ClusterIDOutput", func(t *testing.T) {
		clusterID := terraform.Output(t, ctx.Terraform, "cluster_id")
		assert.NotEmpty(t, clusterID, "cluster_id must not be empty")
	})

	t.Run("RequiredOutputsPresent", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "cluster_arn"), "cluster_arn must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "cluster_name"), "cluster_name must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "cluster_id"), "cluster_id must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "execution_role_arn"), "execution_role_arn must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "dashboard_arn"), "dashboard_arn must not be empty")
	})

	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})
}

// TestEcsAppClusterInvalidClusterNameValidationError asserts that an invalid cluster_name fails validation.
// This test is independent -- it does not apply infrastructure and uses InitAndPlanE only.
func TestEcsAppClusterInvalidClusterNameValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("ecs-app-cluster-bad-name-%s", suffix),
		ExtraVars: map[string]interface{}{
			"cluster_name": "invalid name with spaces",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when cluster_name contains spaces")
}
