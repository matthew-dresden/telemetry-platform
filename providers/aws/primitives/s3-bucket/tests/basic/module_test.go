//go:build terratest

package basic_test

import (
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	"github.com/example-org/telemetry-platform/providers/aws/primitives/s3-bucket/tests/helpers"
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


// TestBasicS3BucketARNFormat asserts the bucket_arn output matches the S3 ARN pattern.
func TestBasicS3BucketARNFormat(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-basic-arn")
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-s3-arn-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	bucketArn := terraform.Output(t, ctx.Terraform, "bucket_arn")
	assert.Regexp(t, `^arn:aws:s3:::`, bucketArn, "bucket_arn must match S3 ARN pattern ^arn:aws:s3:::")
}

// TestBasicS3BucketPublicAccessBlock asserts the aws_s3_bucket_public_access_block resource count is 1.
func TestBasicS3BucketPublicAccessBlock(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-basic-pab")
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-s3-pab-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_public_access_block", 1)
}

// TestBasicS3BucketEncryptionPresent asserts the encryption configuration resource exists.
func TestBasicS3BucketEncryptionPresent(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-basic-enc")
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-s3-enc-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_server_side_encryption_configuration", 1)
}

// TestBasicS3BucketRequiredOutputsNotEmpty asserts all required outputs are non-empty.
func TestBasicS3BucketRequiredOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-basic-out")
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-s3-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "bucket_id"), "bucket_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "bucket_arn"), "bucket_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "bucket_domain_name"), "bucket_domain_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "bucket_regional_domain_name"), "bucket_regional_domain_name must not be empty")
}

// TestBasicS3BucketInvalidTransitionStorageClass asserts plan fails for an invalid transition_storage_class.
func TestBasicS3BucketInvalidTransitionStorageClass(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-basic-bad-class")
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-s3-invalid-class-%s", suffix),
		ExtraVars: map[string]interface{}{
			"bucket_name": bucketName,
			"lifecycle_rules": []map[string]interface{}{
				{
					"id":                       "test-rule",
					"enabled":                  true,
					"prefix":                   "",
					"transition_days":          365,
					"transition_storage_class": "STANDARD_IA",
					"expiration_days":          730,
				},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail for invalid transition_storage_class STANDARD_IA")
}

// TestBasicS3BucketSSEWithoutKMSKeyARN asserts plan fails when kms_key_arn does not match the ^arn:aws:kms: pattern.
// The example is now self-contained (it creates its own KMS key inline), so this test targets the module root
// directly to exercise the kms_key_arn validation contract without requiring an external key.
func TestBasicS3BucketSSEWithoutKMSKeyARN(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-basic-no-kms")
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("basic-s3-no-kms-%s", suffix),
		ExtraVars: map[string]interface{}{
			"bucket_name": bucketName,
			"kms_key_arn": "not-a-kms-arn",
			"block_public_access": map[string]interface{}{
				"block_public_acls":       true,
				"block_public_policy":     true,
				"ignore_public_acls":      true,
				"restrict_public_buckets": true,
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when kms_key_arn does not match ^arn:aws:kms:")
}

// TestBasicS3BucketExpirationBeforeTransition asserts plan fails when expiration_days <= transition_days.
func TestBasicS3BucketExpirationBeforeTransition(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-basic-bad-exp")
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-s3-bad-exp-%s", suffix),
		ExtraVars: map[string]interface{}{
			"bucket_name": bucketName,
			"lifecycle_rules": []map[string]interface{}{
				{
					"id":                       "test-rule",
					"enabled":                  true,
					"prefix":                   "",
					"transition_days":          730,
					"transition_storage_class": "GLACIER",
					"expiration_days":          365,
				},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when expiration_days <= transition_days")
}

// TestBasicS3BucketIncompleteBlockPublicAccess asserts plan fails when block_public_access has any setting set to false.
// D22 hardening requires all four settings to be true; the module validation rejects any incomplete configuration.
func TestBasicS3BucketIncompleteBlockPublicAccess(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-basic-bpa-fail")
	// Target the module root directly so the block_public_access object variable is exposed
	// (the basic example hardens it internally; targeting the module validates the contract directly).
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("basic-s3-bpa-fail-%s", suffix),
		ExtraVars: map[string]interface{}{
			"bucket_name": bucketName,
			"kms_key_arn": "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000",
			"block_public_access": map[string]interface{}{
				"block_public_acls":       false,
				"block_public_policy":     true,
				"ignore_public_acls":      true,
				"restrict_public_buckets": true,
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when block_public_acls is false (incomplete block-public-access configuration)")
}
