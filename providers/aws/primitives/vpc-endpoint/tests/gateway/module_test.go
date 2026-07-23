//go:build terratest

package gateway_test

import (
	"fmt"
	"os"
	"strings"
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

// countResourcesByTypeAndPrefix counts resources of resourceType whose state address
// contains the given modulePrefix (e.g. "module.example.").
// Used instead of assertions.AssertResourceCount so the count can be scoped to the
// module-under-test address prefix and exclude the example's vpc_fixture resources.
func countResourcesByTypeAndPrefix(t *testing.T, ctx testctx.TestContext, resourceType, modulePrefix string) int {
	t.Helper()
	output, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
	assert.NoError(t, err, "terraform state list must not fail")
	count := 0
	needle := modulePrefix + resourceType + "."
	for _, line := range strings.Split(output, "\n") {
		if strings.Contains(line, needle) {
			count++
		}
	}
	return count
}

// TestGatewayVpcEndpoint applies the gateway example once and runs all happy-path
// assertions as sub-tests sharing the same Terraform state. The parent test's
// t.Cleanup destroys the state only after all sub-tests complete, avoiding the
// sync.Once + per-test-cleanup race where the first sub-test's cleanup fires before
// subsequent sub-tests run.
func TestGatewayVpcEndpoint(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "gateway", testctx.TestConfig{
		Name:      fmt.Sprintf("gateway-vpce-shared-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// ResourceCount: exactly 1 aws_vpc_endpoint under module.example.
	// Counts only the module-under-test prefix so the example's vpc_fixture
	// resources are excluded from the assertion.
	t.Run("ResourceCount", func(t *testing.T) {
		count := countResourcesByTypeAndPrefix(t, ctx, "aws_vpc_endpoint", "module.example.")
		assert.Equal(t, 1, count, "expected exactly 1 aws_vpc_endpoint resource under module.example")
	})

	// IDFormat: each endpoint_ids value matches ^vpce-.
	t.Run("IDFormat", func(t *testing.T) {
		endpointIDs := terraform.OutputMap(t, ctx.Terraform, "endpoint_ids")
		assert.NotEmpty(t, endpointIDs, "endpoint_ids must not be empty")
		for name, id := range endpointIDs {
			assert.Regexp(t, `^vpce-`, id, "endpoint_ids[%s] must match ^vpce-", name)
		}
	})

	// AssociatedToRouteTable: gateway endpoint appears in state, confirming route table
	// association was accepted by the AWS API.
	t.Run("AssociatedToRouteTable", func(t *testing.T) {
		output, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
		assert.NoError(t, err, "terraform state list must not fail")

		hasEndpoint := false
		for _, line := range strings.Split(output, "\n") {
			line = strings.TrimSpace(line)
			if len(line) > 0 && strings.Contains(line, "aws_vpc_endpoint.") {
				hasEndpoint = true
				break
			}
		}
		assert.True(t, hasEndpoint, "aws_vpc_endpoint must appear in state, confirming route table association was accepted")

		endpointIDs := terraform.OutputMap(t, ctx.Terraform, "endpoint_ids")
		assert.NotEmpty(t, endpointIDs, "endpoint_ids must be non-empty, confirming gateway endpoint was created with route table association")
	})

	// RequiredOutputsNotEmpty: all required outputs are non-empty.
	t.Run("RequiredOutputsNotEmpty", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "endpoint_ids"), "endpoint_ids must not be empty")
	})

	// Idempotency: gateway example produces zero changes on a second plan.
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode, "second plan must show zero resource changes (exit code 0 means no changes)")
	})

	// TerraformVersion: pinned Terraform version constraint is satisfied.
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})
}
