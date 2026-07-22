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


// TestCommonGlueTerraformVersionBasic asserts Terraform version satisfies the pinned constraint for the basic example.
func TestCommonGlueTerraformVersionBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-glue-tf-version-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_cmn_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonGlueTerraformVersionWithTable asserts Terraform version satisfies the pinned constraint for the with-table example.
func TestCommonGlueTerraformVersionWithTable(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-table", testctx.TestConfig{
		Name: fmt.Sprintf("common-glue-tf-version-wt-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_cmn_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonGlueRequiredOutputsBasic asserts all required outputs are present for the basic example.
func TestCommonGlueRequiredOutputsBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-glue-outputs-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_cmn_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "database_name"), "database_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "database_arn"), "database_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "catalog_id"), "catalog_id must not be empty")
}

// TestCommonGlueRequiredOutputsWithTable asserts all required outputs are present for the with-table example.
func TestCommonGlueRequiredOutputsWithTable(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-table", testctx.TestConfig{
		Name: fmt.Sprintf("common-glue-outputs-wt-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_cmn_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "database_name"), "database_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "database_arn"), "database_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "catalog_id"), "catalog_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "table_name"), "table_name must not be empty")
}

// TestCommonGlueIdempotencyBasic asserts the basic example resource count is stable after apply.
func TestCommonGlueIdempotencyBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-glue-idem-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_cmn_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_database", 1)
	assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_table", 0)
}

// TestCommonGlueIdempotencyWithTable asserts the with-table example resource count is stable after apply.
func TestCommonGlueIdempotencyWithTable(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-table", testctx.TestConfig{
		Name: fmt.Sprintf("common-glue-idem-wt-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_cmn_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_database", 1)
	assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_table", 1)
}
