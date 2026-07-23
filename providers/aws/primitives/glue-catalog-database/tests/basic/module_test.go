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

// TestBasicGlueDatabaseARNFormat asserts the database_arn output matches the Glue ARN pattern.
func TestBasicGlueDatabaseARNFormat(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-glue-arn-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_basic_%s", suffix),
		}, mustTaggingVars(t)),
	})

	databaseArn := terraform.Output(t, ctx.Terraform, "database_arn")
	assert.Regexp(t, `^arn:aws:glue:`, databaseArn, "database_arn must match Glue ARN pattern ^arn:aws:glue:")
}

// TestBasicGlueDatabaseCount asserts exactly one aws_glue_catalog_database resource is created.
func TestBasicGlueDatabaseCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-glue-db-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_basic_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_database", 1)
}

// TestBasicGlueCatalogTableCountZero asserts no aws_glue_catalog_table is created when create_table defaults to false.
func TestBasicGlueCatalogTableCountZero(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-glue-table-count-zero-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_basic_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_table", 0)
}

// TestBasicGlueTableNameOutputNullWhenNoTable asserts table_name output is null when create_table is false.
// terraform.Output fatals on null outputs, so OutputAll is used to read the full map.
func TestBasicGlueTableNameOutputNullWhenNoTable(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-glue-table-name-null-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_basic_%s", suffix),
		}, mustTaggingVars(t)),
	})

	// When create_table is false, the table_name output is null in Terraform state.
	// terraform.Output calls "terraform output -json <key>" which exits non-zero for null outputs.
	// OutputAll calls "terraform output -json" (all outputs) which includes null values in the map.
	allOutputs := terraform.OutputAll(t, ctx.Terraform)
	tableNameVal, exists := allOutputs["table_name"]
	if exists {
		assert.Nil(t, tableNameVal, "table_name must be null when create_table is false")
	}
	// If the key is absent from the outputs map, the assertion is also satisfied.
}

// TestBasicGlueRequiredOutputsNotEmpty asserts all required outputs are present.
func TestBasicGlueRequiredOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-glue-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_basic_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "database_name"), "database_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "database_arn"), "database_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "catalog_id"), "catalog_id must not be empty")
}

// TestBasicGlueEmptyDatabaseNameValidationFails asserts plan fails when database_name is empty.
func TestBasicGlueEmptyDatabaseNameValidationFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-glue-empty-name-%s", suffix),
		ExtraVars: map[string]interface{}{
			"database_name": "",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when database_name is empty")
}

// TestBasicGlueCreateTableTrueWithNullTableFails asserts plan fails when create_table is true but table is null.
func TestBasicGlueCreateTableTrueWithNullTableFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("basic-glue-create-table-null-%s", suffix),
		ExtraVars: map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_fail_%s", suffix),
			"create_table":  true,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when create_table is true but table is null")
}
