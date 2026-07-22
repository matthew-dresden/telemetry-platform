//go:build terratest

package interface_test

import (
	"fmt"
	"os"
	"strings"
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

// countVpcEndpointResources counts aws_vpc_endpoint resources under the
// "module.example." address prefix in Terraform state.
// Scoped to the module-under-test prefix so the example's vpc_fixture
// resources are excluded from the count.
func countVpcEndpointResources(t *testing.T, ctx testctx.TestContext) int {
	t.Helper()
	output, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
	assert.NoError(t, err, "terraform state list must not fail")
	count := 0
	for _, line := range strings.Split(output, "\n") {
		if strings.Contains(line, "module.example.aws_vpc_endpoint.") {
			count++
		}
	}
	return count
}

// TestInterfaceVpcEndpoint applies the interface example once and runs all happy-path
// assertions as sub-tests sharing the same Terraform state. The parent test's
// t.Cleanup destroys the state only after all sub-tests complete, avoiding the
// sync.Once + per-test-cleanup race where the first sub-test's cleanup fires before
// subsequent sub-tests run.
func TestInterfaceVpcEndpoint(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "interface", testctx.TestConfig{
		Name:      fmt.Sprintf("interface-vpce-shared-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// ResourceCount: exactly 1 aws_vpc_endpoint under module.example.
	// Counts only the module-under-test prefix so the example's vpc_fixture
	// resources are excluded from the assertion.
	t.Run("ResourceCount", func(t *testing.T) {
		count := countVpcEndpointResources(t, ctx)
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

	// DNSEntriesNonEmpty: endpoint_dns_entries map is non-empty for interface endpoints.
	t.Run("DNSEntriesNonEmpty", func(t *testing.T) {
		dnsEntries := terraform.Output(t, ctx.Terraform, "endpoint_dns_entries")
		assert.NotEmpty(t, dnsEntries, "endpoint_dns_entries must not be empty for interface endpoints")
	})

	// RequiredOutputsNotEmpty: all required outputs are non-empty.
	t.Run("RequiredOutputsNotEmpty", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "endpoint_ids"), "endpoint_ids must not be empty")
	})

	// Idempotency: interface example produces zero changes on a second plan.
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode, "second plan must show zero resource changes (exit code 0 means no changes)")
	})

	// TerraformVersion: pinned Terraform version constraint is satisfied.
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})
}
