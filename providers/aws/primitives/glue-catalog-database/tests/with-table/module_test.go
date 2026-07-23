//go:build terratest

package withtable_test

import (
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/assertions"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
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

// TestWithTableGlueCatalogTableCount asserts exactly one aws_glue_catalog_table is created when create_table is true.
func TestWithTableGlueCatalogTableCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-table", testctx.TestConfig{
		Name: fmt.Sprintf("with-table-glue-table-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_wt_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_table", 1)
}

// TestWithTableGlueTableNameNotEmpty asserts table_name output is non-empty when create_table is true.
func TestWithTableGlueTableNameNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-table", testctx.TestConfig{
		Name: fmt.Sprintf("with-table-glue-table-name-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_wt_%s", suffix),
		}, mustTaggingVars(t)),
	})

	tableName := terraform.Output(t, ctx.Terraform, "table_name")
	assert.NotEmpty(t, tableName, "table_name must not be empty when create_table is true")
}

// TestWithTableGlueToolPartitionKeyPresent asserts the Glue table was created with tool partition key.
func TestWithTableGlueToolPartitionKeyPresent(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-table", testctx.TestConfig{
		Name: fmt.Sprintf("with-table-glue-partition-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_wt_%s", suffix),
		}, mustTaggingVars(t)),
	})

	// Verify the table was created (which requires partition keys by the example configuration)
	assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_table", 1)
	tableName := terraform.Output(t, ctx.Terraform, "table_name")
	assert.NotEmpty(t, tableName, "table_name non-empty confirms with-table fixture created the partitioned table")
}

// TestWithTableGlueDatabaseCount asserts exactly one aws_glue_catalog_database is created.
func TestWithTableGlueDatabaseCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-table", testctx.TestConfig{
		Name: fmt.Sprintf("with-table-glue-db-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_wt_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_database", 1)
}

// TestWithTableGlueRequiredOutputsNotEmpty asserts all required outputs are present.
func TestWithTableGlueRequiredOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-table", testctx.TestConfig{
		Name: fmt.Sprintf("with-table-glue-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_wt_%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "database_name"), "database_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "database_arn"), "database_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "catalog_id"), "catalog_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "table_name"), "table_name must not be empty")
}

// TestWithTableGluePartitionProjectionParameters asserts the table-level Glue parameters
// (the Athena partition-projection configuration) are rendered onto the created table via
// the new table.parameters passthrough. Without parameters support the table has no
// projection and Athena cannot resolve partitions without a crawler.
func TestWithTableGluePartitionProjectionParameters(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-table", testctx.TestConfig{
		Name: fmt.Sprintf("with-table-glue-projection-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_wt_%s", suffix),
		}, mustTaggingVars(t)),
	})

	params := terraform.OutputMap(t, ctx.Terraform, "table_parameters")
	assert.Equal(t, "true", params["projection.enabled"],
		"projection.enabled must be true so Athena resolves partitions without a crawler")
	assert.Equal(t, "injected", params["projection.tool.type"],
		"tool must be an injected (unbounded) projection dimension")
	assert.Equal(t, "date", params["projection.dt.type"],
		"dt must be a date projection dimension")
	assert.Equal(t, "yyyy-MM-dd", params["projection.dt.format"],
		"projection.dt.format must match the Firehose timestamp partition format")
	assert.Contains(t, params["storage.location.template"], "tool=${tool}/dt=${dt}",
		"storage.location.template must map the projected placeholders onto the data prefix")
}

// TestWithTableGlueDuplicateColumnFails asserts the table validation rejects a table whose
// data columns and partition keys share a name. Glue stores partition keys as additional
// table columns, so an overlap yields a descriptor with duplicate columns that Athena
// rejects ("HIVE_INVALID_METADATA: Table descriptor contains duplicate columns"). The
// validation makes that unqueryable-table failure impossible at plan time.
func TestWithTableGlueDuplicateColumnFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("with-table-glue-dup-col-%s", suffix),
		ExtraVars: map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_wt_%s", suffix),
			"create_table":  true,
			// "tool" appears in BOTH columns and partition_keys -> must fail validation.
			"table": map[string]interface{}{
				"name":                        "telemetry_events",
				"location":                    "s3://telemetry-data-lake-example/raw/",
				"input_format":                "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
				"output_format":               "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
				"serde_serialization_library": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
				"columns": []map[string]interface{}{
					{"name": "timestamp", "type": "string", "comment": ""},
					{"name": "tool", "type": "string", "comment": ""},
				},
				"partition_keys": []map[string]interface{}{
					{"name": "tool", "type": "string"},
					{"name": "dt", "type": "string"},
				},
				"parameters": map[string]interface{}{},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	require.Error(t, err, "plan must fail when a data column name is also a partition key (duplicate columns)")
	assert.Contains(t, err.Error(), "disjoint",
		"the failure must be the columns/partition-keys disjointness validation, not an unrelated error")
}

// TestWithTableGlueInvalidLocationURIFails asserts plan fails when location_uri does not start with s3://.
func TestWithTableGlueInvalidLocationURIFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("with-table-glue-bad-uri-%s", suffix),
		ExtraVars: map[string]interface{}{
			"database_name": fmt.Sprintf("telemetry_wt_%s", suffix),
			"location_uri":  "http://not-s3",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when location_uri does not start with s3://")
}
