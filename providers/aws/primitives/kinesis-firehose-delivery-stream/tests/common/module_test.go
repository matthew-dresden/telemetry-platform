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

// TestCommonFirehoseTerraformVersionBasic asserts Terraform version satisfies the pinned constraint for the basic example.
func TestCommonFirehoseTerraformVersionBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-firehose-tf-version-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-cmn-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonFirehoseTerraformVersionParquetPartitioned asserts Terraform version for the parquet-partitioned example.
func TestCommonFirehoseTerraformVersionParquetPartitioned(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "parquet-partitioned", testctx.TestConfig{
		Name: fmt.Sprintf("common-firehose-tf-version-pp-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-cmn-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonFirehoseRequiredOutputsBasic asserts all required outputs are present for the basic example.
func TestCommonFirehoseRequiredOutputsBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-firehose-outputs-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-cmn-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "delivery_stream_arn"), "delivery_stream_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "delivery_stream_name"), "delivery_stream_name must not be empty")
}

// TestCommonFirehoseRequiredOutputsParquetPartitioned asserts all required outputs are present for the parquet-partitioned example.
func TestCommonFirehoseRequiredOutputsParquetPartitioned(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "parquet-partitioned", testctx.TestConfig{
		Name: fmt.Sprintf("common-firehose-outputs-pp-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-cmn-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "delivery_stream_arn"), "delivery_stream_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "delivery_stream_name"), "delivery_stream_name must not be empty")
}

// TestCommonFirehoseIdempotencyBasic asserts the basic example resource count is stable after apply.
func TestCommonFirehoseIdempotencyBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-firehose-idem-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-cmn-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_kinesis_firehose_delivery_stream", 1)
}

// TestCommonFirehoseIdempotencyParquetPartitioned asserts the parquet-partitioned example resource count is stable.
func TestCommonFirehoseIdempotencyParquetPartitioned(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "parquet-partitioned", testctx.TestConfig{
		Name: fmt.Sprintf("common-firehose-idem-pp-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-cmn-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_kinesis_firehose_delivery_stream", 1)
}
