//go:build terratest

package default_test

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

// TestDefaultAthenaWorkgroupCount asserts exactly one aws_athena_workgroup resource is created.
func TestDefaultAthenaWorkgroupCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name: fmt.Sprintf("athena-wg-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name":                 fmt.Sprintf("telemetry-wg-%s", suffix),
			"result_s3_bucket":               fmt.Sprintf("telemetry-results-%s", suffix),
			"result_kms_key_arn":             "arn:aws:kms:us-east-1:123456789012:key/test-key-id",
			"bytes_scanned_cutoff_per_query": 10485760,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_athena_workgroup", 1)
}

// TestDefaultAthenaWorkgroupARNFormat asserts the workgroup_arn output matches the Athena ARN pattern.
func TestDefaultAthenaWorkgroupARNFormat(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name: fmt.Sprintf("athena-wg-arn-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name":                 fmt.Sprintf("telemetry-wg-%s", suffix),
			"result_s3_bucket":               fmt.Sprintf("telemetry-results-%s", suffix),
			"result_kms_key_arn":             "arn:aws:kms:us-east-1:123456789012:key/test-key-id",
			"bytes_scanned_cutoff_per_query": 10485760,
		}, mustTaggingVars(t)),
	})

	assertions.AssertOutputMatches(t, ctx, "workgroup_arn", `^arn:aws:athena:`)
}

// TestDefaultAthenaWorkgroupRequiredOutputsNotEmpty asserts all required outputs are present.
func TestDefaultAthenaWorkgroupRequiredOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name: fmt.Sprintf("athena-wg-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name":                 fmt.Sprintf("telemetry-wg-%s", suffix),
			"result_s3_bucket":               fmt.Sprintf("telemetry-results-%s", suffix),
			"result_kms_key_arn":             "arn:aws:kms:us-east-1:123456789012:key/test-key-id",
			"bytes_scanned_cutoff_per_query": 10485760,
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "workgroup_id"), "workgroup_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "workgroup_arn"), "workgroup_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "workgroup_name"), "workgroup_name must not be empty")
}

// TestDefaultAthenaWorkgroupTerraformVersion asserts Terraform version satisfies the pinned constraint.
func TestDefaultAthenaWorkgroupTerraformVersion(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name: fmt.Sprintf("athena-wg-tf-version-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name":                 fmt.Sprintf("telemetry-wg-%s", suffix),
			"result_s3_bucket":               fmt.Sprintf("telemetry-results-%s", suffix),
			"result_kms_key_arn":             "arn:aws:kms:us-east-1:123456789012:key/test-key-id",
			"bytes_scanned_cutoff_per_query": 10485760,
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestDefaultAthenaWorkgroupIdempotency asserts apply is idempotent (no changes on re-apply).
func TestDefaultAthenaWorkgroupIdempotency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name: fmt.Sprintf("athena-wg-idem-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"workgroup_name":                 fmt.Sprintf("telemetry-wg-%s", suffix),
			"result_s3_bucket":               fmt.Sprintf("telemetry-results-%s", suffix),
			"result_kms_key_arn":             "arn:aws:kms:us-east-1:123456789012:key/test-key-id",
			"bytes_scanned_cutoff_per_query": 10485760,
		}, mustTaggingVars(t)),
	})

	assertions.AssertIdempotent(t, ctx)
}

// TestDefaultAthenaWorkgroupBytesScannedCutoffBelowMinFails asserts plan fails when bytes_scanned_cutoff_per_query is below the minimum.
func TestDefaultAthenaWorkgroupBytesScannedCutoffBelowMinFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("athena-wg-cutoff-fail-%s", suffix),
		ExtraVars: map[string]interface{}{
			"workgroup_name":                 fmt.Sprintf("telemetry-wg-%s", suffix),
			"result_s3_bucket":               fmt.Sprintf("telemetry-results-%s", suffix),
			"result_kms_key_arn":             "arn:aws:kms:us-east-1:123456789012:key/test-key-id",
			"bytes_scanned_cutoff_per_query": 1000000,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when bytes_scanned_cutoff_per_query is below 10485760")
}
