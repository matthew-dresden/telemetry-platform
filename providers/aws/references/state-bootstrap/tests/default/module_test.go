//go:build terratest

package default_test

import (
	"fmt"
	"os"
	"regexp"
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

// TestStateBootstrap applies the default example once, runs all output and composition
// assertions as subtests sharing the applied state, and destroys via t.Cleanup when
// the parent test ends -- after all subtests complete. This ensures destroy does not
// fire between subtests.
func TestStateBootstrap(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	// Inject a per-run-unique bucket_prefix and kms_alias so each apply provisions
	// isolated resources. Without unique names the fixed defaults (bucket_prefix
	// "state-bootstrap-test", kms_alias "state-bootstrap/state") collide across runs
	// and leftover orphans, producing KMS AlreadyExistsException and S3
	// BucketAlreadyOwnedByYou at CreateAlias/CreateBucket. bucket_prefix is capped at
	// 28 chars by module validation, so a short "sb-<epoch>" form is used.
	extraVars := map[string]interface{}{
		"bucket_prefix": fmt.Sprintf("sb-%s", suffix),
		"kms_alias":     fmt.Sprintf("state-bootstrap/%s", suffix),
	}
	for k, v := range mustTaggingVars(t) {
		extraVars[k] = v
	}
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("state-bootstrap-default-%s", suffix),
		ExtraVars: extraVars,
	})

	// StateKmsKeyArnOutput asserts state_kms_key_arn is non-empty
	// and matches the KMS ARN pattern ^arn:aws:kms: (AC-17).
	t.Run("StateKmsKeyArnOutput", func(t *testing.T) {
		arn := terraform.Output(t, ctx.Terraform, "state_kms_key_arn")
		assert.NotEmpty(t, arn, "state_kms_key_arn must not be empty")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:kms:`),
			arn,
			"state_kms_key_arn must match ^arn:aws:kms:",
		)
	})

	// AccessLogBucketOutput asserts access_log_bucket is non-empty (AC-17).
	t.Run("AccessLogBucketOutput", func(t *testing.T) {
		bucket := terraform.Output(t, ctx.Terraform, "access_log_bucket")
		assert.NotEmpty(t, bucket, "access_log_bucket must not be empty")
	})

	// ArtifactBucketNameOutput asserts artifact_bucket_name is non-empty (AC-17).
	t.Run("ArtifactBucketNameOutput", func(t *testing.T) {
		bucketName := terraform.Output(t, ctx.Terraform, "artifact_bucket_name")
		assert.NotEmpty(t, bucketName, "artifact_bucket_name must not be empty")
	})

	// AllRequiredOutputsNotEmpty asserts all required outputs are non-empty (AC-17).
	// DynamoDB lock table output removed: S3-native locking (use_lockfile = true)
	// is the single locking mechanism (spec section 0.3 / D5, AC-14, E8-F6-S1-T1).
	t.Run("AllRequiredOutputsNotEmpty", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "state_kms_key_arn"), "state_kms_key_arn must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "access_log_bucket"), "access_log_bucket must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "artifact_bucket_name"), "artifact_bucket_name must not be empty")
	})

	// KmsKeyComposed asserts the composed kms-key primitive produces
	// an aws_kms_key resource in state, confirming the key ARN is wired internally (AC-3).
	t.Run("KmsKeyComposed", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_kms_key", 1)
	})

	// S3BucketsComposed asserts two aws_s3_bucket resources exist:
	// one access-log bucket and one artifact bucket (AC-3).
	// No DynamoDB table is expected: S3-native locking (use_lockfile = true) is the
	// single locking mechanism (spec section 0.3 / D5, AC-14, E8-F6-S1-T1).
	t.Run("S3BucketsComposed", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_s3_bucket", 2)
	})

	// TerraformVersion asserts the pinned Terraform version constraint is satisfied.
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// Idempotency asserts a plan after apply exits with code 0 (no changes).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan must show zero resource changes (exit code 0 means no changes)")
	})
}

// TestStateBootstrapInvalidBucketPrefix asserts that an invalid bucket_prefix
// causes a validation error (error path coverage). This test runs independently
// of the shared apply to avoid creating real AWS resources.
func TestStateBootstrapInvalidBucketPrefix(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("state-bootstrap-invalid-prefix-%s", suffix),
		ExtraVars: map[string]interface{}{
			"bucket_prefix": "AB",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when bucket_prefix fails validation")
}
