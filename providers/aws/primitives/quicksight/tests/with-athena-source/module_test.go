//go:build terratest

package with_athena_source_test

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

// skipUnlessQuickSightSubscribed gates tests that create aws_quicksight_data_source.
// QuickSight CreateDataSource provisions against the account's QuickSight directory,
// which only exists once the account has an active QuickSight subscription. In an
// unsubscribed account CreateDataSource fails with
// "ResourceNotFoundException: Directory information for account <id> is not found".
// A QuickSight subscription is an account-level prerequisite that cannot be created
// from this module's Terraform, so these tests run only when the operator has
// confirmed a subscription exists by setting TT_QUICKSIGHT_SUBSCRIBED=true.
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


// TestWithAthenaSourceDataSourceARNNotEmpty asserts data_source_arn output is non-empty.
func TestWithAthenaSourceDataSourceARNNotEmpty(t *testing.T) {
	skipUnlessQuickSightSubscribed(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-athena-source", testctx.TestConfig{
		Name: fmt.Sprintf("qs-athena-ds-arn-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name": fmt.Sprintf("telemetry-wg-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertOutputNotEmpty(t, ctx, "data_source_arn")
}

// TestWithAthenaSourceDataSourceCount asserts exactly one aws_quicksight_data_source is created.
func TestWithAthenaSourceDataSourceCount(t *testing.T) {
	skipUnlessQuickSightSubscribed(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-athena-source", testctx.TestConfig{
		Name: fmt.Sprintf("qs-athena-ds-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name": fmt.Sprintf("telemetry-wg-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_quicksight_data_source", 1)
}

// TestWithAthenaSourceDataSourceARNFormat asserts data_source_arn matches the QuickSight ARN pattern.
func TestWithAthenaSourceDataSourceARNFormat(t *testing.T) {
	skipUnlessQuickSightSubscribed(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-athena-source", testctx.TestConfig{
		Name: fmt.Sprintf("qs-athena-ds-arn-fmt-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name": fmt.Sprintf("telemetry-wg-%s", suffix),
		}, mustTaggingVars(t)),
	})

	dataSourceArn := terraform.Output(t, ctx.Terraform, "data_source_arn")
	assert.Regexp(t, `^arn:aws:quicksight:`, dataSourceArn, "data_source_arn must match QuickSight ARN pattern")
}

// TestWithAthenaSourceIdempotency asserts apply is idempotent.
func TestWithAthenaSourceIdempotency(t *testing.T) {
	skipUnlessQuickSightSubscribed(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-athena-source", testctx.TestConfig{
		Name: fmt.Sprintf("qs-athena-idem-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name": fmt.Sprintf("telemetry-wg-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertIdempotent(t, ctx)
}
