//go:build terratest

package parquetpartitioned_test

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
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

// TestParquetPartitionedFirehoseStreamCreated asserts the delivery stream is created in the parquet-partitioned example.
func TestParquetPartitionedFirehoseStreamCreated(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "parquet-partitioned", testctx.TestConfig{
		Name: fmt.Sprintf("pp-firehose-created-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-pp-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_kinesis_firehose_delivery_stream", 1)
}

// TestParquetPartitionedFirehoseARNFormat asserts the delivery_stream_arn matches the Firehose ARN pattern.
func TestParquetPartitionedFirehoseARNFormat(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "parquet-partitioned", testctx.TestConfig{
		Name: fmt.Sprintf("pp-firehose-arn-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-pp-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	streamArn := terraform.Output(t, ctx.Terraform, "delivery_stream_arn")
	assert.Regexp(t, `^arn:aws:firehose:`, streamArn, "delivery_stream_arn must match Firehose ARN pattern ^arn:aws:firehose:")
}

// TestParquetPartitionedFirehoseRequiredOutputsNotEmpty asserts all required outputs are present.
func TestParquetPartitionedFirehoseRequiredOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "parquet-partitioned", testctx.TestConfig{
		Name: fmt.Sprintf("pp-firehose-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-pp-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "delivery_stream_arn"), "delivery_stream_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "delivery_stream_name"), "delivery_stream_name must not be empty")
}

// TestParquetPartitionedFirehoseEmptyJQValueFails asserts plan fails when a dynamic_partitioning_jq value is empty.
func TestParquetPartitionedFirehoseEmptyJQValueFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("pp-firehose-bad-jq-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":        fmt.Sprintf("tst-pp-fh-%s", suffix),
			"bucket_arn":  "arn:aws:s3:::placeholder-bucket",
			"role_arn":    "arn:aws:iam::000000000000:role/placeholder-role",
			"kms_key_arn": "arn:aws:kms:us-east-1:000000000000:key/00000000-0000-0000-0000-000000000000",
			"dynamic_partitioning_jq": map[string]interface{}{
				"tool": "",
				"date": "%Y/%m/%d",
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when a dynamic_partitioning_jq value is empty")
}

// TestParquetPartitionedFirehoseFormatConversionRequiresGlueWhenEnabled asserts plan fails when
// enable_format_conversion is true but data_format_conversion is null.
func TestParquetPartitionedFirehoseFormatConversionRequiresGlueWhenEnabled(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("pp-firehose-no-glue-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":                     fmt.Sprintf("tst-pp-fh-%s", suffix),
			"bucket_arn":               "arn:aws:s3:::placeholder-bucket",
			"role_arn":                 "arn:aws:iam::000000000000:role/placeholder-role",
			"kms_key_arn":              "arn:aws:kms:us-east-1:000000000000:key/00000000-0000-0000-0000-000000000000",
			"enable_format_conversion": true,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when enable_format_conversion is true but data_format_conversion is null")
}

// firehoseStateJSON is a partial representation of the JSON emitted by `terraform show -json`
// for parsing Firehose delivery stream processor attributes from Terraform state.
type firehoseStateJSON struct {
	Values struct {
		RootModule struct {
			ChildModules []struct {
				Address   string `json:"address"`
				Resources []struct {
					Address string                 `json:"address"`
					Type    string                 `json:"type"`
					Values  map[string]interface{} `json:"values"`
				} `json:"resources"`
			} `json:"child_modules"`
		} `json:"root_module"`
	} `json:"values"`
}

// TestParquetPartitionedFirehoseJQPartitionMetadataInState asserts that the
// MetadataExtractionQuery parameter values in the Firehose processor state
// correspond to the keys supplied via dynamic_partitioning_jq (AC-11).
// This proves partition keys are input-driven (T27) and not hardcoded.
func TestParquetPartitionedFirehoseJQPartitionMetadataInState(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	// Supply explicit dynamic_partitioning_jq so the test can assert exact keys/values.
	inputJQ := map[string]interface{}{
		"tool": ".tool",
		"date": "%Y/%m/%d",
	}
	ctx := testctx.RunSingleExample(t, "../../examples", "parquet-partitioned", testctx.TestConfig{
		Name: fmt.Sprintf("pp-firehose-jq-state-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name":             fmt.Sprintf("tst-pp-fh-%s", suffix),
			"dynamic_partitioning_jq": inputJQ,
		}, mustTaggingVars(t)),
	})

	// Confirm the stream exists before inspecting its state attributes.
	assertions.AssertResourceCount(t, ctx, "aws_kinesis_firehose_delivery_stream", 1)

	// Read Terraform state as JSON and parse processor attributes.
	stateJSON, err := terraform.ShowE(t, ctx.Terraform)
	require.NoError(t, err, "terraform show -json must succeed")

	var state firehoseStateJSON
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state), "state JSON must be parseable")

	// Locate the aws_kinesis_firehose_delivery_stream resource inside any child module.
	var streamValues map[string]interface{}
	for _, child := range state.Values.RootModule.ChildModules {
		for _, res := range child.Resources {
			if res.Type == "aws_kinesis_firehose_delivery_stream" {
				streamValues = res.Values
				break
			}
		}
		if streamValues != nil {
			break
		}
	}
	require.NotNil(t, streamValues, "aws_kinesis_firehose_delivery_stream must be present in Terraform state")

	// Navigate extended_s3_configuration -> processing_configuration -> processors.
	ext3Raw, ok := streamValues["extended_s3_configuration"]
	require.True(t, ok, "state must contain extended_s3_configuration")
	ext3List, ok := ext3Raw.([]interface{})
	require.True(t, ok, "extended_s3_configuration must be a list")
	require.NotEmpty(t, ext3List, "extended_s3_configuration must have at least one entry")
	ext3, ok := ext3List[0].(map[string]interface{})
	require.True(t, ok, "extended_s3_configuration entry must be a map")

	procConfigRaw, ok := ext3["processing_configuration"]
	require.True(t, ok, "extended_s3_configuration must contain processing_configuration")
	procConfigList, ok := procConfigRaw.([]interface{})
	require.True(t, ok, "processing_configuration must be a list")
	require.NotEmpty(t, procConfigList, "processing_configuration must have at least one entry")
	procConfig, ok := procConfigList[0].(map[string]interface{})
	require.True(t, ok, "processing_configuration entry must be a map")

	processorsRaw, ok := procConfig["processors"]
	require.True(t, ok, "processing_configuration must contain processors")
	processorsList, ok := processorsRaw.([]interface{})
	require.True(t, ok, "processors must be a list")
	require.NotEmpty(t, processorsList, "processors list must not be empty -- dynamic_partitioning_jq must produce at least one MetadataExtraction processor")

	// AWS enforces exactly one MetadataExtraction processor.  The module merges all
	// dynamic_partitioning_jq entries into a single combined MetadataExtractionQuery of
	// the form {key1:val1,key2:val2,...}.  Collect that single query value.
	var combinedQueryValue string
	metadataProcessorCount := 0
	for _, pRaw := range processorsList {
		proc, ok := pRaw.(map[string]interface{})
		require.True(t, ok, "each processor entry must be a map")
		procType, _ := proc["type"].(string)
		if procType != "MetadataExtraction" {
			continue
		}
		metadataProcessorCount++
		paramsRaw, ok := proc["parameters"]
		require.True(t, ok, "MetadataExtraction processor must have parameters")
		params, ok := paramsRaw.([]interface{})
		require.True(t, ok, "parameters must be a list")
		for _, paramRaw := range params {
			param, ok := paramRaw.(map[string]interface{})
			require.True(t, ok, "each parameter must be a map")
			if param["parameter_name"] == "MetadataExtractionQuery" {
				combinedQueryValue, _ = param["parameter_value"].(string)
			}
		}
	}

	// AWS enforces exactly one MetadataExtraction processor -- assert the combined processor exists.
	require.Equal(t, 1, metadataProcessorCount,
		"exactly one MetadataExtraction processor must exist in state (AWS enforces this limit); found %d",
		metadataProcessorCount)
	require.NotEmpty(t, combinedQueryValue,
		"MetadataExtraction processor must have a non-empty MetadataExtractionQuery parameter value")

	// Assert that each key from dynamic_partitioning_jq appears inside the combined query value
	// in the form "key:val" as produced by main.tf's combined-JQ template.
	// This proves partition keys are input-driven (T27) and not hardcoded.
	for key, val := range inputJQ {
		expectedFragment := fmt.Sprintf("%s:%s", key, val)
		assert.True(t, strings.Contains(combinedQueryValue, expectedFragment),
			"combined MetadataExtractionQuery %q must contain fragment %q (derived from dynamic_partitioning_jq key %q -- proving partition keys are input-driven via T27)",
			combinedQueryValue, expectedFragment, key)
	}
}

// processorsFromState walks the Firehose state JSON and returns the ordered list of
// processor entries from extended_s3_configuration[0].processing_configuration[0].processors.
func processorsFromState(t *testing.T, ctx testctx.TestContext) []map[string]interface{} {
	t.Helper()

	stateJSON, err := terraform.ShowE(t, ctx.Terraform)
	require.NoError(t, err, "terraform show -json must succeed")

	var state firehoseStateJSON
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state), "state JSON must be parseable")

	var streamValues map[string]interface{}
	for _, child := range state.Values.RootModule.ChildModules {
		for _, res := range child.Resources {
			if res.Type == "aws_kinesis_firehose_delivery_stream" {
				streamValues = res.Values
				break
			}
		}
		if streamValues != nil {
			break
		}
	}
	require.NotNil(t, streamValues, "aws_kinesis_firehose_delivery_stream must be present in Terraform state")

	ext3List, ok := streamValues["extended_s3_configuration"].([]interface{})
	require.True(t, ok, "extended_s3_configuration must be a list")
	require.NotEmpty(t, ext3List, "extended_s3_configuration must have at least one entry")
	ext3 := ext3List[0].(map[string]interface{})

	procConfigList, ok := ext3["processing_configuration"].([]interface{})
	require.True(t, ok, "processing_configuration must be a list")
	require.NotEmpty(t, procConfigList, "processing_configuration must have at least one entry")
	procConfig := procConfigList[0].(map[string]interface{})

	processorsRaw, ok := procConfig["processors"].([]interface{})
	require.True(t, ok, "processors must be a list")

	result := make([]map[string]interface{}, 0, len(processorsRaw))
	for _, p := range processorsRaw {
		result = append(result, p.(map[string]interface{}))
	}
	return result
}

