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

// skipUnlessQuickSightSubscribed gates tests that apply the with-athena-source example,
// which creates aws_quicksight_data_source. QuickSight CreateDataSource provisions
// against the account's QuickSight directory, which only exists once the account has an
// active QuickSight subscription. In an unsubscribed account CreateDataSource fails with
// "ResourceNotFoundException: Directory information for account <id> is not found".
// A QuickSight subscription is an account-level prerequisite that cannot be created from
// this module's Terraform, so these tests run only when the operator has confirmed a
// subscription exists by setting TT_QUICKSIGHT_SUBSCRIBED=true.
func skipUnlessQuickSightSubscribed(t *testing.T) {
	t.Helper()
	if os.Getenv("TT_QUICKSIGHT_SUBSCRIBED") != "true" {
		t.Skipf("SKIP: requires an active QuickSight account subscription. " +
			"CreateDataSource fails with 'Directory information for account ... is not found' " +
			"in an unsubscribed account. Set TT_QUICKSIGHT_SUBSCRIBED=true to run in a " +
			"subscribed account (subscription is an account-level prerequisite not creatable " +
			"by this module).")
	}
}


// TestCommonQuickSightTerraformVersionBasic asserts Terraform version satisfies the pinned constraint for the basic example.
func TestCommonQuickSightTerraformVersionBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("qs-common-tf-version-basic-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonQuickSightTerraformVersionWithAthenaSource asserts Terraform version for the with-athena-source example.
func TestCommonQuickSightTerraformVersionWithAthenaSource(t *testing.T) {
	skipUnlessQuickSightSubscribed(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-athena-source", testctx.TestConfig{
		Name: fmt.Sprintf("qs-common-tf-version-athena-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name": fmt.Sprintf("telemetry-wg-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonQuickSightRequiredOutputsBasic asserts the data source outputs are declared
// for the basic example even though no data source is configured (empty strings).
func TestCommonQuickSightRequiredOutputsBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("qs-common-outputs-basic-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	_, err := terraform.OutputE(t, ctx.Terraform, "data_source_arn")
	assert.NoError(t, err, "data_source_arn output must be declared even when no data source is configured")
	_, err = terraform.OutputE(t, ctx.Terraform, "data_source_id")
	assert.NoError(t, err, "data_source_id output must be declared even when no data source is configured")
}

// TestCommonQuickSightRequiredOutputsWithAthenaSource asserts the data source ARN output for with-athena-source.
func TestCommonQuickSightRequiredOutputsWithAthenaSource(t *testing.T) {
	skipUnlessQuickSightSubscribed(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-athena-source", testctx.TestConfig{
		Name: fmt.Sprintf("qs-common-outputs-athena-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name": fmt.Sprintf("telemetry-wg-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "data_source_arn"), "data_source_arn must not be empty")
}

// TestCommonQuickSightIdempotencyBasic asserts the basic example apply is idempotent.
func TestCommonQuickSightIdempotencyBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("qs-common-idem-basic-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertIdempotent(t, ctx)
}

// TestCommonQuickSightIdempotencyWithAthenaSource asserts the with-athena-source example apply is idempotent.
func TestCommonQuickSightIdempotencyWithAthenaSource(t *testing.T) {
	skipUnlessQuickSightSubscribed(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-athena-source", testctx.TestConfig{
		Name: fmt.Sprintf("qs-common-idem-athena-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name": fmt.Sprintf("telemetry-wg-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertIdempotent(t, ctx)
}
