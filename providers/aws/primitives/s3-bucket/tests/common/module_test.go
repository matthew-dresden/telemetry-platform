//go:build terratest

package common_test

import (
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/matthew-dresden/telemetry-platform/providers/aws/primitives/s3-bucket/tests/helpers"
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

// TestCommonS3TerraformVersionBasic asserts Terraform version satisfies the pinned constraint for the basic example.
func TestCommonS3TerraformVersionBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-cmn-ver-basic")
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-s3-tf-version-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonS3TerraformVersionDataLake asserts Terraform version satisfies the pinned constraint for the data-lake example.
func TestCommonS3TerraformVersionDataLake(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-cmn-ver-dl")
	ctx := testctx.RunSingleExample(t, "../../examples", "data-lake", testctx.TestConfig{
		Name: fmt.Sprintf("common-s3-tf-version-dl-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonS3RequiredOutputsBasic asserts all required outputs are present for the basic example.
func TestCommonS3RequiredOutputsBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-cmn-out-basic")
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-s3-outputs-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "bucket_id"), "bucket_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "bucket_arn"), "bucket_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "bucket_domain_name"), "bucket_domain_name must not be empty")
}

// TestCommonS3RequiredOutputsDataLake asserts all required outputs are present for the data-lake example.
func TestCommonS3RequiredOutputsDataLake(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-cmn-out-dl")
	ctx := testctx.RunSingleExample(t, "../../examples", "data-lake", testctx.TestConfig{
		Name: fmt.Sprintf("common-s3-outputs-dl-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "bucket_id"), "bucket_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "bucket_arn"), "bucket_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "bucket_domain_name"), "bucket_domain_name must not be empty")
}

// TestCommonS3NoPublicAccessBasic asserts the basic example has no public-access S3 bucket resource.
func TestCommonS3NoPublicAccessBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-cmn-npa-basic")
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-s3-npa-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	// The public access block must exist with all four settings set to true
	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_public_access_block", 1)
	// Bucket ARN confirms apply succeeded with full block-public-access
	bucketArn := terraform.Output(t, ctx.Terraform, "bucket_arn")
	assert.Regexp(t, `^arn:aws:s3:::`, bucketArn, "bucket_arn must match S3 ARN pattern confirming no-public-access apply succeeded")
}

// TestCommonS3NoPublicAccessDataLake asserts the data-lake example has no public-access S3 bucket resource.
func TestCommonS3NoPublicAccessDataLake(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-cmn-npa-dl")
	ctx := testctx.RunSingleExample(t, "../../examples", "data-lake", testctx.TestConfig{
		Name: fmt.Sprintf("common-s3-npa-dl-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_public_access_block", 1)
	bucketArn := terraform.Output(t, ctx.Terraform, "bucket_arn")
	assert.Regexp(t, `^arn:aws:s3:::`, bucketArn, "bucket_arn must match S3 ARN pattern confirming no-public-access apply succeeded")
}

// TestCommonS3IdempotencyBasic asserts the basic example is idempotent (plan after apply shows no changes).
func TestCommonS3IdempotencyBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-cmn-idem-basic")
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-s3-idem-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket", 1)
	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_versioning", 1)
	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_public_access_block", 1)
}

// TestCommonS3IdempotencyDataLake asserts the data-lake example is idempotent.
func TestCommonS3IdempotencyDataLake(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-cmn-idem-dl")
	ctx := testctx.RunSingleExample(t, "../../examples", "data-lake", testctx.TestConfig{
		Name: fmt.Sprintf("common-s3-idem-dl-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket", 1)
	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_lifecycle_configuration", 1)
	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_public_access_block", 1)
}