// TestParquetPartitionedFirehoseDefaultSingleMetadataProcessor asserts that with
// transform_lambda_arn unset (the default null), the stream has exactly ONE processor
// (MetadataExtraction) -- i.e. no Lambda transform processor is prepended.
func TestParquetPartitionedFirehoseDefaultSingleMetadataProcessor(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "parquet-partitioned", testctx.TestConfig{
		Name: fmt.Sprintf("pp-firehose-default-proc-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-pp-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	procs := processorsFromState(t, ctx)
	require.Len(t, procs, 1, "default stream must have exactly one processor (MetadataExtraction)")
	assert.Equal(t, "MetadataExtraction", procs[0]["type"],
		"default stream's single processor must be MetadataExtraction")
}

// TestParquetPartitionedFirehoseTransformLambdaProcessors asserts that when
// transform_lambda_arn is set, the processing_configuration is, IN ORDER, a Lambda
// transform processor (LambdaArn = the supplied ARN) followed by the single
// MetadataExtraction processor. The Lambda transform is the
// record-splitting front end for a CloudWatch Logs subscription source: a single
// subscription record batches many logEvents, and the AWS-native unwrap path
// (Decompression -> CloudWatchLogProcessing -> RecordDeAggregation) cannot be used at
// scale because RecordDeAggregation is hard-capped at 500 sub-records per record (a
// delivery exceeding 500 events is passed WHOLE to the MetadataExtraction JQ engine,
// which rejects it with "Non JSON record provided" and routes every event to
// errors/metadata-extraction-failed/). The Lambda decompresses + envelope-strips and
// re-ingests each event as its own single-JSON record via firehose:PutRecordBatch (no
// 500 cap), so a delivery of any size is split before MetadataExtraction. This is a
// plan-time assertion: applying a stream whose Lambda processor references a function
// the delivery role cannot invoke is exercised end-to-end by the data-lake reference
// terratest, which owns the real re-ingestion Lambda.
func TestParquetPartitionedFirehoseTransformLambdaProcessors(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	// A well-formed (but unprovisioned) Lambda ARN: terraform plan renders the processor
	// wiring without invoking AWS to validate the function exists.
	dummyLambdaArn := "arn:aws:lambda:us-east-1:123456789012:function:tt-transform-plan-only"
	opts := testctx.InitTerraform("../../examples/parquet-partitioned", testctx.TestConfig{
		Name: fmt.Sprintf("pp-firehose-lambda-proc-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name":          fmt.Sprintf("tst-pp-fh-%s", suffix),
			"transform_lambda_arn": dummyLambdaArn,
		}, mustTaggingVars(t)),
	})

	procs := plannedFirehoseProcessors(t, opts)
	require.Len(t, procs, 2,
		"transform-Lambda stream must have two ordered processors: Lambda, MetadataExtraction")

	// Processor 0: Lambda with LambdaArn = the supplied ARN. Only LambdaArn is set; AWS
	// applies its default NumberOfRetries (3), which DescribeDeliveryStream does not echo back,
	// so setting it explicitly would make the stream perpetually non-idempotent.
	assert.Equal(t, "Lambda", procs[0]["type"], "first processor must be Lambda")
	assert.True(t, processorHasParameter(procs[0], "LambdaArn", dummyLambdaArn),
		"Lambda processor must set LambdaArn to the supplied transform_lambda_arn")

	// Processor 1: MetadataExtraction (dynamic partitioning runs on each split JSON record).
	assert.Equal(t, "MetadataExtraction", procs[1]["type"], "second processor must be MetadataExtraction")
}

// plannedFirehoseProcessors runs terraform plan and returns the ordered processor entries
// from the planned aws_kinesis_firehose_delivery_stream resource's
// extended_s3_configuration[0].processing_configuration[0].processors. Each entry's
// parameters are normalized to the {parameter_name, parameter_value} maps the
// processorHasParameter helper expects.
func plannedFirehoseProcessors(t *testing.T, opts *terraform.Options) []map[string]interface{} {
	t.Helper()

	// InitAndPlanAndShowWithStructE writes a binary plan then `terraform show -json`s it,
	// so it requires an explicit plan-file path on the options struct. Write it to a
	// test-scoped temp dir (auto-cleaned) so no plan artifact leaks into the example dir.
	opts.PlanFilePath = filepath.Join(t.TempDir(), "transform-lambda.tfplan")

	plan, err := terraform.InitAndPlanAndShowWithStructE(t, opts)
	require.NoError(t, err, "terraform init + plan must succeed")

	for _, res := range plan.ResourcePlannedValuesMap {
		if res.Type != "aws_kinesis_firehose_delivery_stream" {
			continue
		}
		ext3, ok := firstMapInList(res.AttributeValues["extended_s3_configuration"])
		require.True(t, ok, "planned stream must contain extended_s3_configuration")
		procConfig, ok := firstMapInList(ext3["processing_configuration"])
		require.True(t, ok, "planned stream must contain processing_configuration")
		processorsRaw, ok := procConfig["processors"].([]interface{})
		require.True(t, ok, "processing_configuration must contain a processors list")

		result := make([]map[string]interface{}, 0, len(processorsRaw))
		for _, p := range processorsRaw {
			result = append(result, p.(map[string]interface{}))
		}
		return result
	}

	t.Fatal("planned values must contain an aws_kinesis_firehose_delivery_stream resource")
	return nil
}

// firstMapInList returns the first element of a []interface{} as a map[string]interface{}.
func firstMapInList(v interface{}) (map[string]interface{}, bool) {
	list, ok := v.([]interface{})
	if !ok || len(list) == 0 {
		return nil, false
	}
	m, ok := list[0].(map[string]interface{})
	return m, ok
}

// processorHasParameter returns true when the processor's parameters list contains an
// entry with the given parameter_name and parameter_value.
func processorHasParameter(proc map[string]interface{}, name, value string) bool {
	paramsRaw, ok := proc["parameters"].([]interface{})
	if !ok {
		return false
	}
	for _, pRaw := range paramsRaw {
		p, ok := pRaw.(map[string]interface{})
		if !ok {
			continue
		}
		if p["parameter_name"] == name && p["parameter_value"] == value {
			return true
		}
	}
	return false
}
