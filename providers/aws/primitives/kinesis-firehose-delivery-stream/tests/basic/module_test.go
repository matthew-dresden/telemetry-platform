//go:build terratest

package basic_test

import (
	"fmt"
	"testing"
	"time"

	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/stretchr/testify/assert"
	"os"
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

// TestBasicFirehoseDeliveryStreamARNFormat asserts the delivery_stream_arn output matches the Firehose ARN pattern.
func TestBasicFirehoseDeliveryStreamARNFormat(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-firehose-arn-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-bsc-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	streamArn := terraform.Output(t, ctx.Terraform, "delivery_stream_arn")
	assert.Regexp(t, `^arn:aws:firehose:`, streamArn, "delivery_stream_arn must match Firehose ARN pattern ^arn:aws:firehose:")
}

// TestBasicFirehoseDeliveryStreamCount asserts exactly one aws_kinesis_firehose_delivery_stream resource is created.
func TestBasicFirehoseDeliveryStreamCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-firehose-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-bsc-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_kinesis_firehose_delivery_stream", 1)
}

// TestBasicFirehoseRequiredOutputsNotEmpty asserts all required outputs are present.
func TestBasicFirehoseRequiredOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-firehose-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"stream_name": fmt.Sprintf("tst-bsc-fh-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "delivery_stream_arn"), "delivery_stream_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "delivery_stream_name"), "delivery_stream_name must not be empty")
}

// TestBasicFirehoseNonExtendedS3DestinationFails asserts plan fails when destination is not extended_s3.
func TestBasicFirehoseNonExtendedS3DestinationFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("basic-firehose-bad-dest-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":        fmt.Sprintf("tst-bsc-fh-%s", suffix),
			"destination": "http_endpoint",
			"bucket_arn":  "arn:aws:s3:::placeholder-bucket",
			"role_arn":    "arn:aws:iam::000000000000:role/placeholder-role",
			"kms_key_arn": "arn:aws:kms:us-east-1:000000000000:key/00000000-0000-0000-0000-000000000000",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when destination is not extended_s3")
}

// TestBasicFirehoseBufferingSizeOutOfRangeFails asserts plan fails when buffering_size exceeds the AWS allowed max.
func TestBasicFirehoseBufferingSizeOutOfRangeFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("basic-firehose-bad-size-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":           fmt.Sprintf("tst-bsc-fh-%s", suffix),
			"bucket_arn":     "arn:aws:s3:::placeholder-bucket",
			"role_arn":       "arn:aws:iam::000000000000:role/placeholder-role",
			"kms_key_arn":    "arn:aws:kms:us-east-1:000000000000:key/00000000-0000-0000-0000-000000000000",
			"buffering_size": 256,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when buffering_size exceeds the AWS allowed maximum of 128")
}

// TestBasicFirehoseBufferingIntervalOutOfRangeFails asserts plan fails when buffering_interval exceeds the AWS allowed max.
func TestBasicFirehoseBufferingIntervalOutOfRangeFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("basic-firehose-bad-interval-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":               fmt.Sprintf("tst-bsc-fh-%s", suffix),
			"bucket_arn":         "arn:aws:s3:::placeholder-bucket",
			"role_arn":           "arn:aws:iam::000000000000:role/placeholder-role",
			"kms_key_arn":        "arn:aws:kms:us-east-1:000000000000:key/00000000-0000-0000-0000-000000000000",
			"buffering_interval": 1200,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when buffering_interval exceeds the AWS allowed maximum of 900")
}

// TestBasicFirehoseInvalidKMSKeyARNFails asserts plan fails when kms_key_arn does not match the required pattern.
func TestBasicFirehoseInvalidKMSKeyARNFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("basic-firehose-bad-kms-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":        fmt.Sprintf("tst-bsc-fh-%s", suffix),
			"bucket_arn":  "arn:aws:s3:::placeholder-bucket",
			"role_arn":    "arn:aws:iam::000000000000:role/placeholder-role",
			"kms_key_arn": "not-a-kms-arn",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when kms_key_arn does not match ^arn:aws:kms:")
}
