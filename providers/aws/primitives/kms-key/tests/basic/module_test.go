//go:build terratest

package basic_test

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

// TestBasicKMSKeyCreation tests that a KMS key and alias are created in the basic example.
func TestBasicKMSKeyCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-kms-key-creation-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	keyArn := terraform.Output(t, ctx.Terraform, "key_arn")
	assert.Regexp(t, `^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/`, keyArn, "key_arn must match KMS key ARN pattern")

	aliasName := terraform.Output(t, ctx.Terraform, "alias_name")
	assert.True(t, len(aliasName) > len("alias/") && aliasName[:6] == "alias/", "alias_name must start with alias/")
}

// TestBasicKMSKeyResourceCounts asserts exactly one aws_kms_key and one aws_kms_alias exist.
func TestBasicKMSKeyResourceCounts(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-kms-resource-counts-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-counts-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_kms_key", 1)
	assertions.AssertResourceCount(t, ctx, "aws_kms_alias", 1)
}

// TestBasicKMSKeyOutputsNotEmpty asserts all required outputs are non-empty.
func TestBasicKMSKeyOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-kms-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-outputs-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "key_id"), "key_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "key_arn"), "key_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "alias_name"), "alias_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "alias_arn"), "alias_arn must not be empty")
}

// TestBasicKMSKeyInvalidAlias tests that an invalid alias produces a validation error.
func TestBasicKMSKeyInvalidAlias(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-kms-invalid-alias-%s", suffix),
		ExtraVars: map[string]interface{}{
			"alias_name": "invalid alias with spaces!",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail for invalid alias_name")
}

// TestBasicKMSKeyOutOfRangeDeletionWindow tests that a deletion window outside 7-30 fails validation.
func TestBasicKMSKeyOutOfRangeDeletionWindow(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-kms-bad-window-%s", suffix),
		ExtraVars: map[string]interface{}{
			"alias_name":              fmt.Sprintf("test-window-%s", suffix),
			"deletion_window_in_days": 5,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when deletion_window_in_days < 7")
}

// TestBasicKMSKeyMalformedPolicyJSON tests that a malformed policy_json fails validation.
func TestBasicKMSKeyMalformedPolicyJSON(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-kms-bad-policy-%s", suffix),
		ExtraVars: map[string]interface{}{
			"alias_name":  fmt.Sprintf("test-policy-%s", suffix),
			"policy_json": "not-valid-json{{{",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail for malformed policy_json")
}
