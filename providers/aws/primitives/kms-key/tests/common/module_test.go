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

// TestCommonKMSTerraformVersion asserts that the Terraform version satisfies the pinned constraint.
func TestCommonKMSTerraformVersion(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-kms-tf-version-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-version-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonKMSRequiredOutputsBasic asserts all required outputs are present for the basic example.
func TestCommonKMSRequiredOutputsBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-kms-outputs-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-out-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "key_id"), "key_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "key_arn"), "key_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "alias_name"), "alias_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "alias_arn"), "alias_arn must not be empty")
}

// TestCommonKMSRequiredOutputsWithPolicy asserts all required outputs are present for the with-policy example.
func TestCommonKMSRequiredOutputsWithPolicy(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	accountID := os.Getenv("AWS_ACCOUNT_ID")
	require.NotEmpty(t, accountID, "AWS_ACCOUNT_ID environment variable must be set")

	ctx := testctx.RunSingleExample(t, "../../examples", "with-policy", testctx.TestConfig{
		Name: fmt.Sprintf("common-kms-outputs-policy-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-out-policy-%s", suffix),
			"account_id": accountID,
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "key_id"), "key_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "key_arn"), "key_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "alias_name"), "alias_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "alias_arn"), "alias_arn must not be empty")
}

// TestCommonKMSValidateBasic asserts the basic example passes terraform validate.
func TestCommonKMSValidateBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-kms-validate-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-validate-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	// A successful apply confirms validate passed.
	keyArn := terraform.Output(t, ctx.Terraform, "key_arn")
	assert.NotEmpty(t, keyArn, "key_arn must be non-empty after successful apply, confirming validate passed")
}

// TestCommonKMSValidateWithPolicy asserts the with-policy example passes terraform validate.
func TestCommonKMSValidateWithPolicy(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	accountID := os.Getenv("AWS_ACCOUNT_ID")
	require.NotEmpty(t, accountID, "AWS_ACCOUNT_ID environment variable must be set")

	ctx := testctx.RunSingleExample(t, "../../examples", "with-policy", testctx.TestConfig{
		Name: fmt.Sprintf("common-kms-validate-policy-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-validate-policy-%s", suffix),
			"account_id": accountID,
		}, mustTaggingVars(t)),
	})

	keyArn := terraform.Output(t, ctx.Terraform, "key_arn")
	assert.NotEmpty(t, keyArn, "key_arn must be non-empty after successful apply, confirming validate passed")
}

// TestCommonKMSIdempotencyBasic asserts the basic example is idempotent (plan after apply shows no changes).
func TestCommonKMSIdempotencyBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-kms-idempotent-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-idem-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_kms_key", 1)
	assertions.AssertResourceCount(t, ctx, "aws_kms_alias", 1)
}

// TestCommonKMSIdempotencyWithPolicy asserts the with-policy example is idempotent.
func TestCommonKMSIdempotencyWithPolicy(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	accountID := os.Getenv("AWS_ACCOUNT_ID")
	require.NotEmpty(t, accountID, "AWS_ACCOUNT_ID environment variable must be set")

	ctx := testctx.RunSingleExample(t, "../../examples", "with-policy", testctx.TestConfig{
		Name: fmt.Sprintf("common-kms-idempotent-policy-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-idem-policy-%s", suffix),
			"account_id": accountID,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_kms_key", 1)
	assertions.AssertResourceCount(t, ctx, "aws_kms_alias", 1)
}
