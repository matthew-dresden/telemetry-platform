//go:build terratest

package basic_test

import (
	"fmt"
	"os"
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

// TestRoute53ZoneCreation tests that a Route53 hosted zone is created and outputs
// match required patterns per docs/terragrunt-concepts.md. Also asserts Terraform version
// meets the minimum requirement and the plan is idempotent after apply.
func TestRoute53ZoneCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-route53-zone-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"zone_name": fmt.Sprintf("tt-%s.example-terratest.net", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	assertions.AssertIdempotent(t, ctx)

	zoneID := terraform.Output(t, ctx.Terraform, "zone_id")
	assert.Regexp(t, `^Z`, zoneID, "zone_id must start with Z")

	nameServersRaw := terraform.OutputList(t, ctx.Terraform, "name_servers")
	assert.Equal(t, 4, len(nameServersRaw), "name_servers must have exactly 4 entries")
	for _, ns := range nameServersRaw {
		assert.NotEmpty(t, ns, "each name server entry must be non-empty")
	}
}

// TestRoute53ZoneResourceCount asserts exactly one aws_route53_zone resource exists.
func TestRoute53ZoneResourceCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-route53-zone-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"zone_name": fmt.Sprintf("tt-%s.example-terratest.net", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_route53_zone", 1)
}

// TestRoute53ZoneRequiredOutputsPresent asserts all required outputs are non-empty.
func TestRoute53ZoneRequiredOutputsPresent(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-route53-zone-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"zone_name": fmt.Sprintf("tt-%s.example-terratest.net", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "zone_id"), "zone_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "arn"), "arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "zone_name"), "zone_name must not be empty")
	nameServers := terraform.OutputList(t, ctx.Terraform, "name_servers")
	assert.Equal(t, 4, len(nameServers), "name_servers must have exactly 4 entries")
}

// TestRoute53ZoneInvalidName asserts that an invalid zone_name fails validation.
func TestRoute53ZoneInvalidName(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-route53-zone-invalid-%s", suffix),
		ExtraVars: map[string]interface{}{
			"zone_name": "INVALID_ZONE_NAME!",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when zone_name does not match ^[a-z0-9.-]+$")
}
